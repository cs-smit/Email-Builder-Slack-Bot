/**
 * Email Builder: Cloudflare Worker front end.
 *
 * Slack needs an answer within 3 seconds, but building an email takes ~10 minutes. So this
 * Worker answers Slack right away, saves the message in KV, and starts a GitHub Actions run
 * (repository_dispatch) carrying only an opaque event id. The run (python -m emailbot.runner)
 * reads the message and stores its state back here through /internal/kv, authenticated with
 * RUNNER_SECRET. Message text and designs never appear in the Actions logs.
 *
 * Routes
 *   POST /slack/events      Slack Events API (message events)
 *   POST /slack/commands    the /email slash command
 *   GET|PUT /internal/kv/:key   storage for the runner (Bearer RUNNER_SECRET)
 */

export interface Env {
  BOT_KV: KVNamespace;
  SLACK_SIGNING_SECRET: string;
  SLACK_BOT_TOKEN: string;
  GITHUB_TOKEN: string;
  RUNNER_SECRET: string;
  GITHUB_REPO: string; // "owner/name"
  GITHUB_EVENT_TYPE?: string;
  GITHUB_API?: string; // overridable for local testing
  SLACK_API?: string; // overridable for local testing
}

const FIGMA = /figma\.com\/(?:design|file|proto)\//i;
const URL_RE = /<(https?:\/\/[^>|]+)(?:\|[^>]*)?>|(https?:\/\/\S+)/g;
const EVENT_TTL = 60 * 60 * 24 * 7; // events only need to live until the run picks them up
const THREAD_TTL = 60 * 60 * 24 * 60; // matches the runner's job retention
const MAX_KV_TTL_MIN = 60; // KV's minimum expirationTtl

const HELP =
  "Two ways to start an email:\n" +
  "• `/email <Figma link> [notes]` — reads the design straight from Figma\n" +
  "• @mention me and attach the email exported from Figma as PNG/JPG, plus its images " +
  "(or one .zip of them). The biggest picture is taken as the design. Add notes in the same message.";
const USAGE =
  "Usage: `/email <Figma link to the email frame> [notes, e.g. base it on welcome.html]`\n" +
  "No Figma access? @mention me in a channel and attach the exported design (PNG/JPG) and its images.";

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    try {
      if (url.pathname.startsWith("/internal/kv/")) return await internalKv(request, env, url);
      if (request.method === "POST" && url.pathname === "/slack/events") return await slackEvents(request, env, ctx);
      if (request.method === "POST" && url.pathname === "/slack/commands") return await slackCommand(request, env, ctx);
      if (url.pathname === "/") return new Response("Email Builder worker is running.");
      return new Response("Not found", { status: 404 });
    } catch (err) {
      console.error("request failed:", (err as Error).name);
      return new Response("Server error", { status: 500 });
    }
  },
};

// ---------------------------------------------------------------- Slack request checks

async function verifySlack(request: Request, env: Env, body: string): Promise<boolean> {
  const ts = request.headers.get("x-slack-request-timestamp") ?? "";
  const sig = request.headers.get("x-slack-signature") ?? "";
  if (!ts || !sig || Math.abs(Date.now() / 1000 - Number(ts)) > 60 * 5) return false;
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(env.SLACK_SIGNING_SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`v0:${ts}:${body}`));
  const expected = "v0=" + [...new Uint8Array(mac)].map((b) => b.toString(16).padStart(2, "0")).join("");
  return timingSafeEqual(expected, sig);
}

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

// ---------------------------------------------------------------- Slack events

interface SlackFile {
  name?: string;
  url_private?: string;
  url_private_download?: string;
  size?: number;
  mimetype?: string;
}

interface MessageEvent {
  type: string;
  subtype?: string;
  bot_id?: string;
  channel: string;
  channel_type?: string;
  ts: string;
  thread_ts?: string;
  text?: string;
  files?: SlackFile[];
}

async function slackEvents(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  const body = await request.text();
  if (!(await verifySlack(request, env, body))) return new Response("Bad signature", { status: 401 });
  const payload = JSON.parse(body);

  if (payload.type === "url_verification") return Response.json({ challenge: payload.challenge });
  // Slack retries when we're slow; the first delivery already started the work.
  if (request.headers.get("x-slack-retry-num")) return new Response("");
  if (payload.type !== "event_callback") return new Response("");

  const event = payload.event as MessageEvent;
  // Only "message" events: an @mention also arrives as app_mention, which would double up.
  if (event.type !== "message" || event.bot_id || (event.subtype && event.subtype !== "file_share")) {
    return new Response("");
  }
  const botUserId: string | undefined = payload.authorizations?.[0]?.user_id;
  ctx.waitUntil(handleMessage(env, event, botUserId).catch((e) => console.error("message failed:", e?.name)));
  return new Response("");
}

async function handleMessage(env: Env, event: MessageEvent, botUserId?: string): Promise<void> {
  const text = event.text ?? "";
  const files = (event.files ?? []).map(slimFile);
  const thread = event.thread_ts;

  if (thread && (await env.BOT_KV.get(threadKey(event.channel, thread)))) {
    await enqueue(env, { kind: "followup", channel: event.channel, ts: thread, text, files });
    return;
  }
  const mentioned = !!botUserId && text.includes(`<@${botUserId}>`);
  if (!mentioned && event.channel_type !== "im") return; // in channels, only when @mentioned

  const root = thread ?? event.ts;
  if (!figmaLinks(text).length && !files.length) {
    await say(env, event.channel, root, HELP);
    return;
  }
  await say(env, event.channel, root, "On it. I'll post updates in this thread.");
  await env.BOT_KV.put(threadKey(event.channel, root), "pending", { expirationTtl: THREAD_TTL });
  await enqueue(env, { kind: "new", channel: event.channel, ts: root, text, files });
}

