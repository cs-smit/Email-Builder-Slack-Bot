"""Screenshots of the built email (desktop at the design width, and a phone), plus placeholder
pictures so missing images show as labelled boxes instead of broken-image icons.

Uses the Google Chrome already installed on this Mac (through Playwright).
"""
from __future__ import annotations

import re
from pathlib import Path

import sys

from PIL import Image, ImageDraw, ImageFont

from . import config

from .figma import _slice_reference

PHONE = {"width": 390, "height": 844}

OVERFLOW_JS = """() => {
  const vw = window.innerWidth, out = [];
  for (const el of document.querySelectorAll('table, td, div, img, p, a, h1, h2, h3, span')) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.right > vw + 1) {
      const txt = (el.innerText || el.getAttribute('alt') || el.getAttribute('src') || '').trim().slice(0, 50);
      out.push(`<${el.tagName.toLowerCase()} class="${el.className}"> is ${Math.round(r.width)}px wide, `
             + `ends at ${Math.round(r.right)}px (screen ${vw}px): "${txt}"`);
    }
  }
  return {scrollWidth: document.documentElement.scrollWidth, viewport: vw, wide: out.slice(0, 15)};
}"""


def _attr(tag: str, name: str) -> int | None:
    m = re.search(rf'\b{name}\s*=\s*["\']?(\d+)', tag, re.I) or re.search(
        rf"{name}\s*:\s*(\d+)px", tag, re.I
    )
    return int(m.group(1)) if m else None


def make_placeholders(job_dir: Path) -> list[str]:
    """Create a labelled grey box for every images/MISSING-* the email uses (img or background)."""
    html = (job_dir / "email.html").read_text(errors="ignore")
    made = []
    for tag in re.findall(r"<[a-z][^>]*>", html, re.I | re.S):
        for name in re.findall(r"""images/(MISSING-[A-Za-z0-9._-]+\.(?:png|jpg))""", tag):
            if name in made:
                continue
            size = re.search(r'data-placeholder\s*=\s*["\']?(\d+)x(\d+)', tag)
            if size:
                w, h = int(size.group(1)), int(size.group(2))
            else:
                w = _attr(tag, "width") or _attr(tag, "max-width") or 600
                h = _attr(tag, "height") or round(w * 0.6)
            w, h = max(w, 40), max(h, 24)
            scale = 2
            img = Image.new("RGB", (w * scale, h * scale), "#e3e7ee")
            d = ImageDraw.Draw(img)
            d.rectangle([0, 0, w * scale - 1, h * scale - 1], outline="#9aa5b8", width=4)
            label = name.removeprefix("MISSING-").rsplit(".", 1)[0].replace("-", " ")
            try:
                font = ImageFont.load_default(size=max(12, min(36, w * scale // 14, h * scale // 5)))
            except TypeError:
                font = ImageFont.load_default()
            text = f"Image needed:\n{label}\n{w}x{h}"
            box = d.multiline_textbbox((0, 0), text, font=font, align="center")
            d.multiline_text(
                ((w * scale - (box[2] - box[0])) / 2, (h * scale - (box[3] - box[1])) / 2),
                text, fill="#5b6475", font=font, align="center",
            )
            (job_dir / "images").mkdir(exist_ok=True)
            img.save(job_dir / "images" / name)
            made.append(name)
    return made


def _launch(p):
    """Installed Google Chrome on a Mac; Playwright's own Chromium on Linux servers."""
    channel = config.BROWSER_CHANNEL
    if channel:
        try:
            return p.chromium.launch(channel=channel)
        except Exception:
            pass
    return p.chromium.launch(args=["--no-sandbox"] if sys.platform.startswith("linux") else [])


def screenshots(job_dir: Path, design_width: int) -> dict:
    """render-desktop-*.png at the design width, render-phone-*.png on a phone, and what
    sticks out past the phone screen."""
    from playwright.sync_api import sync_playwright

    for old in job_dir.glob("render-*.png"):
        old.unlink()
    url = (job_dir / "email.html").resolve().as_uri()
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": design_width, "height": 1000})
            page.goto(url, wait_until="networkidle", timeout=60000)
            page.screenshot(path=str(job_dir / "render-desktop.png"), full_page=True)

            phone = browser.new_context(viewport=PHONE, is_mobile=True, has_touch=True, device_scale_factor=1)
            mp = phone.new_page()
            mp.goto(url, wait_until="networkidle", timeout=60000)
            mp.screenshot(path=str(job_dir / "render-phone.png"), full_page=True)
            overflow = mp.evaluate(OVERFLOW_JS)
        finally:
            browser.close()

    desktop = _slice_reference(job_dir / "render-desktop.png", job_dir, prefix="render-desktop")
    phone_slices = _slice_reference(job_dir / "render-phone.png", job_dir, prefix="render-phone")
    with Image.open(job_dir / "render-desktop.png") as im:
        desktop_size = im.size
    return {"desktop": desktop, "phone": phone_slices, "overflow": overflow, "desktop_size": desktop_size}


def apply_crops(job_dir: Path) -> tuple[list[str], list[str]]:
    """Cut the pictures listed in crops.json out of the full-resolution design.

    crops.json: [{"file": "logo-miami-herald.png", "x": 120, "y": 2110, "w": 148, "h": 22,
                  "trim": true}]  (x/y/w/h in reference-picture pixels)
    Returns (files made, problems).
    """
    import json

    from PIL import ImageChops

    spec_path, full_path = job_dir / "crops.json", job_dir / "design-full.png"
    if not spec_path.exists() or not full_path.exists():
        return [], []
    try:
        specs = json.loads(spec_path.read_text())
    except json.JSONDecodeError as e:
        return [], [f"crops.json isn't valid JSON: {e}"]
    scale = json.loads((job_dir / "design.json").read_text()).get("scale", 1) if (job_dir / "design.json").exists() else 1
    made, problems = [], []
    with Image.open(full_path) as full:
        full = full.convert("RGBA")
        for c in specs if isinstance(specs, list) else []:
            try:
                name = Path(str(c["file"])).name
                if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*\.(png|jpg)", name):
                    problems.append(f"bad file name {name!r}")
                    continue
                box = [round(float(c[k]) * scale) for k in ("x", "y", "w", "h")]
                x, y, w, h = box
                x, y = max(0, x), max(0, y)
                w, h = min(w, full.width - x), min(h, full.height - y)
                if w < 4 or h < 4:
                    problems.append(f"{name}: crop box is outside the design")
                    continue
                piece = full.crop((x, y, x + w, y + h))
                if c.get("trim"):
                    # Drop the flat background around a logo/icon, keeping a 2px margin.
                    bg = Image.new("RGBA", piece.size, piece.getpixel((0, 0)))
                    diff = ImageChops.difference(piece, bg).convert("L").point(lambda v: 255 if v > 18 else 0)
                    bbox = diff.getbbox()
                    if bbox:
                        m = 2 * scale
                        piece = piece.crop((max(0, bbox[0] - m), max(0, bbox[1] - m),
                                            min(piece.width, bbox[2] + m), min(piece.height, bbox[3] + m)))
                (job_dir / "images").mkdir(exist_ok=True)
                out = job_dir / "images" / name
                if name.endswith(".jpg"):
                    piece.convert("RGB").save(out, quality=88, optimize=True)
                else:
                    piece.save(out, optimize=True)
                made.append(name)
            except (KeyError, TypeError, ValueError) as e:
                problems.append(f"bad crop entry {c!r}: {e}")
    return made, problems
