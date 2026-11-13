# Academic Paper Downloader — Compliance & Workflow Rules

1. **Authorized Academic Use Only**: Only assist with downloading papers that the user is legitimately authorized to access through open-access repositories or valid institutional/campus subscriptions in their own authenticated Chrome session.
2. **Never Bypass Access Controls**:
   - If a publisher presents a CAPTCHA, slider challenge, Cloudflare Turnstile, or Radware challenge, **STOP immediately**, save resumable state (`missing_papers.json`), and prompt the user to complete verification manually in the browser.
   - If an article hits an unsubscribed paywall, mark it as `unauthorized_or_paywall` and report it transparently. Never attempt credential stuffing, proxy circumvention, or DRM/paywall bypass.
3. **Respect Publisher Rate Limits**: Keep randomized inter-request delays (default 5–10 seconds) and stop on HTTP 429 or download-quota warnings so the user can cool down and resume later.
4. **Verify Every PDF**: Always verify `%PDF-` magic bytes and non-trivial file size before marking any paper as downloaded.
