"""Have Claude Code build (or revise) the email HTML inside a job folder."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import config

TOOLS = ["Read", "Write", "Edit", "Glob", "Grep"]
# Text from designs and Slack messages ends up in prompts, so no shell or web access at all.
BLOCKED = ["Bash", "WebFetch", "WebSearch", "Task", "NotebookEdit"]

RULES = """\
## Rules

Base template
- Pick the candidate in candidates/ whose layout is closest to the design (candidates/README.md
  lists them, best wording match first). Copy that file to email.html and change it; never start
  from a blank page.
- Keep everything the design doesn't change exactly as the base has it: the <head>, CSS and media
  queries, Outlook conditional comments, wrapper tables, font stacks, preheader, header/logo,
  footer, social icons, legal text, link tracking and merge tags such as {{View_Online}},
  {{unsubscribe}} and {{{EmailPreferenceCenter}}}. Never invent, rename or drop a merge tag.
- Read the base fully, and render it in your head: its desktop and mobile behaviour is what
  we want, so change it carefully rather than rewriting sections.
- Reuse the base's own section patterns (hero, text block, button, two-column, cards, divider)
  when the design has the same kind of section, so the result looks like our other emails in code.
  Look at the other candidates for a pattern the base is missing before writing a new one.

Match the design
- Read every reference-*.png (or reference.png) and design.md before writing. The pictures show
  how it should look; design.md gives exact text, colours, font sizes, line heights, spacing and
  widths. Where they disagree on a number, trust design.md.
- Text must be the design's text, word for word (fix nothing, add nothing). Keep live text as
  HTML text; only text that design.md says is drawn into an image stays inside that image.
- Colours, font sizes, weights, line heights, letter spacing, padding, radius and section order
  must match the design. The email width matches the frame width (normally 600px).

Real HTML, not pictures of the design
- The email must be coded HTML: every headline, paragraph, list, button, link, price, stat and
  footer line is live text in table cells. Never use a picture of the whole email, of a whole
  section, or of a block of text in place of HTML, even if such a picture exists in images/.
- Pictures are only for real imagery: photos, illustrations, icons, logos, badges, product shots.
  Text drawn inside such a picture (e.g. a word on a product photo or a logo) is fine; a headline
  or paragraph over a photo should be live text on top of (or next to) the photo, the way the
  base template does it.
- If the design needs a photo or graphic that isn't in images/, cut it out of the design: add an
  entry to crops.json (see below) and use it as src="images/<file>". Never substitute a bigger
  picture, and never cut out a region that has HTML text on top of it (that would duplicate the
  text).
- Only when a picture can't be cut cleanly (e.g. a photo with a headline or logo laid over it
  that you build as live text) use src="images/MISSING-<short-name>.png" (or the same name as a
  background image) with the right width/height and alt text, and list it in notes.md so the
  team can export it without the text. The bot draws a labelled grey box there meanwhile. For a
  MISSING background image, put data-placeholder="<width>x<height>" (design size) on that cell
  so the box gets the right size.

Cutting pictures out of the design (crops.json)
- crops.json is a JSON list: [{"file": "logo-miami-herald.png", "x": 120, "y": 2110, "w": 148,
  "h": 22, "trim": true}]. x/y/w/h are in the full reference picture's pixels (the design's own
  size; design.md says where each reference slice starts, so add that offset to y).
- Cut tight around the picture with a few px of margin. "trim": true removes a flat background
  border (use it for logos, icons and badges on a plain background; not for photos).
- File names: lowercase, a-z 0-9 and dashes, .png (or .jpg for big photos).
- The bot cuts them at full resolution after you finish; you'll see the results in the
  screenshots later and can fix the boxes then.

Images
- New pictures are in images/ (2x exports). Use them as src="images/<file>" with the width=""
  attribute set to the design width from design.md, style="display:block; width:100%;
  max-width:<width>px; height:auto; border:0;" (or the base's own image style) and a short, real
  alt text.
- If a picture is the same as one the base already uses (logo, social icons, app badges, footer
  graphics), keep the base's existing hosted URL and don't use the exported copy.
- Don't reference any file in images/ that doesn't exist (except crops.json and MISSING- files),
  and don't hotlink figma.com.
- Fonts: use the design's font (from design.md / the picture) as the first font in the stack,
  with the base's fallbacks after it, e.g. font-family: Inter, Arial, sans-serif. Load a web
  font the same way the base or another candidate already does, if any of them does.

Email-safe code
- Tables for layout, inline styles on every element that needs them (as the base does),
  bulletproof buttons the way the base builds them, no flexbox/grid, no JavaScript, no external
  CSS other than what the base already loads. Must render in Gmail, Outlook (desktop + web) and
  Apple Mail, and stack on mobile using the base's media queries.
- Links: keep the base's link where the element is the same; use links from the request notes
  for new buttons/links; otherwise href="#".
- Keep the file under 100 KB (Gmail clips larger emails).
- Never put a height="" (or CSS height) on a cell that also has padding: browsers add the two
  and the section grows. Size padded sections with padding alone.

