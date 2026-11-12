"""What the bot does in Slack, independent of how messages reach it.

Used by both ways of running the bot:
- slack_app.py: one long-lived process (Socket Mode) on a Mac or server; slow work runs in
  background threads and state lives on disk.
- runner.py: a GitHub Actions run started by the Cloudflare Worker for one message; work runs
  inline and state lives in the Worker's KV storage.
"""
from __future__ import annotations

import logging
import re
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from slack_sdk import WebClient

from . import config, figma, pipeline
from .store import LocalStore

log = logging.getLogger("emailbot")

URL = re.compile(r"<(https?://[^>|]+)(?:\|[^>]*)?>|(https?://\S+)")
HOSTED = re.compile(r"amazonaws\.com|cloudfront\.net", re.I)
FIGMA = re.compile(r"figma\.com/(?:design|file|proto)/", re.I)
MENTION = re.compile(r"<@[A-Z0-9]+>")
PENDING = "pending"

HELP = (
    "Two ways to start an email:\n"
    "• `/email <Figma link> [notes]` — reads the design straight from Figma\n"
    "• @mention me and attach the email exported from Figma as PNG/JPG, plus its images "
    "(or one .zip of them). The biggest picture is taken as the design. Add notes in the same message."
)
USAGE = (
    "Usage: `/email <Figma link to the email frame> [notes, e.g. base it on welcome.html]`\n"
    "No Figma access? @mention me in a channel and attach the exported design (PNG/JPG) and its images."
)


def urls_in(text: str) -> list[str]:
    return [a or b.rstrip(">.,)") for a, b in URL.findall(text or "")]


def strip_urls(text: str) -> str:
    return MENTION.sub("", URL.sub("", text or "")).strip()


def figma_links(text: str) -> list[str]:
    return [u for u in urls_in(text) if FIGMA.search(u)]


