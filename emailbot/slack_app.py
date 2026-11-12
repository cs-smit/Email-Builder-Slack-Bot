"""Run the bot as one long-lived process (Socket Mode): on a Mac, or any server.

No public URL is needed; the process connects out to Slack. See README → "Run on your machine".

In Slack:
  /email <figma link> [notes]      start a new email (or @mention the bot with a Figma link or files)
  reply in the thread with changes  the bot edits the email and sends the new version
  reply with the S3 folder URL      the bot swaps in the S3 image links and sends the final zip
"""
from __future__ import annotations

import logging
import threading

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from . import config, flows, library
from .store import LocalStore

log = logging.getLogger("emailbot")

app = App(token=config.SLACK_BOT_TOKEN or "xoxb-not-set", token_verification_enabled=False)
bot = flows.Bot(app.client, LocalStore(), inline=False)
BOT_USER_ID = ""
_seen: set[tuple[str, str]] = set()
_seen_lock = threading.Lock()


@app.command("/email")
def on_command(ack, command, respond):
    ack()
    text = command.get("text", "")
    links = flows.figma_links(text)
    if not links:
        respond(flows.USAGE)
        return
    channel = command["channel_id"]
    notes = flows.strip_urls(text)
    try:
        msg = app.client.chat_postMessage(
            channel=channel,
            text=f"<@{command['user_id']}> asked for a new email from <{links[0]}|this Figma link>"
            + (f"\n> {notes}" if notes else "")
            + "\nI'll work in this thread.",
            unfurl_links=False,
        )
    except Exception as e:
        if "not_in_channel" in str(e) or "channel_not_found" in str(e):
            respond("I'm not in this channel yet. Add me with `/invite @<bot name>` and try again.")
            return
        raise
    bot.start_from_link(channel, msg["ts"], links[0], notes)


def handle(event: dict, mentioned: bool):
    if event.get("bot_id") or event.get("subtype") not in (None, "file_share"):
        return
    channel = event["channel"]
    with _seen_lock:  # Slack sends a message with an @mention twice (message + app_mention)
        key = (channel, event["ts"])
        if key in _seen:
            return
        _seen.add(key)
    text, files = event.get("text", ""), event.get("files") or []
    thread = event.get("thread_ts")
    job_id = bot.store.get_thread(channel, thread) if thread else None
    if job_id:
        bot.follow_up(channel, thread, job_id, text, files)
        return
    if not mentioned and event.get("channel_type") != "im":
        return  # in channels, only act when @mentioned (or in our own threads)
    ts = thread or event["ts"]
    if flows.figma_links(text) or files:
        bot.say(channel, ts, "On it. I'll post updates in this thread.")
    bot.start(channel, ts, text, files)


@app.event("app_mention")
def on_mention(event):
    handle(event, mentioned=True)


@app.event("message")
def on_message(event):
    handle(event, mentioned=bool(BOT_USER_ID and f"<@{BOT_USER_ID}>" in event.get("text", "")))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not (config.SLACK_BOT_TOKEN and config.SLACK_APP_TOKEN):
        raise SystemExit("Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN in .env (see README).")
    if not config.FIGMA_TOKEN:
        log.warning("FIGMA_TOKEN is not set: only uploaded designs will work, not Figma links.")
    try:
        who = app.client.auth_test()
    except Exception as e:
        raise SystemExit(f"Slack rejected SLACK_BOT_TOKEN: {e}")
    global BOT_USER_ID
    BOT_USER_ID = who["user_id"]
    log.info("Connected to Slack as %s in %s", who["user"], who["team"])
    library.sync(log=log.info)
    log.info("Email bot is running. Press Ctrl+C to stop.")
    SocketModeHandler(app, config.SLACK_APP_TOKEN).start()


if __name__ == "__main__":
    main()
