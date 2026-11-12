"""Read a Figma frame: its layout outline, a reference picture, and the images it needs.

Needs FIGMA_TOKEN (Figma → Settings → Security → Personal access tokens, scope
"File content: read").
"""
from __future__ import annotations

import io
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from PIL import Image

from . import config

API = "https://api.figma.com/v1"

GRAPHIC_TYPES = {"VECTOR", "BOOLEAN_OPERATION", "STAR", "POLYGON", "REGULAR_POLYGON", "ELLIPSE"}
CONTAINER_TYPES = {"FRAME", "GROUP", "INSTANCE", "COMPONENT", "COMPONENT_SET", "SECTION"}
GENERIC_NAME = re.compile(
    r"^(frame|group|rectangle|vector|image|img|ellipse|union|subtract|mask group|layer|component|instance|"
    r"untitled|shape|polygon|star|line)\b[\s\d_().-]*$",
    re.I,
)
REFERENCE_SLICE_PX = 1100  # reference picture is cut into slices this tall so Claude can read them


_NODE_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
NODE_CACHE_SECONDS = 30 * 60


class FigmaError(RuntimeError):
    pass


@dataclass
class FigmaLink:
    file_key: str
    node_id: str


@dataclass
class Asset:
    file: str
    node_id: str
    name: str
    width: int  # display width in the design (CSS px)
    height: int
    kind: str  # photo | graphic | marked
    has_text: bool  # the exported picture includes text drawn on top


def parse_link(url: str) -> FigmaLink:
    u = urlparse(url.strip("<>"))
    if "figma.com" not in u.netloc:
        raise FigmaError("That doesn't look like a Figma link.")
    parts = [p for p in u.path.split("/") if p]
    # /design/<key>/<name>, /file/<key>/..., /proto/<key>/..., /design/<key>/branch/<branchKey>/...
    if len(parts) < 2 or parts[0] not in {"design", "file", "proto", "board"}:
        raise FigmaError("Couldn't find the file key in that Figma link.")
    key = parts[1]
    if len(parts) >= 4 and parts[2] == "branch":
        key = parts[3]
    node = parse_qs(u.query).get("node-id", [""])[0]
    if not node:
        raise FigmaError("The link has no node-id. In Figma, select the email frame and use Copy link to selection.")
    return FigmaLink(file_key=key, node_id=unquote(node).replace("-", ":"))


class Figma:
    def __init__(self, token: str | None = None):
        token = token or config.FIGMA_TOKEN
        if not token:
            raise FigmaError("FIGMA_TOKEN is not set in .env.")
        self.http = httpx.Client(
            base_url=API, headers={"X-Figma-Token": token}, timeout=120, follow_redirects=True
        )

    def _get(self, path: str, **params):
        r = self.http.get(path, params=params)
        if r.status_code == 403:
            raise FigmaError("Figma refused access (403). Check FIGMA_TOKEN and that it can open this file.")
        if r.status_code == 404:
            raise FigmaError("Figma file or frame not found (404).")
        if r.status_code == 429:
            wait = int(r.headers.get("retry-after", "60") or 60)
            if wait <= 90:  # a short burst limit: wait it out once
                time.sleep(wait + 1)
                r = self.http.get(path, params=params)
            if r.status_code == 429:
                wait = int(r.headers.get("retry-after", "60") or 60)
                when = f"{wait // 86400} days {wait % 86400 // 3600} hours" if wait > 3600 else f"{wait // 60 + 1} minutes"
                msg = f"Figma's API limit is used up for this token. It resets in about {when}."
                if r.headers.get("x-figma-rate-limit-type") == "low":
                    msg += (" The token belongs to a Figma account with a View or Collab seat, which only gets a"
                            " few API calls a month. Use a token from an account with a Dev or Full seat on"
                            " that team.")
                raise FigmaError(msg)
        r.raise_for_status()
        data = r.json()
        if data.get("err"):
            raise FigmaError(f"Figma: {data['err']}")
        return data

    def node(self, link: FigmaLink) -> dict:
        key = (link.file_key, link.node_id)
        hit = _NODE_CACHE.get(key)
        if hit and time.time() - hit[0] < NODE_CACHE_SECONDS:
            return hit[1]
        data = self._get(f"/files/{link.file_key}/nodes", ids=link.node_id)
        entry = (data.get("nodes") or {}).get(link.node_id)
        if not entry or not entry.get("document"):
            raise FigmaError("That frame wasn't found in the file. Copy the link to the email frame again.")
        doc = entry["document"]
        now = time.time()

        def remember(n):  # every frame inside is cached too, so building it costs no extra call
            _NODE_CACHE[(link.file_key, n["id"])] = (now, n)
            for c in n.get("children", []) or []:
                if c.get("type") in {"FRAME", "SECTION", "COMPONENT", "INSTANCE", "GROUP", "CANVAS"}:
                    remember(c)

        remember(doc)
        return doc

    def render(self, file_key: str, ids: list[str], scale: float, fmt: str = "png") -> dict[str, bytes]:
        out: dict[str, bytes] = {}
        for i in range(0, len(ids), 100):
            chunk = ids[i : i + 100]
            data = self._get(f"/images/{file_key}", ids=",".join(chunk), scale=scale, format=fmt)
            for node_id, url in (data.get("images") or {}).items():
                if url:
                    r = httpx.get(url, timeout=120, follow_redirects=True)
                    r.raise_for_status()
                    out[node_id] = r.content
        return out


