"""The existing template library: copy it locally, and find the closest templates to a design.

Two sources (see docs/template-library.md):
- LIBRARY_DIR: a folder of .html files.
- LIBRARY_URL: a website publishing a manifest (JSON, or JS wrapping a JSON object) with
  {"templates": [{"file": "welcome.html", "title": "...", "headline": "...", "modified": "..."}]}.
  Each template is downloaded once to data/library/ and re-fetched when `modified` changes.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from . import config

MANIFEST_CACHE = config.CACHE_DIR / "manifest.json"
FILES_DIR = config.CACHE_DIR / "files"

STOPWORDS = set(
    """a an and are as at be by for from has have i in is it its of on or our that the this to
    was we were will with you your yours can more not all any how what when who why do does
    just get now here there their they them us me my no yes if so than then too very also
    view online unsubscribe email preferences preference center click browser""".split()
)


NETLIFY_COMMENT = re.compile(r"\s*<!--(?:(?!-->).)*?netlify(?:(?!-->).)*-->", re.S | re.I)


def clean(html: str) -> str:
    """Drop the promotional comment Netlify injects into every page it serves."""
    return NETLIFY_COMMENT.sub("", html)


@dataclass
class Template:
    file: str
    title: str
    folder: str
    headline: str
    modified: str
    bulk: bool

    @property
    def path(self) -> Path:
        return FILES_DIR / self.file

    @property
    def url(self) -> str:
        return config.LIBRARY_URL + quote(self.file, safe="/+") if config.LIBRARY_URL else self.file


def _parse_manifest(text: str) -> dict:
    """Plain JSON ({"templates": [...]} or a bare list), or JS that assigns such an object."""
    text = text.strip()
    if text.startswith("["):
        return {"templates": json.loads(text)}
    start, end = text.index("{"), text.rindex("}")
    data = json.loads(text[start : end + 1])
    return data if "templates" in data else {"templates": data.get("items", [])}


def sync(force: bool = False, log=print) -> list[Template]:
    """Refresh the local copy of the library. Returns all templates."""
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    if config.LIBRARY_DIR:
        manifest = _scan_dir(Path(config.LIBRARY_DIR).expanduser())
    elif config.LIBRARY_URL:
        manifest = _sync_url(force, log)
    else:
        raise RuntimeError("Set LIBRARY_DIR or LIBRARY_URL in .env to point at your existing email templates.")
    MANIFEST_CACHE.write_text(json.dumps(manifest, indent=1))
    return load()


def _scan_dir(root: Path) -> dict:
    """Build a manifest from a folder of .html files and copy them into the cache."""
    if not root.is_dir():
        raise RuntimeError(f"LIBRARY_DIR {root} is not a folder.")
    templates = []
    for f in sorted(root.rglob("*.htm*")):
        if any(part.startswith(".") for part in f.relative_to(root).parts):
            continue
        rel = f.relative_to(root).as_posix()
        html = clean(f.read_text(errors="ignore"))
        dest = FILES_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(html)
        soup = BeautifulSoup(html, "html.parser")
        title = (soup.title.get_text(strip=True) if soup.title else "") or f.stem.replace("-", " ").title()
        heading = soup.find(["h1", "h2"])
        templates.append({
            "file": rel,
            "title": title,
            "folder": Path(rel).parent.as_posix() if "/" in rel else "",
            "headline": heading.get_text(" ", strip=True)[:120] if heading else "",
            "modified": str(int(f.stat().st_mtime)),
        })
    if not templates:
        raise RuntimeError(f"No .html files found in LIBRARY_DIR {root}.")
    return {"templates": templates}


def _sync_url(force: bool, log) -> dict:
    old = {}
    if MANIFEST_CACHE.exists() and not force:
        old = {t["file"]: t for t in json.loads(MANIFEST_CACHE.read_text())["templates"]}

    with httpx.Client(timeout=30, follow_redirects=True) as client:
        r = client.get(config.LIBRARY_URL + config.LIBRARY_MANIFEST)
        r.raise_for_status()
        manifest = _parse_manifest(r.text)
        todo = [
            t
            for t in manifest["templates"]
            if force
            or not (FILES_DIR / t["file"]).exists()
            or old.get(t["file"], {}).get("modified") != t.get("modified")
        ]
        if todo:
            log(f"Downloading {len(todo)} of {len(manifest['templates'])} templates…")

        def fetch(t):
            dest = FILES_DIR / t["file"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            resp = client.get(config.LIBRARY_URL + quote(t["file"], safe="/+"))
            if resp.status_code == 200:
                dest.write_text(clean(resp.text))
            return t["file"], resp.status_code

        with ThreadPoolExecutor(max_workers=8) as pool:
            failed = [f for f, code in pool.map(fetch, todo) if code != 200]
        if failed:
            log(f"Could not download {len(failed)} templates: {', '.join(failed[:5])}")
    return manifest


def load() -> list[Template]:
    if not MANIFEST_CACHE.exists():
        return sync()
    data = json.loads(MANIFEST_CACHE.read_text())
    out = []
    for t in data["templates"]:
        tpl = Template(
            file=t["file"],
            title=t.get("title") or t["file"],
            folder=t.get("folder") or "",
            headline=t.get("headline") or "",
            modified=t.get("modified") or "",
            bulk=bool(t.get("bulk")),
        )
        if tpl.path.exists():
            out.append(tpl)
    return out


# ---------------------------------------------------------------- matching

def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z][a-z0-9']+", text.lower())
    return [w.strip("'") for w in words if len(w) > 2 and w not in STOPWORDS]


def visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["style", "script", "head"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def _structure(html: str) -> Counter:
    """Rough layout fingerprint: how many images, buttons, columns, etc."""
    h = html.lower()
    return Counter(
        img=h.count("<img"),
        button=len(re.findall(r"border-radius:\s*\d+px[^\"]*\"[^>]*>\s*<a|class=\"[^\"]*btn", h)),
        column=len(re.findall(r'width="(?:50|33|25)%"|width:\s*(?:50|33|25)%', h)),
        table=h.count("<table"),
    )


def find_matches(query_text: str, hint: str = "", top: int = 5, templates=None) -> list[tuple[Template, float]]:
    """Rank templates by how close their wording is to the design (TF-IDF cosine).

    `hint` is the user's note; a template whose title or file name it mentions jumps to the top.
    """
    templates = templates if templates is not None else load()
    docs = []
    for t in templates:
        text = visible_text(t.path.read_text(errors="ignore"))
        docs.append(Counter(tokens(f"{t.title} {t.headline} {text}")))

    n = len(docs)
    df = Counter()
    for d in docs:
        df.update(d.keys())
    idf = {w: math.log((n + 1) / (c + 1)) + 1 for w, c in df.items()}

    def vec(c: Counter) -> dict[str, float]:
        v = {w: (1 + math.log(k)) * idf.get(w, math.log(n + 1) + 1) for w, k in c.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / norm for w, x in v.items()}

    q = vec(Counter(tokens(query_text)))
    hint_l = hint.lower()
    scored = []
    for t, d in zip(templates, docs):
        dv = vec(d)
        score = sum(q[w] * dv.get(w, 0.0) for w in q)
        name = Path(t.file).stem.replace("-", " ").replace("_", " ").lower()
        if hint_l:
            # The user named it: the longest name found in their note wins.
            named = [x for x in (t.file.lower(), name, t.title.lower()) if len(x) > 3 and x in hint_l]
            if named:
                score += 1.0 + 0.1 * max(len(x.split()) for x in named)
        scored.append((t, score))

    scored.sort(key=lambda x: (x[1], x[0].modified), reverse=True)
    # Same email often exists in two folders; keep the first (newest) copy of identical files.
    seen, out = set(), []
    for t, s in scored:
        key = t.path.read_bytes().__hash__()
        if key in seen:
            continue
        seen.add(key)
        out.append((t, s))
        if len(out) == top:
            break
    return out
