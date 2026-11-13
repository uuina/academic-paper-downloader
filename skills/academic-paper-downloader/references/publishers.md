# Publisher Adapters & Rate-Limit Reference

This reference documents how `publishers.py` handles each supported publisher
over an authenticated Chrome DevTools Protocol (CDP) session.

> **Compliance Rule**: All adapters reuse the user's legitimate browser cookies
> and institutional IP/VPN session. They **never** bypass CAPTCHAs, paywalls,
> or DRM controls.

## Supported Publishers

| Key | DOI Prefixes / Domains | Primary Extraction Strategy | Fallbacks |
|---|---|---|---|
| `ieee` | `10.1109/*`, `ieeexplore.ieee.org` | Resolve `/document/<arnumber>`, fetch `stampPDF/getPDF.jsp?tp=&arnumber=<id>` via `page.request.get` | Check block/paywall markers |
| `sciencedirect` | `10.1016/*`, `sciencedirect.com` | Locate `/pdfft` button, navigate to `pdf.sciencedirectassets.com`, fetch binary blob via in-page JS `fetch()` | Wait for delayed asset redirect |
| `iop` | `10.1088/*`, `iopscience.iop.org` | Append `/pdf` to resolved article URL and `page.request.get` | Locate PDF `href` or `expect_download()` click |
| `wiley` | `10.1002/*`, `10.1049/*`, `10.1111/*`, `onlinelibrary.wiley.com` | Rewrite `/doi/(abs\|full\|epdf)/` to `/doi/pdfdirect/` and `page.request.get` | Locate PDF `href` or `expect_download()` click |
| `acs` | `10.1021/*`, `pubs.acs.org` | Locate `/doi/pdf/` link and fetch via `page.request.get` | CDP `Page.setDownloadBehavior` + `expect_download()` click |
| `cnki` | `cnki.net`, `kns.cnki.net`, `oversea.cnki.net` | Detect slider/CAPTCHA first; locate `#pdfDown` / `PDF下载` and capture `expect_download()` | Pause & prompt user if `安全验证` appears |
| `other` | `springer`, `emerald`, `arxiv`, generic | Direct `.pdf` fetch or `<meta name="citation_pdf_url">` | Locate PDF link `href` or `expect_download()` click |

## Anti-Bot, CAPTCHA, and Paywall Handling

Before and after navigation, `detect_page_block()` inspects the page URL, title,
and visible text:

- **CAPTCHA / Challenge (`exit code 2`)**:
  - Radware Bot Manager (`validate.perfdrive.com`)
  - Cloudflare Turnstile / challenge (`challenges.cloudflare.com`, `Just a moment...`)
  - Slider verification (`安全验证`, `滑动验证`, `verify.cnki.net`)
  - reCAPTCHA / hCaptcha
  - **Action**: Save state as `captcha_required` and stop immediately so the user
    can complete the challenge manually in Chrome.
- **Rate / Quota Limit (`exit code 3`)**:
  - HTTP `429 Too Many Requests` or publisher daily quota text
  - **Action**: Save state as `rate_limited` and stop for cooldown recovery.
- **Unauthorized / Unsubscribed Paywall**:
  - `Purchase PDF`, `Buy this article`, `Institutional login` with no PDF link
  - **Action**: Mark `unauthorized_or_paywall` (or stop immediately if
    `--stop-on-paywall` is set) and report clearly at the end.

## Recommended Delay & Batch Settings

- **Default delay**: `--delay-min 5.0 --delay-max 10.0`
- **Strict anti-bot publishers (`sciencedirect`, `wiley`, `cnki`)**:
  `--delay-min 8.0 --delay-max 15.0 --limit 15`
- **All saved files**: Atomically written to `<name>.pdf.tmp` only after verifying
  the 5-byte `%PDF-` header and minimum file size (`>= 1024` bytes), then renamed
  to `<code_title>.pdf`.