MARKED_TEXT_MAX_HEIGHT = 1000  # taller marked layers with text are looked inside instead
EMAIL_MAX_WIDTH = 1000  # wider than this is a board/section, not one email
FRAME_TYPES = {"FRAME", "COMPONENT", "INSTANCE"}
MAX_EMAILS_PER_LINK = 10


def email_frames(url: str) -> list[tuple[str, str]]:
    """The emails behind a link: [(name, link to that frame)].

    A link to one email frame gives that frame. A link to a section (or a page, or a wide frame
    used as a board) gives every email-sized frame inside it, in reading order.
    """
    link = parse_link(url)
    root = Figma().node(link)

    def width(n):
        return (n.get("absoluteBoundingBox") or {}).get("width", 0)

    def is_container(n):
        return n.get("type") in {"SECTION", "CANVAS", "GROUP"} or (
            n.get("type") in FRAME_TYPES and width(n) > EMAIL_MAX_WIDTH
        )

    if not is_container(root):
        return [(root.get("name", "email"), url)]

    found: list[dict] = []

    def walk(n):
        for c in _children(n):
            if c.get("type") in FRAME_TYPES and 0 < width(c) <= EMAIL_MAX_WIDTH:
                found.append(c)
            elif is_container(c):
                walk(c)

    walk(root)
    if not found:
        return [(root.get("name", "email"), url)]

    def pos(n):
        b = n.get("absoluteBoundingBox") or {}
        return (round(b.get("y", 0) / 1500), b.get("x", 0))  # rows of boards, then left to right

    found.sort(key=pos)
    return [
        (n.get("name", "email"), f"https://www.figma.com/design/{link.file_key}/x?node-id={n['id'].replace(':', '-')}")
        for n in found
    ]


# ---------------------------------------------------------------- tree helpers

def _visible(n: dict) -> bool:
    return n.get("visible", True) is not False


def _children(n: dict) -> list[dict]:
    return [c for c in n.get("children", []) if _visible(c)]


def _has_image_fill(n: dict) -> bool:
    return any(f.get("type") == "IMAGE" and f.get("visible", True) for f in n.get("fills", []) or [])


def _has_text(n: dict) -> bool:
    return n.get("type") == "TEXT" or any(_has_text(c) for c in _children(n))


