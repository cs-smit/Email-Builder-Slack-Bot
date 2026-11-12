# Email Builder: a Slack bot that codes HTML emails from Figma

Send a Figma design to a Slack bot and get back a coded, client-ready HTML email: real
table-based HTML with live text, built from the closest email in your own template library,
checked against the design, and packaged with its images.

[![Tests](https://github.com/cs-smit/Email-Builder-Slack-Bot/actions/workflows/tests.yml/badge.svg)](https://github.com/cs-smit/Email-Builder-Slack-Bot/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## What it does

1. **You start it in Slack:** `/email <Figma link>`, or `@Email Builder` with the design
   exported from Figma as a PNG (plus its pictures) attached.
2. **It finds your closest existing email.** It compares the design's text with every template
   in your library and gives the four best to Claude, which picks the closest layout.
3. **Claude codes the email** by adapting that template: tables, inline styles, bulletproof
   buttons, mobile media queries, Outlook fallbacks and your merge tags (`{{unsubscribe}}` etc.)
   kept as they are. Text is live HTML, never a picture of the design.
4. **Images:** exported from Figma, or cut out of the uploaded design automatically (logos,
   badges, graphics). A photo with text laid over it gets a labelled placeholder until you
   attach it.
5. **It checks its own work:** screenshots the email on desktop and a phone, compares it with the
   design and fixes spacing, sizes, fonts and anything too wide on mobile.
6. **It posts a screenshot and a zip** (HTML + `images/`) in the thread. Reply with changes in
   plain words ("make the headline 28px") or attach new images; it edits and posts again.
7. **Final:** upload the images to your S3 bucket/CDN and reply with the folder URL. It swaps the
   links into the HTML, checks every image loads, and posts the client zip.

It never sends emails or uploads anything itself; everything comes back to the Slack thread.

## How to run it

| | Cost | Always on | Claude | Guide |
|---|---|---|---|---|
| **Cloudflare Workers + GitHub Actions** | Free tiers | Yes | Subscription token or API key | [docs/cloudflare-github-actions.md](docs/cloudflare-github-actions.md) |
| **Your own machine** (Mac/Linux) | Free | While it's on | Your `claude login` | below |
| **A server** (e.g. Oracle Cloud Always Free) | Free / VPS price | Yes | Subscription token or API key | [docs/oracle-cloud.md](docs/oracle-cloud.md) |

All three need a [template library](docs/template-library.md): a folder of your existing HTML
emails, or a website that lists them.

## Run on your own machine

Requirements: Python 3.11+, [Claude Code](https://docs.anthropic.com/en/docs/claude-code) logged
in (`claude login`), and Google Chrome (or run `python -m playwright install chromium`).

```bash
git clone https://github.com/cs-smit/Email-Builder-Slack-Bot.git
cd Email-Builder-Slack-Bot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
```

**Slack app:** https://api.slack.com/apps → **Create New App** → **From a manifest** → paste
[`slack/manifest-socket-mode.yaml`](slack/manifest-socket-mode.yaml). Then:
- **Basic Information → App-Level Tokens → Generate** with scope `connections:write` → put the
  `xapp-…` token in `.env` as `SLACK_APP_TOKEN`.
- **Install App** → put the **Bot User OAuth Token** (`xoxb-…`) in `.env` as `SLACK_BOT_TOKEN`.

**Templates:** set `LIBRARY_DIR` (a folder) or `LIBRARY_URL` (a website) in `.env`, plus
`BRAND_NAME`. See [docs/template-library.md](docs/template-library.md).

**Figma (optional):** for Figma links, create a personal access token (Figma → Settings →
Security → *File content: Read-only*) and set `FIGMA_TOKEN`. It must belong to an account with a
**Dev or Full seat**: View/Collab seats get only a few API calls a month. Without it, upload the
exported design instead.

```bash
.venv/bin/python -m emailbot.slack_app
```

In Slack, `/invite @Email Builder` in a channel and send it a design.

## Using it in Slack

- **From Figma:** `/email <link> [notes]`. In Figma, select the email frame → right-click →
  **Copy link to selection**. A link to a section builds every email inside it, each in its own
  thread.
- **From exported files:** `@Email Builder` with the email exported as PNG/JPG (2x is best) plus
  its photos, icons and logos, or one .zip. The biggest picture is taken as the design.
- **Notes** go in the same message: `base it on welcome.html`, `CTA goes to https://…`.
- **Changes:** reply in the thread. Attach images to fill placeholders.
- **Final:** reply with the S3/CloudFront folder URL (or any one image URL). For another CDN,
  write `final <url>`.

A build takes about 10-15 minutes.

## Without Slack

```bash
.venv/bin/python -m emailbot.cli new "<figma link>" "notes"
.venv/bin/python -m emailbot.cli change <job id> "make the button green"
.venv/bin/python -m emailbot.cli final <job id> https://cdn.example.com/emails/welcome/
.venv/bin/python -m emailbot.cli match "text from the design"   # which templates it would pick
```

Each job's files are in `data/jobs/<job id>/`.

## Configuration

All settings are environment variables (`.env` locally, secrets/variables in GitHub Actions).
See [`.env.example`](.env.example) for the full list. The main ones:

| Variable | Purpose |
|---|---|
| `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` | Slack bot token; app token for Socket Mode only |
| `LIBRARY_DIR` / `LIBRARY_URL` | where your existing templates are |
| `BRAND_NAME` | who the emails are for |
| `FIGMA_TOKEN` | Figma links (optional) |
| `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY` | Claude on servers/CI (`claude login` is enough locally) |
| `REVIEW_ROUNDS` | compare-and-fix rounds after a build (default 1) |

## Project layout

```
emailbot/
  flows.py       what the bot does in Slack (shared by both ways of running)
  slack_app.py   Socket Mode process for your own machine/server
  runner.py      one GitHub Actions run per Slack message
  pipeline.py    build / revise / finalize steps
  figma.py       read a Figma frame, export its images
  uploads.py     build from exported files instead of the Figma API
  library.py     your template library, and picking the closest templates
  generator.py   Claude Code prompts (build, QA review, revise)
  render.py      screenshots, placeholders, cutting pictures out of the design
  package.py     checks, zips, swapping in hosted image URLs
  store.py       state on disk, or in the Worker's KV for GitHub Actions
worker/          Cloudflare Worker (TypeScript) receiving Slack messages
slack/           Slack app manifests
docs/            setup guides
deploy/          server install script
tests/           pytest suite
```

## Privacy

Designs, emails and Slack messages are sent to Claude (through Claude Code) to build the email,
and to Figma's API when you use Figma links. Job files stay on the machine running the bot, or
in your own Cloudflare KV namespace. Nothing is uploaded anywhere else. See
[SECURITY.md](SECURITY.md).

## Contributing

Issues and pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
