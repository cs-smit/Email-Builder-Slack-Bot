"""The three steps, shared by the Slack bot and the command line.

1. start(figma_url, notes)  -> email.html + a zip of its new images (to upload to S3)
2. revise(job, request)     -> email.html changed as asked (any number of times)
3. finalize(job, s3_url)    -> <slug>.zip: HTML pointing at S3 + images, ready for the client
"""
from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image

from . import config, figma, generator, library, package, render, uploads


@dataclass
class Job:
    id: str
    figma_url: str
    notes: str
    slug: str = "email"
    base: str = ""  # library file the email was built from
    candidates: list[str] = field(default_factory=list)
    session_id: str = ""
    s3_url: str = ""
    created: float = field(default_factory=time.time)
    history: list[str] = field(default_factory=list)

    @property
    def dir(self) -> Path:
        return config.JOBS_DIR / self.id

    def save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "job.json").write_text(json.dumps(asdict(self), indent=1))

    @classmethod
    def load(cls, job_id: str) -> "Job":
        return cls(**json.loads((config.JOBS_DIR / job_id / "job.json").read_text()))


@dataclass
class Result:
    job: Job
    html: Path
    images_zip: Path | None = None  # <slug>.zip: the HTML + every image it uses
    preview: Path | None = None  # screenshot of the email
    final_zip: Path | None = None
    report: package.Report | None = None
    notes: str = ""
    warnings: list[str] = field(default_factory=list)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "email"


def _read_notes(job: Job) -> str:
    p = job.dir / "notes.md"
    return p.read_text().strip() if p.exists() else ""


def _base_from_notes(notes: str) -> str:
    m = re.search(r"^Base:\s*(?:candidates/)?(.+?)\s*$", notes, re.M)
    return m.group(1).strip("` ") if m else ""


def _design_width(job: Job) -> int:
    with Image.open(job.dir / "reference.png") as im:
        return im.width


def _prepare_images(job: Job, log) -> list[str]:
    made, problems = render.apply_crops(job.dir)
    if made:
        log(f"Cut {len(made)} picture(s) out of the design: {', '.join(made)}")
    render.make_placeholders(job.dir)
    return problems


def _finish(job: Job, log, review_rounds: int | None = None) -> Result:
    html = job.dir / "email.html"
    if not html.exists():
        raise RuntimeError("Claude didn't produce email.html. Try again or add more detail.")
    problems = _prepare_images(job, log)

    rounds = config.REVIEW_ROUNDS if review_rounds is None else review_rounds
    review = ""
    for i in range(rounds):
        log(f"Checking the result against the design (round {i + 1} of up to {rounds})…")
        shots = render.screenshots(job.dir, _design_width(job))
        review = generator.review(job.dir, shots, problems)
        problems = _prepare_images(job, log)
        if review.strip().upper().startswith("OK"):
            break
    preview = None
    try:
        render.screenshots(job.dir, _design_width(job))
        preview = job.dir / f"{job.slug}-preview.png"
        shutil.copy(job.dir / "render-desktop.png", preview)
    except Exception as e:  # a preview is nice to have; never fail the job for it
        log(f"(Couldn't take a preview screenshot: {str(e)[:200]})")

    notes = _read_notes(job)
    base_name = _base_from_notes(notes)
    if base_name:
        job.base = base_name
    base_path = job.dir / "candidates" / Path(job.base).name if job.base else None
    report = package.check(job.dir, base_path)
    named = job.dir / f"{job.slug}.local.html"
    shutil.copy(html, named)
    job.save()
    warnings = report.lines() + [f":warning: {p}" for p in problems]
    if review and not review.strip().upper().startswith("OK"):
        still = [l for l in review.splitlines()[1:] if l.strip()]
        if still:
            notes += "\n\n*After checking against the design:*\n" + "\n".join(still[:8])
    return Result(
        job=job,
        html=named,
        images_zip=package.package_zip(job.dir, job.slug),
        preview=preview,
        report=report,
        notes=notes,
        warnings=warnings,
    )


def _new_job(source: str, notes: str, on_job) -> Job:
    job = Job(id=time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4], figma_url=source, notes=notes)
    job.save()
    if on_job:
        on_job(job)
    return job


