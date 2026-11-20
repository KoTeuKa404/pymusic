# PyMusic Crash Reports

This repository contains an Android crash reporting screen, a GitHub Pages
dashboard, and an **optional** Cloudflare Worker for one-tap public GitHub
Issues submission. The APK never contains a GitHub token.

## 1. Publish the public dashboard

1. On GitHub open **Settings → Pages → Build and deployment → Source**.
2. Select **GitHub Actions** (this repository had Pages disabled before setup).
3. Run **PyMusic Crash Reports Pages** in the Actions tab, or push a change to
   `docs/**`.
4. The dashboard will appear at:
   `https://koteuka404.github.io/pymusic/crashes/`.

The page reads **public** GitHub Issues, marked `crash-report` or prefixed
`[PyMusic Crash]`. It does not need a GitHub token and cannot accept uploads
itself. Results are delayed by GitHub API availability/rate limits.

## 2. Enable true one-tap sending (optional server)

**Without this step**, the in-app Crash Report button will copy a sanitized
diagnostic to the clipboard and open a GitHub Issue form. User login, paste,
and submit are required. It does **not** upload automatically.

The alternative is the bundled Cloudflare Worker:

1. Deploy `crash_gateway/` in **your own** Cloudflare account (Wrangler 4.36+).
2. Create a **fine-grained GitHub PAT** restricted to the *pymusic* repository
   with **Issues: Read and Write** only. Never put the token into a public file,
   the APK, GitHub Pages, or a chat message.
3. In a local terminal:

   ```sh
   cd crash_gateway
   npx wrangler login
   npx wrangler secret put GITHUB_TOKEN
   npx wrangler deploy
   ```

4. Copy the deployed Worker URL into `docs/crashes/config.json`:

   ```json
   {"gateway":"https://pymusic-crash-gateway.YOUR-SUBDOMAIN.workers.dev"}
   ```

5. Commit that JSON. The APK fetches the public config only when the user
   taps Crash Report and caches the endpoint for 30 minutes. No APK rebuild is
   required for the endpoint change. The button then sends a scrubbed report
   directly to the Worker and opens the created GitHub Issue.

If `GITHUB_TOKEN` or the configured Cloudflare rate-limit binding is missing,
the Worker fails closed with HTTP 503. The Worker only accepts limited-size
JSON reports and does not log report bodies. It does a second redaction before
creating public Issues. The worker's per-IP limiter is approximate and scoped
per Cloudflare location; add Cloudflare WAF/abuse rules if used by the public.

## Privacy and security

Reports are **public**, and users must opt in by tapping the in-app button.
Never auto-upload after a crash or on app restart. Client and Worker both
replace media URLs, emails, IPs, secrets, home directories and long identifiers
with placeholders; redaction cannot guarantee the absence of every personal
detail. Do not use this channel for confidential logs. It is recommended to
review reports if privacy matters before publishing.

The Android logger already persists Python exceptions and a native
faulthandler dump, but some Android JNI/native crashes require an ADB tombstone
for a full stack. The report button works after relaunch and may not contain
the complete native tombstone in those cases.

## Local verification

```sh
python -m unittest discover -s tests -p 'test_*.py' -v
node --test crash_gateway/tests/*.test.mjs
```

Source: `android_src/crash_reporter.py`, `crash_gateway/src/index.js`,
`docs/crashes/index.html`, and `.github/workflows/crash-reports-pages.yml`.
