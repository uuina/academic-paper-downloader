---
name: academic-paper-downloader
description: >-
  Authorized academic literature batch downloader that parses Markdown review
  lists, plain DOI/URL lists, BibTeX, and RIS files, compares against local
  PDFs, and downloads missing papers via the user's authenticated Chrome CDP
  session across IEEE Xplore, ScienceDirect, IOPscience, Wiley/IET, ACS, and
  CNKI with randomized delays, %PDF- validation, and resumable state tracking.
---

# Academic Paper Downloader

> **Compliance Notice**: This skill is strictly for authorized personal academic
> use under valid institutional/campus subscriptions or open-access repositories.
> It reuses your own authenticated Chrome session over Chrome DevTools Protocol
> (CDP) and **never** attempts to bypass CAPTCHAs, DRM, or unauthorized paywalls.
> If human verification or an unsubscribed paywall is encountered, the workflow
> pauses and prompts you to handle it manually.

## Overview

`academic-paper-downloader` automates the repetitive parts of building a local
research PDF library while keeping institutional access compliant and trackable:

1. **Adaptive Parsing & Deduplication**: Extracts and deduplicates paper
   entries across Markdown review tables/lists, plain DOI/URL lists, BibTeX
   (`.bib`), and RIS (`.ris`) exports.
2. **Local Inventory Scan & `%PDF-` Verification**: Scans a local PDF directory,
   rejects fake/corrupt HTML error pages saved with a `.pdf` suffix, and builds
   a resumable `missing_papers.json` state file.
3. **Multi-Publisher Chrome CDP Download**: Dispatches each missing paper to a
   publisher-specific adapter (`ieee`, `sciencedirect`, `iop`, `wiley`, `acs`,
   `cnki`, plus generic `citation_pdf_url` fallback) with randomized delays,
   magic-byte `%PDF-` validation, and per-paper state persistence.

## Dependencies

- **Playwright (Python)** via `uv run --with playwright` (only required for the
  `download` subcommand; `parse`, `scan`, `status`, and `check-cdp` use Python
  standard library only).
- **Authenticated Chrome CDP Session**: Launch Google Chrome with
  `--remote-debugging-port=9222` and a dedicated `--user-data-dir`, then log
  into your institutional/campus library portal before running `download`.
- **Optional**: Pair with `paddleocr-vision` after downloading to convert PDFs
  into Markdown + extracted tables/formulas locally.

## Quick Start

```bash
# 1. Scan your literature list against an existing local PDF folder
uv run python skills/academic-paper-downloader/scripts/paper_downloader.py scan \
  --input examples/sample_review.md \
  --pdf-dir ./papers \
  --state missing_papers.json

# 2. Check that Chrome CDP is running and reachable
uv run python skills/academic-paper-downloader/scripts/paper_downloader.py check-cdp \
  --cdp-url http://127.0.0.1:9222

# 3. Download missing papers with randomized 5-10s delays
uv run --with playwright python skills/academic-paper-downloader/scripts/paper_downloader.py download \
  --state missing_papers.json \
  --pdf-dir ./papers \
  --delay-min 5.0 \
  --delay-max 10.0 \
  --limit 20

# 4. Inspect progress at any time
uv run python skills/academic-paper-downloader/scripts/paper_downloader.py status \
  --state missing_papers.json
```

## Utility Scripts

All commands live in `skills/academic-paper-downloader/scripts/paper_downloader.py`
and write structured JSON state/output to disk rather than flooding stdout.

### `parse` — Extract & Deduplicate Literature Lists

Supports `.md`, `.txt`, `.bib`, `.ris` (auto-detected or `--format markdown|doi|bibtex|ris`):

```bash
uv run python skills/academic-paper-downloader/scripts/paper_downloader.py parse \
  --input review.md refs.bib extra_dois.txt \
  --output parsed_papers.json
```

### `scan` — Verify Local PDFs & Compute Missing Queue

Checks every local `*.pdf` for the `%PDF-` magic-byte header and minimum size
(`--min-bytes`, default `1024`), matches by code prefix or normalized title, and
writes resumable `missing_papers.json`:

```bash
uv run python skills/academic-paper-downloader/scripts/paper_downloader.py scan \
  --input review.md \
  --pdf-dir /path/to/papers \
  --state missing_papers.json
```

### `check-cdp` — Verify Authenticated Chrome Debug Port

```bash
uv run python skills/academic-paper-downloader/scripts/paper_downloader.py check-cdp \
  --cdp-url http://127.0.0.1:9222
```

### `download` — Controlled Batch Download Over CDP

Supports filtering by `--publishers` (`ieee,sciencedirect,iop,wiley,acs,cnki`),
`--codes`, `--limit`, `--retry-failed`, `--include-paywall`, and `--dry-run`:

```bash
uv run --with playwright python skills/academic-paper-downloader/scripts/paper_downloader.py download \
  --state missing_papers.json \
  --pdf-dir /path/to/papers \
  --publishers ieee,sciencedirect \
  --delay-min 6.0 \
  --delay-max 12.0 \
  --limit 15
```

Exit codes:
- `0`: Batch completed (check `status` for any individual `unauthorized_or_paywall` or `failed` items).
- `1`: Configuration, file, or unreachable CDP error.
- `2`: Human verification / CAPTCHA encountered — solve manually in Chrome, then resume with `--retry-failed`.
- `3`: Publisher rate/quota limit encountered — wait for cooldown, then resume with `--retry-failed`.

### `status` — Summarize Resumable Progress

```bash
uv run python skills/academic-paper-downloader/scripts/paper_downloader.py status \
  --state missing_papers.json \
  --output summary.json
```

See [references/publishers.md](references/publishers.md) for publisher-specific
extraction mechanics, URL normalization rules, and anti-bot behavior.

## Rate Limiting

- **Randomized jitter**: Always enforces `random.uniform(delay_min, delay_max)`
  seconds between actual network downloads (default `5.0–10.0s`). Already
  verified local PDFs are skipped immediately without delay.
- **Batch caps (`--limit`)**: Keep per-session batches moderate (e.g., 15–25
  papers per publisher) to avoid triggering institutional or publisher daily quotas.
- **Cooldown & resume**: Every paper updates `missing_papers.json` immediately on
  disk. If exit code `2` (CAPTCHA) or `3` (rate limit) occurs, wait or solve
  manually in Chrome and re-run with `--retry-failed`.

## Common Mistakes

1. **Launching Chrome CDP without a separate `--user-data-dir`**: Chrome ignores
   `--remote-debugging-port=9222` if another Chrome instance already owns the
   default profile directory. Always pass a dedicated `--user-data-dir`.
2. **Trusting `.pdf` files without checking `%PDF-` bytes**: Many publisher
   gateways return HTTP 200 HTML login or Radware challenge pages. Never skip
   `scan` or magic-byte verification.
3. **Attempting to script around CAPTCHAs**: Automated clicks on Turnstile,
   Radware, or CNKI slider checks worsen IP/account blocks and violate compliance.
   Stop immediately, verify manually in the browser, and resume from state.
