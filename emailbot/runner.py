"""Handle one Slack message inside a GitHub Actions run.

The Cloudflare Worker stores the message in its KV storage and starts a run with only an event
id, so no message text, design or file ever appears in the (possibly public) Actions logs:

  python -m emailbot.runner <event id>

The event (written by worker/src/index.ts):
  {"kind": "new" | "followup", "channel": "C123", "ts": "<thread root ts>",
   "text": "...", "files": [...]}
"""
from __future__ import annotations

import logging
import re
import sys

from slack_sdk import WebClient

from . import config, flows
from .store import RemoteStore

log = logging.getLogger("emailbot")


def main(argv: list[str]) -> int:
    # Keep the public log to stage names: never print message text, designs or file names.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    if len(argv) != 1:
        print("usage: python -m emailbot.runner <event id>")
        return 2
    if not re.fullmatch(r"[0-9a-f-]{36}", argv[0]):
        print("Not an event id")
        return 2
    store = RemoteStore()
    event = store.get_event(argv[0])
    bot = flows.Bot(WebClient(token=config.SLACK_BOT_TOKEN), store, inline=True)
    channel, ts = event["channel"], event["ts"]
    print(f"Handling a '{event['kind']}' event")

    if event["kind"] == "new":
        bot.start(channel, ts, event.get("text", ""), event.get("files") or [])
    elif event["kind"] == "followup":
        job_id = store.get_thread(channel, ts)
        if not job_id:
            print("Thread isn't linked to an email; nothing to do")
            return 0
        bot.follow_up(channel, ts, job_id, event.get("text", ""), event.get("files") or [])
    else:
        print("Unknown event kind")
        return 1
    print("Done")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
