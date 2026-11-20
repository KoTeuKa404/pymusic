import test from "node:test";
import assert from "node:assert/strict";
import gateway from "../src/index.js";

const good = {
  schema: 1,
  kind: "crash",
  summary: "Kivy Python exception",
  details: "Traceback (most recent call last):\nValueError: simulated crash",
  created_at: "2026-11-20 16:00 UTC",
};
function env(overrides={}) {
  return {
    GITHUB_TOKEN: "private-token-must-not-leak",
    REPORT_LIMITER: { limit: async () => ({ success: true }) },
    ...overrides,
  };
}
const request = (body=good, options={}) => new Request("https://worker.example/report", {
  method: "POST",
  headers: { "Content-Type": "application/json", ...options.headers },
  body: JSON.stringify(body),
});

test("fails closed if secret or limiter is missing", async () => {
  assert.equal((await gateway.fetch(request(), env({ GITHUB_TOKEN: "" }))).status, 503);
  assert.equal((await gateway.fetch(request(), env({ REPORT_LIMITER: null }))).status, 503);
});

test("rejects excessive, invalid or hostile requests", async () => {
  assert.equal((await gateway.fetch(request({ ...good, details: "x".repeat(12000) }), env())).status, 422);
  assert.equal((await gateway.fetch(request(good, { headers: { Origin: "https://evil.example" }}), env())).status, 403);
  assert.equal((await gateway.fetch(request(), env({ REPORT_LIMITER: { limit: async()=>({success:false}) } }))).status, 429);
  const options = new Request("https://worker.example/report", {
    method: "OPTIONS",
    headers: { Origin: "https://koteuka404.github.io" },
  });
  assert.equal((await gateway.fetch(options, env())).status, 204);
});

test("publishes a redacted GitHub issue and returns only its URL", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  try {
    globalThis.fetch = async (url, options) => {
      requests.push({ url, options });
      return new Response(JSON.stringify({
        number: 77, html_url: "https://github.com/KoTeuKa404/pymusic/issues/77",
      }), { status: 201, headers: { "Content-Type": "application/json" } });
    };
    const malicious = {
      ...good,
      details: "Traceback https://foo.example/secret\ncontact test@example.com IP 1.2.3.4",
    };
    const response = await gateway.fetch(request(malicious), env());
    assert.equal(response.status, 201);
    const data = await response.json();
    assert.equal(data.issue_url, "https://github.com/KoTeuKa404/pymusic/issues/77");
    const sent = JSON.parse(requests[0].options.body);
    assert.equal(sent.labels[0], "crash-report");
    assert.match(sent.body, /\[URL\]/);
    assert.doesNotMatch(sent.body, /foo\.example|test@example.com|1\.2\.3\.4|private-token-must-not-leak/);
    assert.ok(!JSON.stringify(data).includes("private-token"));
  } finally {
    globalThis.fetch = originalFetch;
  }
});
