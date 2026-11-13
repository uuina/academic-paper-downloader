#!/usr/bin/env python3
"""Academic Paper Downloader CLI.

COMPLIANCE NOTICE:
Strictly for authorized personal academic use under valid institutional/campus
subscriptions or open-access repositories. Reuses the user's own authenticated
Chrome session via Chrome DevTools Protocol (CDP) and NEVER bypasses CAPTCHAs,
DRM, or unauthorized paywalls.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from parsers import (  # noqa: E402
    classify_publisher,
    deduplicate_papers,
    normalize_doi,
    normalize_title,
    normalize_url,
    parse_file,
    sanitize_filename,
)
from publishers import (  # noqa: E402
    DEFAULT_MIN_PDF_BYTES,
    check_cdp_endpoint,
    download_single_paper,
    sleep_with_jitter,
    verify_pdf_file,
)


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def write_json_atomic(data: Any, output_path: str) -> None:
    parent = os.path.dirname(os.path.abspath(output_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{output_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp_path, output_path)


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def compute_summary(papers: list[dict[str, Any]], invalid_pdfs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    counts: dict[str, int] = {
        "total": len(papers),
        "downloaded": 0,
        "pending": 0,
        "captcha_required": 0,
        "rate_limited": 0,
        "unauthorized_or_paywall": 0,
        "failed": 0,
    }
    by_publisher: dict[str, dict[str, int]] = {}
    for p in papers:
        st = p.get("status", "pending")
        if st in counts:
            counts[st] += 1
        else:
            counts[st] = counts.get(st, 0) + 1
        pub = p.get("publisher", "other")
        pub_bucket = by_publisher.setdefault(pub, {"total": 0, "downloaded": 0, "remaining": 0})
        pub_bucket["total"] += 1
        if st == "downloaded":
            pub_bucket["downloaded"] += 1
        else:
            pub_bucket["remaining"] += 1

    counts["invalid_local_pdfs"] = len(invalid_pdfs or [])
    counts["by_publisher"] = by_publisher
    return counts


def scan_local_pdfs(
    pdf_dir: str,
    min_bytes: int = DEFAULT_MIN_PDF_BYTES,
    recursive: bool = False,
) -> tuple[dict[str, str], dict[str, str], list[dict[str, Any]]]:
    """Scan pdf_dir and return (by_code, by_norm_title, invalid_pdfs)."""
    by_code: dict[str, str] = {}
    by_norm_title: dict[str, str] = {}
    invalid_pdfs: list[dict[str, Any]] = []

    if not os.path.isdir(pdf_dir):
        return by_code, by_norm_title, invalid_pdfs

    candidates: list[str] = []
    if recursive:
        for root, _, files in os.walk(pdf_dir):
            for fn in sorted(files):
                if fn.lower().endswith(".pdf"):
                    candidates.append(os.path.join(root, fn))
    else:
        for fn in sorted(os.listdir(pdf_dir)):
            full = os.path.join(pdf_dir, fn)
            if os.path.isfile(full) and fn.lower().endswith(".pdf"):
                candidates.append(full)

    for full_path in candidates:
        rel_name = os.path.relpath(full_path, pdf_dir)
        ok, reason, size = verify_pdf_file(full_path, min_bytes=min_bytes)
        if not ok:
            invalid_pdfs.append({"file": rel_name, "size": size, "reason": reason})
            continue

        stem = os.path.splitext(os.path.basename(full_path))[0]
        # Match leading code like P001_Title, R01-Title, etc.
        if "_" in stem:
            prefix, remainder = stem.split("_", 1)
            by_code.setdefault(prefix.strip().lower(), rel_name)
            norm_rem = normalize_title(remainder)
            if norm_rem:
                by_norm_title.setdefault(norm_rem, rel_name)
        elif "-" in stem:
            prefix, remainder = stem.split("-", 1)
            if len(prefix.strip()) <= 16:
                by_code.setdefault(prefix.strip().lower(), rel_name)
                norm_rem = normalize_title(remainder)
                if norm_rem:
                    by_norm_title.setdefault(norm_rem, rel_name)

        norm_full = normalize_title(stem)
        if norm_full:
            by_norm_title.setdefault(norm_full, rel_name)

    return by_code, by_norm_title, invalid_pdfs


def _match_local_pdf(
    entry: dict[str, Any],
    by_code: dict[str, str],
    by_norm_title: dict[str, str],
) -> str | None:
    codes = [entry.get("code", "")] + list(entry.get("aliases") or [])
    for c in codes:
        if c and c.strip().lower() in by_code:
            return by_code[c.strip().lower()]

    norm_t = normalize_title(entry.get("title", ""))
    if norm_t and norm_t in by_norm_title:
        return by_norm_title[norm_t]
    return None


def _load_entries_from_inputs(inputs: list[str], fmt: str) -> list[dict[str, Any]]:
    raw_entries: list[dict[str, Any]] = []
    for path in inputs:
        if not os.path.isfile(path):
            print(f"Error: input file not found: {path}", file=sys.stderr)
            sys.exit(1)
        if path.lower().endswith(".json") and fmt == "auto":
            data = load_json(path)
            if isinstance(data, dict) and isinstance(data.get("papers"), list):
                raw_entries.extend(data["papers"])
            elif isinstance(data, list):
                raw_entries.extend(data)
            else:
                print(f"Error: unrecognized JSON schema in {path}", file=sys.stderr)
                sys.exit(1)
        else:
            raw_entries.extend(parse_file(path, fmt=fmt))
    return deduplicate_papers(raw_entries)


def cmd_parse(args: argparse.Namespace) -> int:
    papers = _load_entries_from_inputs(args.input, args.format)
    payload = {
        "version": 1,
        "generated_at": utc_now_iso(),
        "total": len(papers),
        "papers": papers,
    }
    write_json_atomic(payload, args.output)
    print(f"Success! Parsed {len(papers)} deduplicated paper(s) -> {args.output}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    existing_by_key: dict[str, dict[str, Any]] = {}
    if os.path.isfile(args.state):
        try:
            old_state = load_json(args.state)
            for item in old_state.get("papers", []):
                key = normalize_doi(item.get("doi", "")) or normalize_url(item.get("url", "")) or item.get("code", "")
                if key:
                    existing_by_key[key] = item
        except (OSError, json.JSONDecodeError):
            pass

    if args.input:
        entries = _load_entries_from_inputs(args.input, args.format)
    elif existing_by_key:
        entries = list(existing_by_key.values())
    else:
        print("Error: provide --input file(s) or an existing --state file.", file=sys.stderr)
        return 1

    if not os.path.isdir(args.pdf_dir):
        print(f"Warning: --pdf-dir does not exist yet ({args.pdf_dir}); treating local inventory as empty.", file=sys.stderr)

    by_code, by_norm_title, invalid_pdfs = scan_local_pdfs(
        args.pdf_dir,
        min_bytes=args.min_bytes,
        recursive=args.recursive,
    )

    now = utc_now_iso()
    state_papers: list[dict[str, Any]] = []
    for entry in entries:
        doi = normalize_doi(entry.get("doi", ""))
        url = normalize_url(entry.get("url", "")) or (f"https://doi.org/{doi}" if doi else "")
        key = doi or url or entry.get("code", "")
        prev = existing_by_key.get(key, {})

        matched_file = _match_local_pdf(entry, by_code, by_norm_title)
        if matched_file:
            status = "downloaded"
            last_error = None
        else:
            prev_status = prev.get("status", "pending")
            status = "pending" if prev_status == "downloaded" else prev_status
            last_error = prev.get("last_error")

        state_papers.append(
            {
                "code": entry.get("code", ""),
                "aliases": entry.get("aliases", [entry.get("code", "")]),
                "title": entry.get("title", ""),
                "doi": doi,
                "url": url,
                "year": entry.get("year", ""),
                "publisher": entry.get("publisher") or classify_publisher(doi, url),
                "sources": entry.get("sources", []),
                "status": status,
                "local_file": matched_file,
                "attempts": prev.get("attempts", 0),
                "last_error": last_error,
                "updated_at": now,
            }
        )

    summary = compute_summary(state_papers, invalid_pdfs=invalid_pdfs)
    state_payload = {
        "version": 1,
        "updated_at": now,
        "pdf_dir": os.path.abspath(args.pdf_dir),
        "summary": summary,
        "invalid_pdfs": invalid_pdfs,
        "papers": state_papers,
    }
    write_json_atomic(state_payload, args.state)

    print(
        f"Scan complete: {summary['total']} total | {summary['downloaded']} downloaded | "
        f"{summary['total'] - summary['downloaded']} missing | {len(invalid_pdfs)} invalid PDF(s)"
    )
    print(f"Resumable state written to: {args.state}")
    return 0


def _print_cdp_launch_help(cdp_url: str) -> None:
    print(
        f"Chrome CDP endpoint is not reachable at {cdp_url}.\n"
        "Launch Chrome with a dedicated debugging profile and log into your\n"
        "institutional/campus library portal before retrying:\n\n"
        "  Windows (PowerShell):\n"
        '    & "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
        '--remote-debugging-port=9222 --user-data-dir="$env:USERPROFILE\\chrome-cdp-profile"\n\n'
        "  macOS:\n"
        '    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" '
        '--remote-debugging-port=9222 --user-data-dir="$HOME/chrome-cdp-profile"\n\n'
        "  Linux:\n"
        '    google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/chrome-cdp-profile"\n',
        file=sys.stderr,
    )


def cmd_check_cdp(args: argparse.Namespace) -> int:
    ok, info = check_cdp_endpoint(args.cdp_url, timeout=args.timeout)
    if not ok:
        print(f"CDP check failed ({args.cdp_url}): {info}", file=sys.stderr)
        _print_cdp_launch_help(args.cdp_url)
        return 1

    if args.output:
        write_json_atomic(info, args.output)
    browser_ver = info.get("Browser", "unknown") if isinstance(info, dict) else str(info)
    print(f"CDP reachable at {args.cdp_url} (Browser: {browser_ver})")
    return 0


def _build_target_filename(item: dict[str, Any]) -> str:
    code = sanitize_filename(item.get("code") or "PAPER", max_length=24)
    title = item.get("title") or item.get("doi") or "untitled"
    safe_title = sanitize_filename(title, max_length=140)
    return f"{code}_{safe_title}.pdf"


def cmd_download(args: argparse.Namespace) -> int:
    if not os.path.isfile(args.state):
        print(f"Error: state file not found: {args.state}. Run 'scan' first.", file=sys.stderr)
        return 1

    state = load_json(args.state)
    papers: list[dict[str, Any]] = state.get("papers", [])
    pdf_dir = args.pdf_dir or state.get("pdf_dir")
    if not pdf_dir:
        print("Error: --pdf-dir is required when not present in state file.", file=sys.stderr)
        return 1
    os.makedirs(pdf_dir, exist_ok=True)
    state["pdf_dir"] = os.path.abspath(pdf_dir)

    pub_filter = {p.strip().lower() for p in args.publishers.split(",") if p.strip()} if args.publishers else None
    code_filter = {c.strip().lower() for c in args.codes.split(",") if c.strip()} if args.codes else None
    allowed_statuses = {"pending"}
    if args.retry_failed:
        allowed_statuses.update({"failed", "rate_limited", "captcha_required"})

    queue: list[dict[str, Any]] = []
    for item in papers:
        if item.get("status", "pending") not in allowed_statuses:
            continue
        if pub_filter and item.get("publisher", "other").lower() not in pub_filter:
            continue
        if code_filter and item.get("code", "").lower() not in code_filter:
            continue
        queue.append(item)

    if args.limit > 0:
        queue = queue[: args.limit]

    if args.dry_run:
        print(f"Dry run: {len(queue)} paper(s) would be queued -> {pdf_dir}")
        for item in queue:
            print(f"  [{item.get('code')}] ({item.get('publisher')}) -> {_build_target_filename(item)}")
        return 0

    if not queue:
        print("Nothing to download (no matching pending items).")
        return 0

    ok_cdp, cdp_info = check_cdp_endpoint(args.cdp_url)
    if not ok_cdp:
        print(f"Error: CDP unreachable ({cdp_info})", file=sys.stderr)
        _print_cdp_launch_help(args.cdp_url)
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "Error: 'playwright' is required for CDP downloads.\n"
            "Run with: uv run --with playwright python skills/academic-paper-downloader/scripts/paper_downloader.py download ...",
            file=sys.stderr,
        )
        return 1

    by_code, by_norm_title, _ = scan_local_pdfs(pdf_dir, min_bytes=args.min_bytes)
    exit_code = 0

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(args.cdp_url)
        if not browser.contexts:
            print("Error: no active Chrome browser context found over CDP.", file=sys.stderr)
            return 1
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()

        for idx, item in enumerate(queue):
            code = item.get("code", f"ITEM{idx}")
            existing = _match_local_pdf(item, by_code, by_norm_title)
            if existing:
                item["status"] = "downloaded"
                item["local_file"] = existing
                item["last_error"] = None
                item["updated_at"] = utc_now_iso()
                state["summary"] = compute_summary(papers, state.get("invalid_pdfs"))
                state["updated_at"] = utc_now_iso()
                write_json_atomic(state, args.state)
                print(f"[{code}] SKIP (already exists): {existing}", flush=True)
                continue

            fn = _build_target_filename(item)
            out_path = os.path.join(pdf_dir, fn)
            print(f"[{code}] ({item.get('publisher')}) Downloading -> {fn}", flush=True)

            item["attempts"] = int(item.get("attempts", 0)) + 1
            result = download_single_paper(
                page,
                item,
                out_path,
                timeout_ms=args.timeout_ms,
                min_bytes=args.min_bytes,
            )
            item["status"] = result.status
            item["local_file"] = result.local_file
            item["last_error"] = result.error or None
            item["updated_at"] = utc_now_iso()

            state["summary"] = compute_summary(papers, state.get("invalid_pdfs"))
            state["updated_at"] = utc_now_iso()
            write_json_atomic(state, args.state)

            if result.status == "downloaded":
                print(f"[{code}] SUCCESS ({result.method}): {result.local_file} ({result.bytes_written:,} bytes)", flush=True)
            elif result.status == "captcha_required":
                print(
                    f"\n[{code}] PAUSED: {result.error}\n"
                    "Action required: Complete the human verification manually in your Chrome window, "
                    "then re-run the same 'download' command with --retry-failed to resume.",
                    file=sys.stderr,
                    flush=True,
                )
                exit_code = 2
                break
            elif result.status == "rate_limited":
                print(
                    f"\n[{code}] COOLDOWN REQUIRED: {result.error}\n"
                    "State saved. Wait for publisher rate-limit cooldown, then re-run with --retry-failed.",
                    file=sys.stderr,
                    flush=True,
                )
                exit_code = 3
                break
            elif result.status == "unauthorized_or_paywall":
                print(f"[{code}] UNAUTHORIZED / PAYWALL: {result.error}", file=sys.stderr, flush=True)
                if args.stop_on_paywall:
                    exit_code = 4
                    break
            else:
                print(f"[{code}] FAILED: {result.error}", file=sys.stderr, flush=True)

            if idx < len(queue) - 1:
                slept = sleep_with_jitter(args.delay_min, args.delay_max)
                print(f"[{code}] Rate-limit delay: {slept:.1f}s", flush=True)

    return exit_code


def cmd_status(args: argparse.Namespace) -> int:
    if not os.path.isfile(args.state):
        print(f"Error: state file not found: {args.state}", file=sys.stderr)
        return 1

    state = load_json(args.state)
    papers = state.get("papers", [])
    summary = compute_summary(papers, state.get("invalid_pdfs"))

    if args.output:
        write_json_atomic(summary, args.output)
        print(f"Success! Status summary written to: {args.output}")
        return 0

    print(
        f"Total: {summary['total']} | Downloaded: {summary['downloaded']} | "
        f"Pending: {summary['pending']} | CAPTCHA: {summary['captcha_required']} | "
        f"Rate-limited: {summary['rate_limited']} | Paywall: {summary['unauthorized_or_paywall']} | "
        f"Failed: {summary['failed']} | Invalid local PDFs: {summary['invalid_local_pdfs']}"
    )
    by_pub = summary.get("by_publisher", {})
    if by_pub:
        print("By publisher:")
        for pub, stats in sorted(by_pub.items()):
            print(f"  - {pub}: {stats['downloaded']}/{stats['total']} downloaded ({stats['remaining']} remaining)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compliant multi-publisher academic paper downloader using an authenticated "
            "Chrome CDP session. Strictly for authorized personal academic use."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # parse
    p_parse = sub.add_parser("parse", help="Parse and deduplicate Markdown, DOI/URL, BibTeX, or RIS inputs")
    p_parse.add_argument("--input", nargs="+", required=True, help="One or more literature list files")
    p_parse.add_argument(
        "--format",
        choices=["auto", "markdown", "doi", "bibtex", "ris"],
        default="auto",
        help="Input format (default: auto)",
    )
    p_parse.add_argument("--output", required=True, help="Output parsed JSON path")

    # scan
    p_scan = sub.add_parser("scan", help="Scan local PDF directory and build/update resumable missing_papers.json")
    p_scan.add_argument("--input", nargs="*", help="Input literature list file(s) or parsed JSON")
    p_scan.add_argument(
        "--format",
        choices=["auto", "markdown", "doi", "bibtex", "ris"],
        default="auto",
        help="Input format (default: auto)",
    )
    p_scan.add_argument("--pdf-dir", required=True, help="Local directory containing downloaded PDFs")
    p_scan.add_argument("--state", default="missing_papers.json", help="Resumable state JSON path")
    p_scan.add_argument("--min-bytes", type=int, default=DEFAULT_MIN_PDF_BYTES, help="Minimum valid PDF size in bytes")
    p_scan.add_argument("--recursive", action="store_true", help="Scan --pdf-dir recursively")

    # check-cdp
    p_cdp = sub.add_parser("check-cdp", help="Verify Chrome DevTools Protocol (CDP) endpoint is reachable")
    p_cdp.add_argument("--cdp-url", default="http://127.0.0.1:9222", help="Chrome CDP HTTP endpoint")
    p_cdp.add_argument("--timeout", type=float, default=5.0, help="Connection timeout in seconds")
    p_cdp.add_argument("--output", help="Optional JSON file path to write CDP version info")

    # download
    p_dl = sub.add_parser("download", help="Download missing papers via authenticated Chrome CDP session")
    p_dl.add_argument("--state", default="missing_papers.json", help="Resumable state JSON path")
    p_dl.add_argument("--pdf-dir", help="Target directory for downloaded PDFs (defaults to state pdf_dir)")
    p_dl.add_argument("--cdp-url", default="http://127.0.0.1:9222", help="Chrome CDP HTTP endpoint")
    p_dl.add_argument("--publishers", help="Comma-separated publisher filter (e.g. ieee,sciencedirect)")
    p_dl.add_argument("--codes", help="Comma-separated paper code filter (e.g. P001,P002)")
    p_dl.add_argument("--limit", type=int, default=0, help="Max papers to attempt in this batch (0 = all)")
    p_dl.add_argument("--delay-min", type=float, default=5.0, help="Minimum randomized delay between requests (seconds)")
    p_dl.add_argument("--delay-max", type=float, default=10.0, help="Maximum randomized delay between requests (seconds)")
    p_dl.add_argument("--timeout-ms", type=int, default=45000, help="Page navigation/request timeout in milliseconds")
    p_dl.add_argument("--min-bytes", type=int, default=DEFAULT_MIN_PDF_BYTES, help="Minimum valid PDF size in bytes")
    p_dl.add_argument("--retry-failed", action="store_true", help="Include failed/rate_limited/captcha_required entries")
    p_dl.add_argument("--stop-on-paywall", action="store_true", help="Stop immediately if an unsubscribed paywall is hit")
    p_dl.add_argument("--dry-run", action="store_true", help="Preview queued downloads without launching Playwright")

    # status
    p_status = sub.add_parser("status", help="Summarize progress from resumable missing_papers.json")
    p_status.add_argument("--state", default="missing_papers.json", help="Resumable state JSON path")
    p_status.add_argument("--output", help="Optional JSON file path to write summary")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "parse":
        return cmd_parse(args)
    if args.command == "scan":
        return cmd_scan(args)
    if args.command == "check-cdp":
        return cmd_check_cdp(args)
    if args.command == "download":
        return cmd_download(args)
    if args.command == "status":
        return cmd_status(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