def _match_and_build(job: Job, design_text: str, notes: str, log) -> Result:
    log("Finding the closest existing templates…")
    templates = library.sync(log=log)
    matches = library.find_matches(design_text, hint=notes, top=4, templates=templates)
    cand_dir = job.dir / "candidates"
    cand_dir.mkdir(exist_ok=True)
    readme = ["Closest existing templates (best wording match first):", ""]
    for t, score in matches:
        dest = cand_dir / Path(t.file).name
        dest.write_text(library.clean(t.path.read_text(errors="ignore")))
        readme.append(f"- {dest.name}: \"{t.title}\" — {t.headline} (match {score:.2f}, live: {t.url})")
        job.candidates.append(t.file)
    (cand_dir / "README.md").write_text("\n".join(readme))
    job.save()

    log(f"Building the HTML from *{Path(job.candidates[0]).name}* and the design (a few minutes)…")
    out = generator.build(job.dir, notes)
    job.session_id = out.get("session_id", "")
    job.history.append(f"build: {notes}")
    return _finish(job, log)


def start(figma_url: str, notes: str = "", log=print, on_job=None) -> Result:
    job = _new_job(figma_url, notes, on_job)
    design = figma.fetch_design(figma_url, job.dir, log=log)
    job.slug = _slug(design["frame_name"])
    res = _match_and_build(job, design["text"], notes, log)
    if design["missing"]:
        res.warnings.append(f":warning: Figma didn't export: {', '.join(design['missing'])}")
    return res


def start_upload(files: list[tuple[str, bytes]], notes: str = "", log=print, on_job=None,
                 design_name: str | None = None) -> Result:
    """Same as start(), from files exported out of Figma by hand: [(file name, bytes)]."""
    job = _new_job("upload", notes, on_job)
    design = uploads.prepare(files, job.dir, design_name=design_name)
    job.slug = _slug(design["name"])
    log(f"Using *{design['name']}* as the design ({design['width']}px wide) and "
        f"{len(design['assets'])} other image(s). Reading its text and styles…")
    text = generator.transcribe(job.dir)
    with open(job.dir / "design.md", "a") as f:
        f.write("\n\n## What the design shows (read from the pictures)\n\n" + text)
    return _match_and_build(job, text, notes, log)


def resume(job: Job, notes: str = "", log=print) -> Result:
    """Finish a job whose first build stopped part-way (e.g. Claude was signed out)."""
    if not (job.dir / "reference.png").exists():
        raise RuntimeError("This email's design never got saved. Start a new one.")
    text_file = job.dir / "design_text.md"
    if job.figma_url == "upload":
        if not text_file.exists():
            log("Reading the design's text and styles…")
            text = generator.transcribe(job.dir)
            with open(job.dir / "design.md", "a") as f:
                f.write("\n\n## What the design shows (read from the pictures)\n\n" + text)
        design_text = text_file.read_text()
    else:
        design_text = (job.dir / "design.md").read_text()
    job.candidates = []
    return _match_and_build(job, design_text, notes or job.notes, log)


def add_images(job: Job, files: list[tuple[str, bytes]]) -> list[dict]:
    """Images sent later in the thread: saved into the job's images/."""
    return uploads.save_images(uploads.expand(files), job.dir / "images")


def revise(job: Job, request: str, log=print) -> Result:
    log("Making the changes…")
    out = generator.revise(job.dir, request, job.session_id or None)
    job.session_id = out.get("session_id", job.session_id)
    job.history.append(f"revise: {request}")
    return _finish(job, log, review_rounds=1)


def finalize(job: Job, s3_url: str, log=print) -> Result:
    base = package.normalise_base_url(s3_url)
    job.s3_url = base
    log("Swapping in the S3 image links and checking each one loads…")
    zip_path, broken = package.final_zip(job.dir, job.slug, base)
    job.history.append(f"finalize: {base}")
    job.save()
    res = Result(job=job, html=job.dir / f"{job.slug}.html", final_zip=zip_path)
    if broken:
        res.warnings.append(
            ":warning: These images don't load from S3 yet (not uploaded, wrong folder, or not public):\n"
            + "\n".join(f"• {b}" for b in broken)
        )
    return res