def _is_graphic(n: dict) -> bool:
    """Only drawn shapes (icons, logos, illustrations): no live text anywhere inside."""
    t = n.get("type")
    if t in GRAPHIC_TYPES:
        return True
    if _has_image_fill(n):
        return not _has_text(n)
    if t in CONTAINER_TYPES or t == "RECTANGLE":
        kids = _children(n)
        return bool(kids) and not _has_text(n) and any(_is_graphic(c) for c in kids)
    return False


def _box(n: dict, origin: dict) -> tuple[int, int, int, int]:
    b = n.get("absoluteBoundingBox") or {}
    return (
        round(b.get("x", 0) - origin.get("x", 0)),
        round(b.get("y", 0) - origin.get("y", 0)),
        round(b.get("width", 0)),
        round(b.get("height", 0)),
    )


def _hex(color: dict, opacity: float = 1.0) -> str:
    r, g, b = (round(color.get(k, 0) * 255) for k in "rgb")
    a = color.get("a", 1) * opacity
    h = f"#{r:02x}{g:02x}{b:02x}"
    return h if a >= 0.999 else f"{h} @{a:.2f}"


def _fill_desc(n: dict) -> str:
    out = []
    for f in n.get("fills", []) or []:
        if not f.get("visible", True):
            continue
        if f["type"] == "SOLID":
            out.append(_hex(f["color"], f.get("opacity", 1)))
        elif f["type"].startswith("GRADIENT"):
            stops = ", ".join(_hex(s["color"]) for s in f.get("gradientStops", []))
            out.append(f"{f['type'].lower()}({stops})")
        elif f["type"] == "IMAGE":
            out.append("image")
    return " + ".join(out)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "image"


# ---------------------------------------------------------------- assets

def pick_assets(root: dict) -> list[tuple[dict, str]]:
    """Nodes that should become image files. Doesn't look inside a node once it's picked."""
    picked: list[tuple[dict, str]] = []
    frame_w = (root.get("absoluteBoundingBox") or {}).get("width", 600)

    def walk(n: dict, is_root=False):
        if not _visible(n):
            return
        if not is_root:
            b = n.get("absoluteBoundingBox") or {}
            # Designers often mark whole emails or big text sections for export. Exporting those
            # would bake the text into a picture, so only honour the mark on smaller pieces.
            if n.get("exportSettings") and not (_has_text(n) and b.get("height", 0) > MARKED_TEXT_MAX_HEIGHT):
                picked.append((n, "marked"))
                return
            if _has_image_fill(n):
                picked.append((n, "photo"))
                return
            if _is_graphic(n):
                b = n.get("absoluteBoundingBox") or {}
                if b.get("width", 0) >= 2 and b.get("height", 0) >= 2:
                    picked.append((n, "graphic"))
                return
            if n.get("type") in CONTAINER_TYPES and _children(n) and not _has_text(n):
                # A text-free block mixing shapes and plain boxes (e.g. a badge): one picture.
                b = n.get("absoluteBoundingBox") or {}
                if b.get("width", 0) < frame_w * 0.98 and any(
                    c.get("type") in GRAPHIC_TYPES or _has_image_fill(c) or _children(c) for c in _children(n)
                ):
                    picked.append((n, "graphic"))
                    return
        for c in _children(n):
            walk(c)

    walk(root, is_root=True)
    return picked


def _name_assets(picked: list[tuple[dict, str]]) -> dict[str, str]:
    """node id -> file name. Instances of the same component share one file."""
    names: dict[str, str] = {}
    by_component: dict[str, str] = {}
    used: set[str] = set()
    counter = 0
    for n, kind in picked:
        comp = n.get("componentId")
        if comp and comp in by_component:
            names[n["id"]] = by_component[comp]
            continue
        raw = n.get("name", "")
        if GENERIC_NAME.match(raw.strip()) or not raw.strip():
            counter += 1
            base = f"{'photo' if kind == 'photo' else 'graphic'}-{counter}"
        else:
            base = _slug(raw)
        name, i = f"{base}.png", 2
        while name in used:
            name, i = f"{base}-{i}.png", i + 1
        used.add(name)
        names[n["id"]] = name
        if comp:
            by_component[comp] = name
    return names