class Bot:
    def __init__(self, client: WebClient, store=None, inline: bool = False):
        self.client = client
        self.store = store or LocalStore()
        self.inline = inline
        self.pool = None if inline else ThreadPoolExecutor(max_workers=3)
        self.locks: dict[str, threading.Lock] = {}

    # ------------------------------------------------------------ Slack helpers

    def say(self, channel: str, ts: str, text: str):
        self.client.chat_postMessage(channel=channel, thread_ts=ts, text=text, unfurl_links=False)

    def upload(self, channel: str, ts: str, comment: str, files: list[Path]):
        self.client.files_upload_v2(
            channel=channel,
            thread_ts=ts,
            initial_comment=comment,
            file_uploads=[{"file": str(f), "filename": f.name, "title": f.name} for f in files if f],
        )

    def download(self, files: list[dict]) -> list[tuple[str, bytes]]:
        """Fetch files attached to a Slack message (needs the files:read scope)."""
        out = []
        for f in files:
            url = f.get("url_private_download") or f.get("url_private")
            if not url or f.get("size", 0) > 50 * 1024 * 1024:
                continue
            r = httpx.get(url, headers={"Authorization": f"Bearer {self.client.token}"},
                          follow_redirects=True, timeout=120)
            if r.status_code != 200 or r.headers.get("content-type", "").startswith("text/html"):
                raise RuntimeError(
                    "I couldn't download the attached files. The Slack app needs the files:read permission "
                    "(api.slack.com/apps → OAuth & Permissions → add files:read → Reinstall)."
                )
            out.append((f.get("name") or "file", r.content))
        return out

    def run(self, channel: str, ts: str, work):
        """Run a slow step (inline, or in the background one at a time per thread); report errors."""
        key = f"{channel}:{ts}"
        lock = self.locks.setdefault(key, threading.Lock())

        def task():
            if not lock.acquire(blocking=False):
                self.say(channel, ts, "I'm still working on the last request in this thread; "
                                      "I'll pick this up when it's done.")
                lock.acquire()
            try:
                work()
            except figma.FigmaError as e:
                self.say(channel, ts, f":x: {e}")
            except Exception as e:
                if self.inline:  # public CI logs: the error type only, no content
                    log.error("job failed: %s", type(e).__name__)
                else:
                    log.error("job failed\n%s", traceback.format_exc())
                self.say(channel, ts, f":x: Something went wrong: {str(e)[:500]}")
            finally:
                lock.release()

        if self.inline:
            task()
        else:
            self.pool.submit(task)

    def report_build(self, channel: str, ts: str, res: pipeline.Result, first: bool):
        job = res.job
        lines = []
        if first:
            others = ", ".join(Path(c).name for c in job.candidates[1:3])
            lines.append(f":white_check_mark: Email built from *{Path(job.base or job.candidates[0]).name}*"
                         + (f" (other close matches: {others})." if others else "."))
        else:
            lines.append(":white_check_mark: Updated.")
        notes = "\n".join(l for l in res.notes.splitlines()[1:] if l.strip())
        if notes:
            lines.append(f"*Notes:*\n{notes}")
        lines += res.warnings
        used = res.report.used if res.report else []
        real = [f for f in used if not f.startswith("MISSING-")]
        lines.append(
            f"\n`{res.images_zip.name}` has the HTML and its {len(real)} image(s) in an images/ folder"
            " (unzip and open the .html to see it as it will look)."
        )
        if real:
            lines.append(
                "*Next:* reply with any changes. When it's right, upload the images/ folder to S3 and reply "
                "with the S3 folder URL (or the URL of any one image); I'll send the final client zip with "
                "the S3 links in the HTML. (For a CDN that isn't S3/CloudFront, write `final <url>`.)"
            )
        else:
            lines.append("*Next:* reply with any changes, or `final` for the client zip.")
        self.upload(channel, ts, "\n".join(lines), [f for f in (res.preview, res.images_zip) if f])

    def _remember(self, channel: str, ts: str):
        return lambda job: self.store.set_thread(channel, ts, job.id)

    # ------------------------------------------------------------ flows

    def start(self, channel: str, ts: str, text: str, files: list[dict]):
        """A new email: from a Figma link in the text, or from attached files."""
        links = figma_links(text)
        notes = strip_urls(text)
        if links:
            self.start_from_link(channel, ts, links[0], notes)
        elif files:
            self.start_upload(channel, ts, files, notes)
        else:
            self.say(channel, ts, HELP)

    def start_from_link(self, channel: str, ts: str, figma_url: str, notes: str):
        """A link may be one email or a section holding several; each email gets its own thread."""

        def work():
            frames = figma.email_frames(figma_url)
            if len(frames) == 1:
                self._build(channel, ts, lambda remember: pipeline.start(
                    frames[0][1], notes, log=lambda m: self.say(channel, ts, m), on_job=remember))
                return
            if len(frames) > figma.MAX_EMAILS_PER_LINK:
                names = ", ".join(n for n, _ in frames[:15])
                self.say(channel, ts, f"That link holds {len(frames)} frames ({names}…). Send a link to a "
                                      "smaller section, or to one email frame (right-click → Copy link to selection).")
                return
            self.say(channel, ts, f"This section has {len(frames)} emails: "
                                  + ", ".join(f"*{n}*" for n, _ in frames)
                                  + ". I'll build each one in its own thread in this channel.")
            for i, (name, url) in enumerate(frames, 1):
                msg = self.client.chat_postMessage(
                    channel=channel,
                    text=f"Email {i} of {len(frames)}: *{name}* (<{url}|Figma frame>)"
                         + (f"\n> {notes}" if notes else "")
                         + "\nReplies with changes or the S3 link go in this thread.",
                    unfurl_links=False,
                )
                self.store.set_thread(channel, msg["ts"], PENDING)
                child_ts, child_url = msg["ts"], url
                self.run(channel, child_ts, lambda c=child_ts, u=child_url: self._build(
                    channel, c, lambda remember: pipeline.start(
                        u, notes, log=lambda m: self.say(channel, c, m), on_job=remember)))

        self.run(channel, ts, work)

    def start_upload(self, channel: str, ts: str, files: list[dict], notes: str):
        self.run(channel, ts, lambda: self._build(channel, ts, lambda remember: pipeline.start_upload(
            self.download(files), notes, log=lambda m: self.say(channel, ts, m), on_job=remember)))

    def _build(self, channel: str, ts: str, make):
        job_ids = []

        def remember(job):
            job_ids.append(job.id)
            self.store.set_thread(channel, ts, job.id)

        try:
            res = make(remember)
            self.report_build(channel, ts, res, first=True)
        finally:
            for j in job_ids:
                self.store.push_job(j)

    def follow_up(self, channel: str, ts: str, job_id: str, text: str, files: list[dict] | None = None):
        """A reply in one of our threads: a change, new images, or the S3 link."""
        text = MENTION.sub("", text or "").strip()
        if job_id == PENDING:
            self.say(channel, ts, "I'm still building this email. Send changes after I've posted it.")
            return
        # The final step is an S3/CloudFront link, or "final <url>". Any other link is part of a
        # change request (e.g. "point the button to https://…").
        first_word = (text.split() or [""])[0].lower()
        links = [u for u in urls_in(text) if not FIGMA.search(u)]
        hosted = [u for u in links if HOSTED.search(u)]
        if first_word in {"final", "finalize", "s3"}:
            hosted = hosted or links
        wants_final = not files and (bool(hosted) or text.lower() in {"final", "finalize", "zip"})

        def work():
            if not self.store.pull_job(job_id):
                self.say(channel, ts, "I can't find this email's files any more (they're kept for 60 days). "
                                      "Start a new one.")
                return
            job = pipeline.Job.load(job_id)
            try:
                if wants_final:
                    self._finalize(channel, ts, job, hosted[0] if hosted else job.s3_url)
                else:
                    self._revise(channel, ts, job, text, files)
            finally:
                self.store.push_job(job_id)

        self.run(channel, ts, work)

    def _finalize(self, channel: str, ts: str, job: pipeline.Job, s3: str):
        if not s3:
            html = (job.dir / "email.html").read_text(errors="ignore")
            if pipeline.package.local_images(html):
                self.say(channel, ts, "Send me the S3 folder URL where you uploaded the images.")
                return
            s3 = "https://example.invalid/"  # no local images to swap
        res = pipeline.finalize(job, s3, log=lambda m: self.say(channel, ts, m))
        msg = [f":package: Final email for the client: `{res.final_zip.name}` (HTML with S3 image links + images)."]
        html_now = (job.dir / "email.html").read_text(errors="ignore")
        gaps = [f for f in pipeline.package.local_images(html_now) if f.startswith("MISSING-")]
        if gaps:
            msg.append(f":warning: Not ready to send: still has placeholder boxes for {', '.join(gaps)}. "
                       "Attach those images here first.")
        msg += res.warnings
        self.upload(channel, ts, "\n".join(msg), [res.final_zip, res.html])

    def _revise(self, channel: str, ts: str, job: pipeline.Job, text: str, files: list[dict] | None):
        request = text
        if files:
            saved = pipeline.add_images(job, self.download(files))
            if saved:
                listed = ", ".join(f"images/{a['file']} ({a['w']}x{a['h']}px file)" for a in saved)
                request = (text or "Use these new images where they belong in the design.") + (
                    f"\n\nNew images uploaded in Slack: {listed}. They are usually 2x exports; "
                    "set the display width from the design. Where one matches an images/MISSING-… "
                    "placeholder, replace the placeholder with it."
                )
        if not request.strip():
            return
        res = pipeline.revise(job, request, log=lambda m: self.say(channel, ts, m))
        self.report_build(channel, ts, res, first=False)
