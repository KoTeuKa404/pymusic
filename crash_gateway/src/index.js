// Cloudflare Worker: opt-in anonymous Android crash reports -> GitHub Issues.
// GITHUB_TOKEN is a Cloudflare secret, NEVER packaged in the APK or Pages.
const OWNER = "KoTeuKa404";
const REPO = "pymusic";
const ORIGIN = "https://koteuka404.github.io";
const MAX_PAYLOAD = 16000;
const REFERENCE = /^https:\/\/github\.com\/KoTeuKa404\/pymusic\/issues\/[0-9]+$/;

function reply(body, status = 200, origin = "") {
  const headers = {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  };
  if (origin === ORIGIN) {
    headers["Access-Control-Allow-Origin"] = ORIGIN;
    headers["Access-Control-Allow-Methods"] = "POST, OPTIONS, GET";
    headers["Access-Control-Allow-Headers"] = "content-type";
    headers["Vary"] = "Origin";
  }
  return new Response(JSON.stringify(body), { status, headers });
}

function clean(value, max) {
  let text = String(value ?? "").replace(/\0/g, "").slice(0, max);
  text = text.replace(/https?:\/\/[^\s<>"']+/gi, "[URL]");
  text = text.replace(/[\w.+-]+@[\w.-]+\.[a-z]{2,}/gi, "[EMAIL]");
  text = text.replace(/\b(?:\d{1,3}\.){3}\d{1,3}\b/g, "[IP]");
  text = text.replace(/(?:\/storage\/emulated\/\d+|\/sdcard|\/data\/(?:user|data)\/\d*|\/home\/[\w.-]+|\/Users\/[\w.-]+|C:\\Users\\[^\s\\]+)[^\s"']*/gi, "[PRIVATE_PATH]");
  text = text.replace(/\b[a-f0-9]{32,}\b/gi, "[HEX_ID]");
  text = text.replace(/\b(?:authorization|cookie|password|passwd|secret|access_token|refresh_token|client_secret|api_key)\s*[:=]\s*[^\s,;]+/gi, "[REDACTED]");
  text = text.replace(/\b(?:bearer|basic)\s+[A-Za-z0-9+/_.=-]{8,}/gi, "[AUTH]");
  return text.split("\n").map(line => {
    if (/\b(?:cookie|password|bearer|authorization|oauth|secret|token|signature)\b/i.test(line)) {
      return "[SENSITIVE_LINE_REMOVED]";
    }
    return line.slice(0, 320);
  }).join("\n");
}

function accepted(data) {
  if (!data || typeof data !== "object" || Array.isArray(data)) return false;
  if (data.schema !== 1 || !["crash", "diagnostic"].includes(data.kind)) return false;
  if (typeof data.details !== "string" || typeof data.summary !== "string") return false;
  if (data.details.length > 11000 || data.summary.length > 240) return false;
  if (data.details.length < 10) return false;
  return true;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = request.headers.get("Origin") || "";
    if (origin && origin !== ORIGIN) return reply({ error: "origin_not_allowed" }, 403);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: origin === ORIGIN ? { "Access-Control-Allow-Origin": ORIGIN, "Access-Control-Allow-Methods": "POST, OPTIONS, GET", "Access-Control-Allow-Headers": "content-type", "Vary": "Origin" } : {} });
    if (request.method === "GET" && url.pathname === "/health") {
      return reply({ ready: Boolean(env.GITHUB_TOKEN && env.REPORT_LIMITER) }, 200, origin);
    }
    if (url.pathname !== "/report") return reply({ error: "not_found" }, 404, origin);
    if (request.method !== "POST") return reply({ error: "method_not_allowed" }, 405, origin);

    // Fail closed if a deployment omitted its limiter or GitHub secret.
    if (!env.GITHUB_TOKEN || !env.REPORT_LIMITER) {
      return reply({ error: "report_gateway_not_configured" }, 503, origin);
    }
    const type = (request.headers.get("Content-Type") || "").split(";")[0].trim().toLowerCase();
    if (type !== "application/json") return reply({ error: "invalid_content_type" }, 415, origin);
    const headerSize = Number(request.headers.get("content-length") || 0);
    if (headerSize > MAX_PAYLOAD) return reply({ error: "payload_too_large" }, 413, origin);

    // Cloudflare rate limits are per POP and approximate: enable WAF bot/rate
    // rules in the Cloudflare dashboard for public production deployments.
    const key = request.headers.get("CF-Connecting-IP") || "unknown";
    const limited = await env.REPORT_LIMITER.limit({ key: "crash:" + key });
    if (!limited.success) return reply({ error: "rate_limited" }, 429, origin);
    const raw = await request.text();
    if (new TextEncoder().encode(raw).length > MAX_PAYLOAD) {
      return reply({ error: "payload_too_large" }, 413, origin);
    }

    let data;
    try { data = JSON.parse(raw); } catch { return reply({ error: "invalid_json" }, 400, origin); }
    if (!accepted(data)) return reply({ error: "invalid_report" }, 422, origin);

    const summary = clean(data.summary, 240).replace(/[\r\n]+/g, " ").slice(0, 160);
    const details = clean(data.details, 11000);
    const date = String(data.created_at || "").slice(0, 40).replace(/[^-:0-9 UTC]/g, "");
    const title = "[PyMusic Crash] " + (data.kind === "crash" ? "App crash" : "Manual diagnostics")
      + " · " + date.slice(0, 10);
    const body = [
      "## PyMusic crash report",
      "",
      "> Submitted by an explicit tap in the PyMusic app. Public GitHub Issue.",
      "",
      "**Type:** " + data.kind,
      "**Time (UTC):** " + date,
      "**Summary:** " + summary,
      "",
      "### Redacted diagnostics",
      "",
      "~~~text",
      details.replace(/~~~/g, "[FENCE]"),
      "~~~",
      "",
      "_Diagnostic lines are filtered client-side and on the server. "
        + "No account credentials or raw stream URLs are intentionally transmitted._",
    ].join("\n");

    async function create(labels) {
      return fetch("https://api.github.com/repos/" + OWNER + "/" + REPO + "/issues", {
        method: "POST",
        headers: {
          "Accept": "application/vnd.github+json",
          "Authorization": "Bearer " + env.GITHUB_TOKEN,
          "X-GitHub-Api-Version": "2022-11-28",
          "Content-Type": "application/json",
          "User-Agent": "PyMusic-Crash-Gateway",
        },
        body: JSON.stringify({ title, body, labels }),
      });
    }

    try {
      let response = await create(["crash-report"]);
      if (response.status === 422) {
        // New repositories may not yet have a crash-report label.
        response = await create([]);
      }
      if (response.status !== 201) {
        // Never expose the GitHub API response or token to the Android user.
        return reply({ error: "github_submission_failed" }, 502, origin);
      }
      const issue = await response.json();
      if (!REFERENCE.test(String(issue.html_url || ""))) {
        return reply({ error: "unexpected_issue_url" }, 502, origin);
      }
      return reply({ ok: true, issue_url: issue.html_url, issue_number: issue.number }, 201, origin);
    } catch {
      return reply({ error: "upstream_unavailable" }, 502, origin);
    }
  },
};