// ---------------------------------------------------------------- /email slash command

async function slackCommand(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  const body = await request.text();
  if (!(await verifySlack(request, env, body))) return new Response("Bad signature", { status: 401 });
  const form = new URLSearchParams(body);
  const text = form.get("text") ?? "";
  const links = figmaLinks(text);
  if (!links.length) return Response.json({ response_type: "ephemeral", text: USAGE });

  const channel = form.get("channel_id") ?? "";
  const user = form.get("user_id") ?? "";
  const responseUrl = form.get("response_url") ?? "";
  ctx.waitUntil(
    (async () => {
      const notes = stripUrls(text);
      const posted = await slack(env, "chat.postMessage", {
        channel,
        text: `<@${user}> asked for a new email from <${links[0]}|this Figma link>` +
          (notes ? `\n> ${notes}` : "") + "\nI'll work in this thread.",
        unfurl_links: false,
      });
      if (!posted.ok) {
        const msg = posted.error === "not_in_channel" || posted.error === "channel_not_found"
          ? "I'm not in this channel yet. Add me with `/invite @<bot name>` and try again."
          : `I couldn't post in this channel (${posted.error}).`;
        if (responseUrl) await fetch(responseUrl, { method: "POST", body: JSON.stringify({ response_type: "ephemeral", text: msg }) });
        return;
      }
      await env.BOT_KV.put(threadKey(channel, posted.ts), "pending", { expirationTtl: THREAD_TTL });
      await enqueue(env, { kind: "new", channel, ts: posted.ts, text, files: [] });
    })().catch((e) => console.error("command failed:", e?.name)),
  );
  return new Response("");
}

// ---------------------------------------------------------------- hand-off to GitHub Actions

interface QueuedEvent {
  kind: "new" | "followup";
  channel: string;
  ts: string;
  text: string;
  files: SlackFile[];
}

async function enqueue(env: Env, ev: QueuedEvent): Promise<void> {
  const id = crypto.randomUUID();
  await env.BOT_KV.put(`event:${id}`, JSON.stringify(ev), { expirationTtl: EVENT_TTL });
  // Runs for the same thread queue up instead of editing the same email at once. The group
  // name is a hash so channel ids don't show in the public Actions UI.
  const thread = (await sha256(`${ev.channel}:${ev.ts}`)).slice(0, 16);
  const api = env.GITHUB_API ?? "https://api.github.com";
  const res = await fetch(`${api}/repos/${env.GITHUB_REPO}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "email-builder-worker",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify({ event_type: env.GITHUB_EVENT_TYPE ?? "email-builder", client_payload: { id, thread } }),
  });
  if (!res.ok) {
    await say(env, ev.channel, ev.ts,
      `:x: I couldn't start the build (GitHub answered ${res.status}). Check GITHUB_TOKEN and GITHUB_REPO on the Worker.`);
  }
}

// ---------------------------------------------------------------- storage for the runner

async function internalKv(request: Request, env: Env, url: URL): Promise<Response> {
  const auth = request.headers.get("authorization") ?? "";
  if (!env.RUNNER_SECRET || !timingSafeEqual(auth, `Bearer ${env.RUNNER_SECRET}`)) {
    return new Response("Unauthorized", { status: 401 });
  }
  const key = decodeURIComponent(url.pathname.slice("/internal/kv/".length));
  if (!/^(event|thread|job):[A-Za-z0-9:._-]{1,400}$/.test(key)) return new Response("Bad key", { status: 400 });

  if (request.method === "GET") {
    const value = await env.BOT_KV.get(key, "arrayBuffer");
    return value === null ? new Response("Not found", { status: 404 }) : new Response(value);
  }
  if (request.method === "PUT") {
    const ttl = Math.max(MAX_KV_TTL_MIN, Number(url.searchParams.get("ttl") ?? THREAD_TTL) || THREAD_TTL);
    await env.BOT_KV.put(key, await request.arrayBuffer(), { expirationTtl: ttl });
    return new Response("ok");
  }
  return new Response("Method not allowed", { status: 405 });
}

// ---------------------------------------------------------------- helpers

async function slack(env: Env, method: string, body: Record<string, unknown>): Promise<any> {
  const api = env.SLACK_API ?? "https://slack.com/api";
  const res = await fetch(`${api}/${method}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${env.SLACK_BOT_TOKEN}`, "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(body),
  });
  return res.json();
}

async function say(env: Env, channel: string, threadTs: string, text: string): Promise<void> {
  await slack(env, "chat.postMessage", { channel, thread_ts: threadTs, text, unfurl_links: false });
}

function threadKey(channel: string, ts: string): string {
  return `thread:${channel}:${ts}`;
}

function urlsIn(text: string): string[] {
  return [...text.matchAll(URL_RE)].map((m) => m[1] ?? m[2].replace(/[>.,)]+$/, ""));
}

function figmaLinks(text: string): string[] {
  return urlsIn(text).filter((u) => FIGMA.test(u));
}

function stripUrls(text: string): string {
  return text.replace(URL_RE, "").replace(/<@[A-Z0-9]+>/g, "").trim();
}

function slimFile(f: SlackFile): SlackFile {
  return { name: f.name, url_private: f.url_private, url_private_download: f.url_private_download, size: f.size, mimetype: f.mimetype };
}

async function sha256(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
