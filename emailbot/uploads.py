"""Build from files exported out of Figma by hand (no Figma API needed).

Upload in Slack: the whole email exported as one PNG/JPG (the design), plus the images it uses
(photos, icons, logos), either as separate files or in one .zip. The largest picture is taken
as the design; everything else is an image the email may use.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

from PIL import Image

from . import figma

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_FILE_BYTES = 50 * 1024 * 1024


def _slug_file(name: str) -> str:
    stem, ext = Path(name).stem, Path(name).suffix.lower()
    ext = ".jpg" if ext == ".jpeg" else ext
    return (re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:50] or "image") + ext


def expand(files: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """Unpack zips; keep only images. Returns (original name, bytes)."""
    out = []
    for name, data in files:
        ext = Path(name).suffix.lower()
        if ext == ".zip":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    base = Path(info.filename).name
                    if (
                        info.is_dir()
                        or base.startswith(".")
                        or "__MACOSX" in info.filename
                        or Path(base).suffix.lower() not in IMAGE_EXT
                        or info.file_size > MAX_FILE_BYTES
                    ):
                        continue
                    out.append((base, z.read(info)))
        elif ext in IMAGE_EXT and len(data) <= MAX_FILE_BYTES:
            out.append((Path(name).name, data))
    return out


def _size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as im:
        return im.size


def save_images(files: list[tuple[str, bytes]], img_dir: Path) -> list[dict]:
    """Save uploaded images under clean, unique names. Returns [{file, original, w, h}]."""
    img_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for name, data in files:
        fname = _slug_file(name)
        stem, ext = Path(fname).stem, Path(fname).suffix
        i = 2
        while (img_dir / fname).exists():
            fname, i = f"{stem}-{i}{ext}", i + 1
        (img_dir / fname).write_bytes(data)
        w, h = _size(data)
        saved.append({"file": fname, "original": name, "w": w, "h": h})
    return saved


def split_designs(files: list[tuple[str, bytes]]) -> tuple[list[tuple[str, bytes]], list[tuple[str, bytes]]]:
    """Separate whole-email designs from the images they use.

    An email design is a long picture at least ~500px wide (height >= 2 x width). With none of
    those, the biggest picture is the design. Returns (designs, other images), designs in upload order.
    """
    files = expand(files)
    designs, assets = [], []
    for name, data in files:
        try:
            w, h = _size(data)
        except Exception:
            continue
        # Whole emails are long: 3-4x taller than wide. Tall photos (portraits) are ~1.5x.
        (designs if w >= 500 and h >= 2 * w else assets).append((name, data))
    if not designs and files:
        biggest = max(files, key=lambda f: _area(f[1]))
        designs = [biggest]
        assets = [f for f in files if f is not biggest]
    return designs, assets


def _area(data: bytes) -> int:
    try:
        w, h = _size(data)
        return w * h
    except Exception:
        return 0


def prepare(files: list[tuple[str, bytes]], out_dir: Path, design_name: str | None = None) -> dict:
    """Write reference slices, images/ and a first design.md (no text yet) into out_dir."""
    files = expand(files)
    if not files:
        raise ValueError("No images found. Attach the email exported as PNG/JPG (and its images), or a .zip of them.")
    sized = []
    for name, data in files:
        try:
            sized.append((name, data, *_size(data)))
        except Exception:
            continue
    if not sized:
        raise ValueError("Couldn't open any of the attached images.")

    # The design is the biggest picture; everything else is an asset.
    # The design is the one named, else the biggest picture; everything else is an asset.
    sized.sort(key=lambda x: x[2] * x[3], reverse=True)
    pick = next((i for i, x in enumerate(sized) if design_name and x[0] == design_name), 0)
    design_name, design_data, dw, dh = sized[pick]
    assets = [(n, d) for i, (n, d, _, _) in enumerate(sized) if i != pick]

    # Exports are usually 1x (≈600-800px wide) or 2x (≈1200-1600px). Work out the scale.
    scale = 2 if dw >= 1100 else 1
    width = round(dw / scale)

    out_dir.mkdir(parents=True, exist_ok=True)
    full = Image.open(io.BytesIO(design_data)).convert("RGB")
    full.save(out_dir / "design-full.png")
    (out_dir / "design.json").write_text(json.dumps({"scale": scale}))
    ref = full
    if scale != 1:
        ref = ref.resize((width, round(dh / scale)), Image.LANCZOS)
    ref.save(out_dir / "reference.png")
    slices = figma._slice_reference(out_dir / "reference.png", out_dir)

    saved = save_images(assets, out_dir / "images")
    lines = [
        f"# Design uploaded by hand: {design_name}",
        f"No Figma data for this email: read the text, colours, sizes and spacing from the reference "
        f"pictures. The design is {width}px wide (uploaded at {scale}x).",
        f"Reference pictures (top to bottom): {figma.slice_note(slices)}",
        "",
        "## Images uploaded with the design (files in images/)",
    ]
    if saved:
        for a in saved:
            lines.append(
                f"- images/{a['file']}: {a['w']}x{a['h']}px file (about {round(a['w'] / scale)}px wide in the "
                f"design if exported at {scale}x; check against the reference), uploaded as \"{a['original']}\""
            )
    else:
        lines.append("- none: cut the pictures out of the design (see crops.json in the rules).")
    (out_dir / "design.md").write_text("\n".join(lines))
    return {
        "name": Path(design_name).stem,
        "width": width,
        "scale": scale,
        "slices": slices,
        "assets": saved,
    }
