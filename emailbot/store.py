"""Where the bot remembers things between messages.

- LocalStore: on this machine (data/). Used when the bot runs as one long-lived process.
- RemoteStore: in the Cloudflare Worker's KV storage. Used by GitHub Actions runs, which start
  on a fresh machine every time. Job folders are packed into a tar.gz and stored in chunks.

Thread map: "<channel>:<thread ts>" -> job id ("pending" while the first build is running).
"""
from __future__ import annotations

import io
import json
import tarfile
import threading
from pathlib import Path

import httpx
from urllib.parse import quote

from . import config

THREAD_TTL = 60 * 60 * 24 * 60  # keep threads and jobs for 60 days
CHUNK = 20 * 1024 * 1024  # KV values are limited to 25 MiB
SKIP = ("render-", "-preview.png")  # regenerated on every build; not worth storing


class LocalStore:
    def __init__(self):
        self.file = config.DATA_DIR / "threads.json"
        self.lock = threading.Lock()

    def _all(self) -> dict:
        return json.loads(self.file.read_text()) if self.file.exists() else {}

    def get_thread(self, channel: str, ts: str) -> str | None:
        return self._all().get(f"{channel}:{ts}")

    def set_thread(self, channel: str, ts: str, job_id: str):
        with self.lock:
            data = self._all()
            data[f"{channel}:{ts}"] = job_id
            self.file.write_text(json.dumps(data, indent=1))

    def pull_job(self, job_id: str) -> bool:
        return (config.JOBS_DIR / job_id / "job.json").exists()

    def push_job(self, job_id: str):
        pass


class RemoteStore:
    """Talks to the Worker's /internal/kv/<key> endpoints with the shared RUNNER_SECRET."""

    def __init__(self, base_url: str | None = None, secret: str | None = None):
        base = (base_url or config.WORKER_URL).rstrip("/")
        secret = secret or config.RUNNER_SECRET
        if not base or not secret:
            raise RuntimeError("WORKER_URL and RUNNER_SECRET must be set for the GitHub Actions runner.")
        self.http = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {secret}"}, timeout=120)

    @staticmethod
    def _path(key: str) -> str:
        # Keys contain ":" ("event:…"); quoted so they're never read as a URL scheme.
        return "/internal/kv/" + quote(key, safe="")

    def get(self, key: str) -> bytes | None:
        r = self.http.get(self._path(key))
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content

    def put(self, key: str, value: bytes, ttl: int = THREAD_TTL):
        r = self.http.put(self._path(key), content=value, params={"ttl": ttl})
        r.raise_for_status()

    def get_event(self, event_id: str) -> dict:
        raw = self.get(f"event:{event_id}")
        if raw is None:
            raise RuntimeError(f"Event {event_id} not found (expired?).")
        return json.loads(raw)

    def get_thread(self, channel: str, ts: str) -> str | None:
        raw = self.get(f"thread:{channel}:{ts}")
        return raw.decode() if raw else None

    def set_thread(self, channel: str, ts: str, job_id: str):
        self.put(f"thread:{channel}:{ts}", job_id.encode())

    def pull_job(self, job_id: str) -> bool:
        meta = self.get(f"job:{job_id}:meta")
        if not meta:
            return False
        chunks = json.loads(meta)["chunks"]
        data = b"".join(self.get(f"job:{job_id}:{i}") or b"" for i in range(chunks))
        dest = config.JOBS_DIR / job_id
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            tar.extractall(dest, filter="data")
        return True

    def push_job(self, job_id: str):
        src = config.JOBS_DIR / job_id
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tar:
            for p in sorted(src.rglob("*")):
                if p.is_file() and not any(s in p.name for s in SKIP):
                    tar.add(p, arcname=p.relative_to(src).as_posix())
        data = buf.getvalue()
        parts = [data[i : i + CHUNK] for i in range(0, len(data), CHUNK)] or [b""]
        for i, part in enumerate(parts):
            self.put(f"job:{job_id}:{i}", part)
        self.put(f"job:{job_id}:meta", json.dumps({"chunks": len(parts)}).encode())


def default_store():
    return RemoteStore() if config.WORKER_URL else LocalStore()


def pack_size(job_dir: Path) -> int:
    """Rough size of what push_job would store (for tests and logging)."""
    return sum(p.stat().st_size for p in job_dir.rglob("*") if p.is_file() and not any(s in p.name for s in SKIP))
