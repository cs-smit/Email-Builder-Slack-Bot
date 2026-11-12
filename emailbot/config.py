"""Settings read from .env (see .env.example)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Your existing email templates, which new emails are built from. Set one of:
#   LIBRARY_DIR: a folder of .html files (searched recursively), or
#   LIBRARY_URL: a website that publishes a manifest listing them (see docs/template-library.md).
LIBRARY_DIR = os.getenv("LIBRARY_DIR", "")
LIBRARY_URL = (os.getenv("LIBRARY_URL", "").rstrip("/") + "/") if os.getenv("LIBRARY_URL") else ""
LIBRARY_MANIFEST = os.getenv("LIBRARY_MANIFEST", "gallery/templates.js")

# Who the emails are for; used in Claude's instructions.
BRAND_NAME = os.getenv("BRAND_NAME", "the brand")

FIGMA_TOKEN = os.getenv("FIGMA_TOKEN", "")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN", "")

# Claude Code CLI runs the HTML work (uses your Claude subscription after `claude login`).
CLAUDE_BIN = os.getenv("CLAUDE_BIN", "claude")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "")  # blank = Claude Code's default
CLAUDE_TIMEOUT = int(os.getenv("CLAUDE_TIMEOUT", "1200"))

# After building, compare screenshots with the design and fix up to this many times.
REVIEW_ROUNDS = int(os.getenv("REVIEW_ROUNDS", "1"))

# Browser for screenshots: "chrome" uses installed Google Chrome; blank uses Playwright's Chromium.
BROWSER_CHANNEL = os.getenv("BROWSER_CHANNEL", "chrome" if sys.platform == "darwin" else "")

# Figma exports images at this scale (2 = retina; the HTML still sets display width).
FIGMA_SCALE = float(os.getenv("FIGMA_SCALE", "2"))

# GitHub Actions mode: the Cloudflare Worker's URL and the secret shared with it.
WORKER_URL = os.getenv("WORKER_URL", "")
RUNNER_SECRET = os.getenv("RUNNER_SECRET", "")

DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
CACHE_DIR = DATA_DIR / "library"
JOBS_DIR = DATA_DIR / "jobs"
for d in (CACHE_DIR, JOBS_DIR):
    d.mkdir(parents=True, exist_ok=True)
