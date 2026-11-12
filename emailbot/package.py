"""Check the built email, zip it, and swap local image paths for S3 URLs."""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

LOCAL_REF = re.compile(r"""(?P<pre>(?:src|background)\s*=\s*["']|url\(\s*["']?)(?P<path>(?:\./)?images/(?P<file>[^"')\s>]+))""", re.I)
MERGE_TAG = re.compile(r"\{\{\{?\s*[\w.\- ]+\s*\}?\}\}")
GMAIL_CLIP_KB = 102


@dataclass
class Report:
    used: list[str] = field(default_factory=list)  # images/ files the HTML uses
    missing: list[str] = field(default_factory=list)  # used but not in images/
    unused: list[str] = field(default_factory=list)  # exported but not used
    lost_tags: list[str] = field(default_factory=list)  # merge tags the base had and the email lost
    text_images: list[str] = field(default_factory=list)  # big pictures with text baked in
    size_kb: float = 0.0

    def lines(self) -> list[str]:
        out = []
        if self.missing:
            real = [f for f in self.missing if not f.startswith("MISSING-")]
            if real:
                out.append(f":warning: HTML points to images that don't exist: {', '.join(real)}")
        if self.lost_tags:
            out.append(f":warning: Merge tags from the base template are missing: {', '.join(self.lost_tags)}")
        placeholders = [f for f in self.used if f.startswith("MISSING-")]
        if placeholders:
            out.append(":frame_with_picture: Still needed from the design (export these and attach them in this "
                       f"thread): {', '.join(placeholders)}")
        if self.text_images:
            out.append(":warning: These pictures contain text, so those parts aren't live HTML: "
                       f"{', '.join(self.text_images)}. Ask me to rebuild them as HTML if that's wrong.")
        if self.size_kb > GMAIL_CLIP_KB:
            out.append(f":warning: HTML is {self.size_kb:.0f} KB; Gmail clips emails over {GMAIL_CLIP_KB} KB.")
        return out


def local_images(html: str) -> list[str]:
    seen = []
    for m in LOCAL_REF.finditer(html):
        f = m.group("file")
        if f not in seen:
            seen.append(f)
    return seen


def check(job_dir: Path, base_file: Path | None) -> Report:
    html = (job_dir / "email.html").read_text(errors="ignore")
    used = local_images(html)
    exported = sorted(p.name for p in (job_dir / "images").glob("*") if p.is_file())
    r = Report(
        used=used,
        missing=[f for f in used if not (job_dir / "images" / f).exists()],
        unused=[f for f in exported if f not in used],
        size_kb=len(html.encode()) / 1024,
    )
    # Flag big pictures with text in them: from Figma we know which ones; for uploads, very tall files.
    meta = {}
    if (job_dir / "assets.json").exists():
        import json

        meta = {a["file"]: a for a in json.loads((job_dir / "assets.json").read_text())}
    for f in used:
        p = job_dir / "images" / f
        if not p.exists():
            continue
        a = meta.get(f)
        if a:
            if a.get("has_text") and a.get("height", 0) > 300:
                r.text_images.append(f)
        else:
            try:
                from PIL import Image

                with Image.open(p) as im:
                    if im.height > 1800:  # taller than ~900px in the email: likely a whole section
                        r.text_images.append(f)
            except Exception:
                pass
    if base_file and base_file.exists():
        base_tags = {t.replace(" ", "") for t in MERGE_TAG.findall(base_file.read_text(errors="ignore"))}
        have = {t.replace(" ", "") for t in MERGE_TAG.findall(html)}
        r.lost_tags = sorted(base_tags - have)
    return r


def package_zip(job_dir: Path, slug: str) -> Path:
    """<slug>.zip: <slug>.html with every images/ file it uses, opens correctly as-is."""
    html = (job_dir / "email.html").read_text(errors="ignore")
    from .library import clean

    files = [f for f in local_images(html) if (job_dir / "images" / f).exists()]
    out = job_dir / f"{slug}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{slug}/{slug}.html", clean(html))
        for f in files:
            z.write(job_dir / "images" / f, f"{slug}/images/{f}")
    return out


def images_zip(job_dir: Path, slug: str) -> Path | None:
    """Zip of just the images the email uses, ready to upload to S3."""
    html = (job_dir / "email.html").read_text(errors="ignore")
    files = [f for f in local_images(html) if (job_dir / "images" / f).exists()]
    if not files:
        return None
    out = job_dir / f"{slug}-images.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(job_dir / "images" / f, f"{slug}-images/{f}")
    return out


def normalise_base_url(url: str) -> str:
    """Accept the S3 folder URL or the URL of any one uploaded image."""
    url = url.strip("<> ").split("|")[0]
    path = urlparse(url).path
    if re.search(r"\.(png|jpe?g|gif|webp|svg)$", path, re.I):
        url = url.rsplit("/", 1)[0]
    return url.rstrip("/") + "/"


def with_hosted_images(html: str, base_url: str) -> str:
    def sub(m):
        return m.group("pre") + base_url + quote(m.group("file"))

    return LOCAL_REF.sub(sub, html)


def check_urls(urls: list[str]) -> list[str]:
    """Return the URLs that don't load (wrong folder, not uploaded, not public)."""
    bad = []
    with httpx.Client(timeout=20, follow_redirects=True) as c:
        for u in urls:
            try:
                r = c.head(u)
                if r.status_code >= 400:  # some hosts refuse HEAD; confirm with GET
                    r = c.get(u)
                if r.status_code >= 400:
                    bad.append(f"{u} ({r.status_code})")
            except httpx.HTTPError as e:
                bad.append(f"{u} ({type(e).__name__})")
    return bad


def final_zip(job_dir: Path, slug: str, base_url: str) -> tuple[Path, list[str]]:
    """<slug>.zip with <slug>.html (S3 image URLs) and the images folder. Returns (zip, broken URLs)."""
    html = (job_dir / "email.html").read_text(errors="ignore")
    files = [f for f in local_images(html) if (job_dir / "images" / f).exists()]
    from .library import clean

    hosted = clean(with_hosted_images(html, base_url))
    (job_dir / f"{slug}.html").write_text(hosted)
    broken = check_urls([base_url + quote(f) for f in files])

    out = job_dir / f"{slug}-client.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{slug}/{slug}.html", hosted)
        for f in files:
            z.write(job_dir / "images" / f, f"{slug}/images/{f}")
    return out, broken
