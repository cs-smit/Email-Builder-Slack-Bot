# Run on an Oracle Cloud Always Free server

The bot runs on a free Oracle server 24/7, so your computer can be off. This is the Socket Mode
version (`python -m emailbot.slack_app`): set up the Slack app and `.env` first as in the README
("Run on your own machine"), then move it here. It connects out to Slack, so no ports or domain
are needed. Takes about 30-60 minutes the first time.

## 1. Create the Oracle account

1. Sign up at https://www.oracle.com/cloud/free/.
2. **Home region:** pick one near you (for India: *India West (Mumbai)* or *India South
   (Hyderabad)*; elsewhere, your nearest). It can't be changed later, and the free server must be in it.
3. Oracle asks for a card to verify you. Always Free resources aren't charged.

## 2. Create the server

In the Oracle console: **☰ → Compute → Instances → Create instance**.

1. **Name:** `emailbot`.
2. **Image and shape → Edit:**
   - Image: **Canonical Ubuntu 24.04** (Change image → Ubuntu).
   - Shape: **Change shape → Ampere → VM.Standard.A1.Flex**, **2 OCPUs**, **12 GB memory**
     (marked "Always Free-eligible").
3. **Networking:** leave the defaults (new virtual cloud network, public subnet, *Assign a public
   IPv4 address* on).
4. **Add SSH keys:** choose **Generate a key pair for me** → **Save private key**. Keep that
   file safe; it's the only way into the server.
5. **Create.** When it shows *Running*, copy the **Public IP address**.

> **"Out of capacity for shape VM.Standard.A1.Flex"** is common on free accounts. Try another
> *Availability domain* in the same form, or try again in a few hours.

## 3. Get your Claude token (on your Mac)

In Terminal on your Mac:

```bash
claude setup-token
```

Sign in with your Claude account in the browser. It prints a long token. Open the bot's `.env`
file and add it as `CLAUDE_CODE_OAUTH_TOKEN=<token>`. Don't paste it into Slack or
chat; it works like a password. (Builds count against your Claude plan's usage, same as on your
Mac.)

## 4. Copy the bot to the server (on your Mac)

In Terminal, `cd` into the bot's folder, then (replace the key path and IP with yours):

```bash
KEY=~/Downloads/ssh-key-2026-11-09.key
IP=123.45.67.89
chmod 600 "$KEY"
rsync -av -e "ssh -i $KEY" --exclude .venv --exclude data --exclude __pycache__ --exclude worker/node_modules ./ ubuntu@$IP:~/emailbot/
```

The first time, type `yes` when it asks to trust the server. This copies the code and `.env`
(your tokens) over SSH, encrypted.

## 5. Install and start (on the server)

```bash
ssh -i "$KEY" ubuntu@$IP
```

Then, on the server:

```bash
cd ~/emailbot && bash deploy/setup-server.sh
```

It installs Python, Node, Claude Code and Chromium, checks Claude works, and starts the bot as a
service (`emailbot`). It ends by showing the service status: look for `active (running)`.

## 6. Stop any other copy

If the bot also runs on your computer, stop it (**Ctrl+C**). Only one copy may run at a time;
with two, Slack sends each message to either one.

## Everyday commands (on the server)

```bash
sudo journalctl -u emailbot -f        # live log (Ctrl+C to leave)
sudo systemctl restart emailbot       # restart
sudo systemctl status emailbot        # is it running?
```

**Updating the bot** after changes on your Mac: run the `rsync` command from step 4 again, then
`ssh` in and `sudo systemctl restart emailbot`.

**If your Claude token stops working** (expired or revoked): run `claude setup-token` on your Mac
again, update `CLAUDE_CODE_OAUTH_TOKEN` in the server's `~/emailbot/.env`, and restart.