def _optimize_png(data: bytes) -> bytes:
    try:
        img = Image.open(io.BytesIO(data))
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue() if buf.tell() < len(data) else data
    except Exception:
        return data


# ---------------------------------------------------------------- outline

def outline(root: dict, assets_by_node: dict[str, str]) -> str:
    """Indented text description of the frame: boxes, colours, text and styles."""
    origin = root.get("absoluteBoundingBox") or {}
    lines: list[str] = []

    def walk(n: dict, depth: int):
        if not _visible(n):
            return
        x, y, w, h = _box(n, origin)
        pad = "  " * depth
        head = f"{pad}- {n.get('type')} \"{n.get('name', '')}\" x={x} y={y} w={w} h={h}"
        extra = []
        fill = _fill_desc(n)
        if fill and n.get("type") != "TEXT":
            extra.append(f"bg={fill}")
        if n.get("cornerRadius"):
            extra.append(f"radius={round(n['cornerRadius'])}")
        strokes = [s for s in n.get("strokes", []) or [] if s.get("visible", True) and s.get("type") == "SOLID"]
        if strokes and n.get("strokeWeight"):
            extra.append(f"border={n['strokeWeight']}px {_hex(strokes[0]['color'])}")
        if n.get("layoutMode") in ("HORIZONTAL", "VERTICAL"):
            p = [n.get(k, 0) for k in ("paddingTop", "paddingRight", "paddingBottom", "paddingLeft")]
            extra.append(f"auto-layout={n['layoutMode'].lower()} gap={n.get('itemSpacing', 0)} padding={p}")
        if n.get("opacity", 1) < 1:
            extra.append(f"opacity={n['opacity']:.2f}")

        if n["id"] in assets_by_node:
            lines.append(f"{head} {' '.join(extra)} => IMAGE images/{assets_by_node[n['id']]}".rstrip())
            for t in _texts_inside(n):
                lines.append(f"{pad}    (text drawn in this image: {t!r})")
            return
        if n.get("type") == "TEXT":
            s = n.get("style", {})
            color = _fill_desc(n)
            style = (
                f"font={s.get('fontFamily')} {s.get('fontWeight')} {s.get('fontSize')}px"
                f" line-height={round(s.get('lineHeightPx', 0), 1)}px"
                f" letter-spacing={round(s.get('letterSpacing', 0), 2)}px"
                f" align={str(s.get('textAlignHorizontal', 'LEFT')).lower()} color={color}"
            )
            if s.get("textCase") and s["textCase"] != "ORIGINAL":
                style += f" case={s['textCase'].lower()}"
            if s.get("textDecoration") and s["textDecoration"] != "NONE":
                style += f" decoration={s['textDecoration'].lower()}"
            lines.append(f"{head} {style}")
            text = n.get("characters", "")
            overrides = n.get("characterStyleOverrides") or []
            if overrides and any(overrides):
                lines.append(f"{pad}    (mixed styles inside this text: some words bold/coloured/linked)")
            for t in text.split("\n"):
                lines.append(f"{pad}    | {t}")
            return
        lines.append(f"{head} {' '.join(extra)}".rstrip())
        for c in _children(n):
            walk(c, depth + 1)

    walk(root, 0)
    return "\n".join(lines)


def _texts_inside(n: dict) -> list[str]:
    if n.get("type") == "TEXT":
        return [n.get("characters", "")]
    out = []
    for c in _children(n):
        out += _texts_inside(c)
    return out


def all_text(root: dict) -> str:
    return "\n".join(_texts_inside(root))


# ---------------------------------------------------------------- main entry

