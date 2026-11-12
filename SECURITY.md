# Security

## Reporting a problem

Please report security issues privately through GitHub: **Security → Report a vulnerability**
on this repository. Don't open a public issue for them.

## How the bot handles secrets and data

- **Secrets** (Slack, Figma, Claude, GitHub, `RUNNER_SECRET`) live only in `.env` (git-ignored),
  Cloudflare Worker secrets, or GitHub Actions secrets. Never commit them.
- **Slack requests** to the Worker are checked against your Slack signing secret and rejected if
  older than 5 minutes.
- **The Worker's storage endpoint** (`/internal/kv/…`) requires `RUNNER_SECRET` and only accepts
  the bot's own key prefixes.
- **GitHub Actions logs** never get message text, designs or file names: a run receives only an
  opaque event id, prints stage names only, and reports errors by type. Secrets are masked by
  GitHub. In a public repo, run history is still visible; use a private copy if that matters.
- **Uploaded zips** are unpacked as images only (no paths outside the job folder); cut-out file
  names are reduced to safe names inside `images/`.
- **Claude Code** runs inside the job folder with only file tools allowed (Read, Write, Edit,
  Glob, Grep); shell and web tools are explicitly blocked, since design text and Slack messages
  end up in its instructions.
- **Job data** (designs, generated emails) is kept for 60 days in the Worker's KV, or in
  `data/jobs/` locally. Delete it whenever you like.
