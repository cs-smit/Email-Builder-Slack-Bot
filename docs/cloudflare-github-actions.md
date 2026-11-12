# Run free on Cloudflare Workers + GitHub Actions

No server to look after, and nothing has to stay on. Each Slack message goes to a small
Cloudflare Worker, which answers Slack straight away and starts a GitHub Actions run. The run
builds the email with Claude Code and posts the result back to the Slack thread.

```
Slack ──▶ Cloudflare Worker ──repository_dispatch──▶ GitHub Actions (python -m emailbot.runner)
  ▲         • checks Slack's signature                 • reads the message from the Worker
  │         • replies "On it" within 3 s               • builds the email (Claude Code + Chrome)
  │         • stores the message in KV                 • saves the job back to the Worker
  └──────────────────── posts the screenshot + zip ◀───┘
```

**Cost:** $0 on the free plans, within these limits:

| Service | Free allowance | What one email uses |
|---|---|---|
| GitHub Actions | Public repos: free. Private repos: 2,000 minutes/month | ~12 minutes per build, ~3-5 per change |
| Cloudflare Workers | 100,000 requests/day | a few per message |
| Cloudflare KV | 1,000 writes/day | ~5-10 per build |
| Claude | Your Claude plan's usage limits (or API key billing) | one build ≈ a long Claude Code session |

Check each provider's current pricing; free tiers change.

## Before you start

- A **GitHub** account and your own copy of this repo (fork it, or *Use this template*).
- A free **Cloudflare** account.
- Permission to add apps to your **Slack** workspace.
- A **Claude** subscription (Pro/Max) or an Anthropic API key.
- Node.js 20+ on your computer (for the `wrangler` command).

> **Public or private repo?** In a public repo, anyone can see your Actions runs and logs. This
> project keeps message text, designs and files out of the logs (the run only gets an opaque id
> and reads everything from the Worker), and secrets are masked. For client work you may still
> prefer a **private** copy: logs then stay private, at the cost of the 2,000-minute monthly
> allowance.

## 1. Deploy the Worker

```bash
cd worker
npm install
npx wrangler login                       # opens Cloudflare in your browser
npx wrangler kv namespace create BOT_KV  # prints an id
```

Open `worker/wrangler.toml`:
- set `id` under `[[kv_namespaces]]` to the id it printed;
- set `GITHUB_REPO` to your copy of the repo, e.g. `your-name/Email-Builder-Slack-Bot`.

```bash
npx wrangler deploy
```

It prints the Worker's address, like `https://email-builder.your-subdomain.workers.dev`. Opening
it in a browser should say *Email Builder worker is running.*

## 2. Create the Slack app

1. Open `slack/manifest-cloudflare.yaml`, replace both `YOUR-WORKER.workers.dev` with your
   Worker's address.
2. https://api.slack.com/apps → **Create New App** → **From a manifest** → pick the workspace →
   paste it → **Create**. (Slack may say the events URL isn't verified yet; that's fixed in step 4.)
3. **Basic Information → App Credentials → Signing Secret** → copy it, then:
   ```bash
   npx wrangler secret put SLACK_SIGNING_SECRET
   ```
4. **Install App → Install to Workspace → Allow**. Copy the **Bot User OAuth Token** (`xoxb-…`):
   ```bash
   npx wrangler secret put SLACK_BOT_TOKEN
   ```
5. **Event Subscriptions** → next to the Request URL, click **Retry** → it should say
   **Verified**. Save.

## 3. Connect the Worker to GitHub

1. **A GitHub token for the Worker.** GitHub → Settings → Developer settings → **Fine-grained
   tokens** → Generate. *Repository access:* only your copy of this repo. *Permissions:*
   **Contents: Read and write** (needed to start a run). Then:
   ```bash
   npx wrangler secret put GITHUB_TOKEN
   ```
2. **A shared secret** between the Worker and the runs. Generate one:
   ```bash
   openssl rand -hex 32
   ```
   Give it to the Worker (and keep it for step 4):
   ```bash
   npx wrangler secret put RUNNER_SECRET
   ```

## 4. Add the GitHub Actions secrets

In your repo: **Settings → Secrets and variables → Actions → New repository secret**.

| Secret | Value |
|---|---|
| `SLACK_BOT_TOKEN` | the `xoxb-…` token from step 2 |
| `CLAUDE_CODE_OAUTH_TOKEN` | run `claude setup-token` on your computer and paste the token (uses your Claude plan) |
| `ANTHROPIC_API_KEY` | *instead of the line above:* an API key from console.anthropic.com |
| `WORKER_URL` | your Worker's address from step 1 |
| `RUNNER_SECRET` | the same value as in step 3 |
| `LIBRARY_URL` | your template library website (see [template-library.md](template-library.md)) |
| `BRAND_NAME` | who the emails are for, e.g. your client's name |
| `FIGMA_TOKEN` | optional, for Figma links (see README) |

Using a folder of templates instead of a website? Commit them to a `templates/` folder in a
**private** copy of the repo and add a repository *variable* (Variables tab) `LIBRARY_DIR` =
`templates`.

Optional variables: `REVIEW_ROUNDS` (default 1), `LIBRARY_MANIFEST` (default `gallery/templates.js`).

## 5. Try it

In Slack, `/invite @Email Builder` in a channel, then either:

- `/email <Figma link to an email frame>`, or
- `@Email Builder` with the email exported from Figma as PNG (plus its images) attached.

The bot replies *On it* within a second. In GitHub → **Actions** you'll see a *Build email* run
start; about 10-15 minutes later the screenshot and zip appear in the Slack thread.

## Troubleshooting

| What you see | What to check |
|---|---|
| Slack says the Request URL failed | `SLACK_SIGNING_SECRET` on the Worker; then Retry in Event Subscriptions |
| No reply at all to `@Email Builder` | The bot is in the channel (`/invite`); the app has the `message.channels` event |
| *I couldn't start the build (GitHub answered 401/403/404)* | `GITHUB_TOKEN` (Contents: Read and write, on this repo) and `GITHUB_REPO` in `wrangler.toml` |
| A run fails at *Build* | The run's log names the step; check the Actions secrets above, especially `WORKER_URL`/`RUNNER_SECRET` |
| *Something went wrong* in Slack | Same run's log shows the error type; Claude limits or an expired `CLAUDE_CODE_OAUTH_TOKEN` are the usual causes |

Live Worker logs: `cd worker && npx wrangler tail`.

## How state is kept

GitHub Actions machines start empty every time, so between messages everything lives in the
Worker's KV storage, behind `RUNNER_SECRET`:

- `event:<id>`: the Slack message waiting for its run (7 days).
- `thread:<channel>:<ts>`: which email a Slack thread belongs to (60 days).
- `job:<id>:*`: the email's working folder, packed (60 days). Changes and the final S3 step
  restore it, update it and save it again.