When you're done
- email.html is the finished email.
- Write notes.md: first line "Base: <candidate file>", then short bullets for anything you were
  unsure about or couldn't match exactly. No other commentary.
- Don't ask questions; make the best call and note it in notes.md.
"""


def _run(cwd: Path, prompt: str, resume: str | None = None) -> dict:
    cmd = [config.CLAUDE_BIN, "-p", prompt, "--output-format", "json", "--allowedTools", *TOOLS,
           "--disallowedTools", *BLOCKED]
    cmd += ["--permission-mode", "acceptEdits"]
    if config.CLAUDE_MODEL:
        cmd += ["--model", config.CLAUDE_MODEL]
    if resume:
        cmd += ["--resume", resume]
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=config.CLAUDE_TIMEOUT, stdin=subprocess.DEVNULL
    )
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"Claude Code failed: {(proc.stderr or proc.stdout)[-800:]}")
    if out.get("is_error"):
        raise RuntimeError(f"Claude Code error: {str(out.get('result'))[-800:]}")
    return out


def transcribe(job_dir: Path) -> str:
    """For uploaded designs (no Figma data): write down the text and styles from the pictures."""
    prompt = """Read every reference-*.png (or reference.png) in this folder, top to bottom. It is one
email design. Write design_text.md describing it section by section, so a developer can build it
without seeing the picture:

## Section N: <short name> (background colour as hex)
- each text block, word for word in quotes, then: approx font size px, weight (400/600/700),
  colour hex, alignment, and whether it's a heading, paragraph, button label or link
- buttons: label, background hex, text colour hex, corner radius, approx width x height
- pictures: what it shows, approx position and size in px, and which images/ file matches it if one does
- spacing: approx padding above/below the section

Measure in design pixels (the picture's own pixels). Copy text exactly, including punctuation and
capitals. Don't add commentary before or after. Write only design_text.md."""
    _run(job_dir, prompt)
    p = job_dir / "design_text.md"
    if not p.exists():
        raise RuntimeError("Couldn't read the text from the uploaded design.")
    return p.read_text()


def build(job_dir: Path, request: str) -> dict:
    prompt = f"""You are an email developer building HTML emails for {config.BRAND_NAME}.
Build the email shown in this folder's Figma export as email.html.

Request from the team: {request or "(no extra notes — match the design)"}

Folder contents
- reference*.png: the Figma design, top to bottom
- design.md: exact text, colours, sizes and spacing from Figma, plus the images list
- images/: pictures exported from Figma
- candidates/: our closest existing templates (README.md lists them)

{RULES}"""
    return _run(job_dir, prompt)


def review(job_dir: Path, shots: dict, crop_problems: list[str]) -> str:
    """Compare screenshots of email.html with the design and fix what's off. Returns review.md."""
    over = shots["overflow"]
    wide = "\n".join(f"  - {w}" for w in over["wide"]) or "  - nothing (good)"
    w, h = shots["desktop_size"]
    prompt = f"""You built email.html in this folder from the design. Now check it like a QA tester and
fix it.

Screenshots of email.html right now:
- Desktop, {w}px wide (same width as the design), {h}px tall: {', '.join(shots['desktop'])}
- Phone, 390px wide: {', '.join(shots['phone'])}
Things sticking out past the phone screen (page should be 390px wide, is {over['scrollWidth']}px):
{wide}
{"Problems with crops.json: " + "; ".join(crop_problems) if crop_problems else ""}

The design: reference-*.png (and design.md). Pictures cut from the design: crops.json -> images/.

Go through the design section by section, top to bottom, and compare each with the desktop
screenshot. Look for differences in: spacing above/below/between elements, section heights,
font family/size/weight/line height/colour, alignment, widths, image sizes and crops (a cut-out
picture with a stray edge or the wrong area), button size/shape/colour, divider lines, icon
sizes (all social icons the same size), background colours. Then check the phone screenshots:
nothing may be wider than the screen, text must be readable, columns stack cleanly, images
scale down.

Fix every difference you find in email.html (and crops.json for cut-outs), following the rules
below. Don't change any wording. Don't restructure sections that already match.

Then write review.md: the first line is exactly "OK" if the email now matches the design closely
(no difference a client would notice), otherwise "CHANGED", followed by short bullets of what
you fixed and anything that still differs.

{RULES}"""
    _run(job_dir, prompt)
    p = job_dir / "review.md"
    return p.read_text() if p.exists() else ""


def revise(job_dir: Path, request: str, session_id: str | None) -> dict:
    prompt = f"""Change email.html in this folder as requested below. Change only what is asked;
keep everything else exactly as it is. The Figma export (reference*.png, design.md, images/)
and candidates/ are still here if you need them.

Change request: {request}

{RULES}"""
    if session_id:
        try:
            return _run(job_dir, prompt, resume=session_id)
        except Exception:
            pass  # session gone (e.g. bot restarted elsewhere) — run fresh
    return _run(job_dir, prompt)