def fetch_design(url: str, out_dir: Path, log=print) -> dict:
    """Write reference slices, images/ and design.md into out_dir. Returns a summary."""
    link = parse_link(url)
    fig = Figma()
    log("Reading the Figma frame…")
    root = fig.node(link)
    picked = pick_assets(root)
    names = _name_assets(picked)

    out_dir.mkdir(parents=True, exist_ok=True)
    img_dir = out_dir / "images"
    img_dir.mkdir(exist_ok=True)

    log(f"Exporting the design preview and {len(set(names.values()))} images from Figma…")
    unique_ids = []
    seen = set()
    for n, _ in picked:
        if names[n["id"]] not in seen:
            seen.add(names[n["id"]])
            unique_ids.append(n["id"])
    # One Figma export call for the whole email (the API allowance is small on some seats).
    rendered = fig.render(link.file_key, [link.node_id, *unique_ids], scale=config.FIGMA_SCALE)
    if link.node_id not in rendered:
        raise FigmaError("Figma couldn't render that frame.")
    full = rendered.pop(link.node_id)
    (out_dir / "design-full.png").write_bytes(full)
    (out_dir / "design.json").write_text(json.dumps({"scale": config.FIGMA_SCALE}))
    ref = Image.open(io.BytesIO(full))
    fw_px = round((root.get("absoluteBoundingBox") or {}).get("width") or ref.width / config.FIGMA_SCALE)
    if ref.width != fw_px:
        ref = ref.resize((fw_px, round(ref.height * fw_px / ref.width)), Image.LANCZOS)
    ref.save(out_dir / "reference.png")
    slices = _slice_reference(out_dir / "reference.png", out_dir)

    origin = root.get("absoluteBoundingBox") or {}
    assets: list[Asset] = []
    for n, kind in picked:
        fname = names[n["id"]]
        if n["id"] in rendered:
            (img_dir / fname).write_bytes(_optimize_png(rendered[n["id"]]))
        if any(a.file == fname for a in assets):
            continue
        _, _, w, h = _box(n, origin)
        assets.append(Asset(fname, n["id"], n.get("name", ""), w, h, kind, _has_text(n)))
    missing = [a.file for a in assets if not (img_dir / a.file).exists()]

    _, _, fw, fh = _box(root, origin)
    design = [
        f"# Figma frame: {root.get('name')}",
        f"Frame size: {fw} x {fh}px. Background: {_fill_desc(root) or 'none'}.",
        f"Reference pictures (top to bottom): {slice_note(slices)}",
        "",
        "## Images exported from this frame (files in images/, retina 2x; use the design width below)",
    ]
    for a in assets:
        note = " — includes text drawn into the picture" if a.has_text else ""
        design.append(f"- images/{a.file}: {a.width}x{a.height}px, {a.kind}, layer \"{a.name}\"{note}")
    design += ["", "## Layer outline (positions relative to the frame's top-left)", "", outline(root, names)]
    (out_dir / "design.md").write_text("\n".join(design))
    (out_dir / "assets.json").write_text(json.dumps([asdict(a) for a in assets], indent=1))

    return {
        "frame_name": root.get("name", "email"),
        "width": fw,
        "height": fh,
        "text": all_text(root),
        "assets": [asdict(a) for a in assets],
        "missing": missing,
        "slices": slices,
    }


SLICE_OVERLAP = 60


def slice_note(slices: list[str]) -> str:
    """Where each slice starts in the full reference picture (for measuring y positions)."""
    if len(slices) == 1:
        return slices[0]
    step = REFERENCE_SLICE_PX - SLICE_OVERLAP
    return ", ".join(f"{n} (starts at y={i * step})" for i, n in enumerate(slices))


def _slice_reference(path: Path, out_dir: Path, prefix: str = "reference") -> list[str]:
    img = Image.open(path)
    w, h = img.size
    if h <= REFERENCE_SLICE_PX * 1.3:
        return [path.name]
    names = []
    overlap = SLICE_OVERLAP
    top, i = 0, 1
    while top < h:
        bottom = min(h, top + REFERENCE_SLICE_PX)
        name = f"{prefix}-{i}.png"
        img.crop((0, top, w, bottom)).save(out_dir / name)
        names.append(name)
        if bottom == h:
            break
        top, i = bottom - overlap, i + 1
    return names
