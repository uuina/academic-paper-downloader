"""Multi-publisher Chrome CDP adapters, PDF verification, and compliance guardrails.

COMPLIANCE NOTICE:
This module is strictly for authorized personal academic use under valid
institutional/campus subscriptions or open-access repositories. It reuses the
user's existing authenticated Chrome session over CDP and NEVER attempts to
bypass CAPTCHAs, DRM, or unauthorized paywalls.
"""

from __future__ import annotations

import base64
import json
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

PDF_MAGIC = b"%PDF-"
DEFAULT_MIN_PDF_BYTES = 1024


@dataclass
class DownloadResult:
    status: str  # downloaded | already_exists | captcha_required | rate_limited | unauthorized_or_paywall | failed
    local_file: str | None = None
    bytes_written: int = 0
    method: str = ""
    error: str = ""


def verify_pdf_bytes(data: bytes, min_bytes: int = DEFAULT_MIN_PDF_BYTES) -> tuple[bool, str]:
    """Verify that raw bytes begin with the %PDF- magic header and meet size threshold."""
    if not data:
        return False, "Empty response body"
    if not data.startswith(PDF_MAGIC):
        preview = repr(data[:48])
        return False, f"Missing %PDF- magic header (got {preview})"
    if len(data) < min_bytes:
        return False, f"File too small ({len(data)} bytes < {min_bytes} bytes)"
    return True, "OK"


def verify_pdf_file(path: str, min_bytes: int = DEFAULT_MIN_PDF_BYTES) -> tuple[bool, str, int]:
    """Verify an existing file on disk is a real PDF via %PDF- magic header and size."""
    if not os.path.isfile(path):
        return False, "File does not exist", 0
    size = os.path.getsize(path)
    if size < min_bytes:
        return False, f"File too small ({size} bytes < {min_bytes} bytes)", size
    try:
        with open(path, "rb") as f:
            header = f.read(5)
    except OSError as exc:
        return False, f"Read error: {exc}", size
    if header != PDF_MAGIC:
        return False, f"Invalid header {header!r} (expected {PDF_MAGIC!r})", size
    return True, "OK", size


def save_verified_pdf(data: bytes, out_path: str, min_bytes: int = DEFAULT_MIN_PDF_BYTES) -> tuple[bool, str]:
    """Atomically write bytes to out_path only after %PDF- validation succeeds."""
    ok, reason = verify_pdf_bytes(data, min_bytes=min_bytes)
    if not ok:
        return False, reason
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmp_path = f"{out_path}.tmp"
    with open(tmp_path, "wb") as f:
        f.write(data)
    os.replace(tmp_path, out_path)
    return True, "OK"


def check_cdp_endpoint(cdp_url: str, timeout: float = 5.0) -> tuple[bool, dict[str, Any] | str]:
    """Check whether Chrome DevTools Protocol is reachable via /json/version."""
    base = cdp_url.rstrip("/")
    version_url = f"{base}/json/version"
    try:
        req = urllib_request.Request(version_url, headers={"Accept": "application/json"})
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            info = json.loads(resp.read().decode("utf-8"))
        return True, info
    except (urllib_error.URLError, urllib_error.HTTPError, OSError, json.JSONDecodeError) as exc:
        return False, str(exc)


def detect_page_block(url: str, title: str, body_text: str) -> tuple[str | None, str]:
    """Detect CAPTCHA challenges, rate-limiting, or explicit paywall blocks."""
    u = (url or "").lower()
    t = (title or "").lower()
    b = (body_text or "")[:4000].lower()

    captcha_url_markers = (
        "validate.perfdrive.com",
        "challenges.cloudflare.com",
        "verify.cnki.net",
        "/captcha",
    )
    if any(m in u for m in captcha_url_markers):
        return "captcha_required", f"Anti-bot verification URL encountered: {url}"

    captcha_text_markers = (
        "just a moment...",
        "verify you are human",
        "cf-challenge",
        "radware bot manager",
        "安全验证",
        "滑动验证",
        "请完成安全验证",
        "hcaptcha",
        "i'm not a robot",
    )
    if any(m in t or m in b for m in captcha_text_markers):
        return "captcha_required", f"Human verification / CAPTCHA detected on page: {title or url}"

    rate_limit_markers = (
        "429 too many requests",
        "too many requests",
        "rate limit exceeded",
        "exceeded your daily download limit",
        "请求过于频繁",
        "下载次数已达上限",
    )
    if any(m in t or m in b for m in rate_limit_markers):
        return "rate_limited", f"Publisher rate/quota limit reached on: {url}"

    paywall_markers = (
        "purchase pdf",
        "buy this article",
        "buy article",
        "sign in via your institution",
        "get access to the full version",
        "institutional login",
        "access through your institution",
    )
    if any(m in b for m in paywall_markers):
        return "unauthorized_or_paywall", f"Institutional access / subscription required: {url}"

    return None, ""


def _safe_inspect_page(page: Any) -> tuple[str, str, str]:
    url = getattr(page, "url", "") or ""
    title = ""
    body_text = ""
    try:
        title = page.title() or ""
    except Exception:
        pass
    try:
        body_text = page.evaluate("() => document.body ? document.body.innerText.slice(0, 4000) : ''") or ""
    except Exception:
        pass
    return url, title, body_text


def _fetch_via_request(page: Any, target_url: str, timeout_ms: int) -> tuple[bytes | None, str]:
    try:
        res = page.request.get(target_url, timeout=timeout_ms)
        status = getattr(res, "status", 200)
        if status == 429:
            return None, "HTTP 429 Too Many Requests"
        if status in (401, 403):
            return None, f"HTTP {status} Unauthorized/Forbidden"
        body = res.body()
        return body, ""
    except Exception as exc:
        return None, str(exc)


def _fetch_via_inpage_js(page: Any, target_url: str | None = None) -> tuple[bytes | None, str]:
    js = """async (targetUrl) => {
        const u = targetUrl || window.location.href;
        const r = await fetch(u, { credentials: 'include' });
        if (!r.ok) {
            return { error: 'HTTP ' + r.status };
        }
        const buf = await r.arrayBuffer();
        const bytes = new Uint8Array(buf);
        let binary = '';
        const chunk = 16384;
        for (let i = 0; i < bytes.length; i += chunk) {
            binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
        }
        return { b64: btoa(binary) };
    }"""
    try:
        res = page.evaluate(js, target_url)
        if not isinstance(res, dict):
            return None, "Unexpected JS fetch return value"
        if res.get("error"):
            return None, str(res["error"])
        b64 = res.get("b64", "")
        return base64.b64decode(b64), ""
    except Exception as exc:
        return None, str(exc)


def _try_click_download(page: Any, locator: Any, out_path: str, timeout_ms: int, min_bytes: int) -> tuple[bool, str, int]:
    tmp_dl = f"{out_path}.dl.tmp"
    try:
        with page.expect_download(timeout=min(timeout_ms, 20000)) as dl_info:
            locator.click()
        dl = dl_info.value
        dl.save_as(tmp_dl)
        ok, reason, size = verify_pdf_file(tmp_dl, min_bytes=min_bytes)
        if ok:
            os.replace(tmp_dl, out_path)
            return True, "OK", size
        if os.path.exists(tmp_dl):
            os.remove(tmp_dl)
        return False, reason, 0
    except Exception as exc:
        if os.path.exists(tmp_dl):
            try:
                os.remove(tmp_dl)
            except OSError:
                pass
        return False, str(exc), 0


def _try_save_bytes(data: bytes | None, err: str, out_path: str, method: str, min_bytes: int) -> DownloadResult | None:
    if data is None:
        if "429" in err:
            return DownloadResult(status="rate_limited", error=err)
        return None
    ok, reason = save_verified_pdf(data, out_path, min_bytes=min_bytes)
    if ok:
        return DownloadResult(
            status="downloaded",
            local_file=os.path.basename(out_path),
            bytes_written=len(data),
            method=method,
        )
    if b"captcha" in data[:1024].lower() or b"perfdrive" in data[:1024].lower():
        return DownloadResult(status="captcha_required", error=f"Challenge returned by PDF endpoint ({reason})")
    return None


def _check_block_on_current_page(page: Any) -> DownloadResult | None:
    url, title, body = _safe_inspect_page(page)
    block_status, reason = detect_page_block(url, title, body)
    if block_status == "captcha_required":
        return DownloadResult(status="captcha_required", error=reason)
    if block_status == "rate_limited":
        return DownloadResult(status="rate_limited", error=reason)
    return None


def download_ieee(page: Any, item: dict[str, Any], out_path: str, timeout_ms: int = 45000, min_bytes: int = DEFAULT_MIN_PDF_BYTES) -> DownloadResult:
    target = item.get("url") or (f"https://doi.org/{item['doi']}" if item.get("doi") else "")
    page.goto(target, wait_until="load", timeout=timeout_ms)
    time.sleep(2.0)

    blocked = _check_block_on_current_page(page)
    if blocked:
        return blocked

    curr_url = page.url
    m = re.search(r"/document/(\d+)", curr_url)
    if not m:
        time.sleep(2.0)
        m = re.search(r"/document/(\d+)", page.url)

    if m:
        arnumber = m.group(1)
        pdf_url = f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={arnumber}"
        data, err = _fetch_via_request(page, pdf_url, timeout_ms)
        saved = _try_save_bytes(data, err, out_path, "ieee_getPDF", min_bytes)
        if saved:
            return saved

    url, title, body = _safe_inspect_page(page)
    b_status, b_reason = detect_page_block(url, title, body)
    if b_status:
        return DownloadResult(status=b_status, error=b_reason)
    return DownloadResult(status="failed", error=f"Could not extract IEEE arnumber or valid PDF from {curr_url}")


def download_sciencedirect(page: Any, item: dict[str, Any], out_path: str, timeout_ms: int = 45000, min_bytes: int = DEFAULT_MIN_PDF_BYTES) -> DownloadResult:
    target = item.get("url") or (f"https://doi.org/{item['doi']}" if item.get("doi") else "")
    page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
    time.sleep(5.0)

    blocked = _check_block_on_current_page(page)
    if blocked:
        return blocked

    sel = 'a[aria-label*="PDF"], a.link-button-primary, a[href*="/pdfft"]'
    pdf_btn = page.locator(sel).first
    if pdf_btn.count() == 0:
        time.sleep(4.0)
        pdf_btn = page.locator(sel).first

    if pdf_btn.count() > 0:
        href = pdf_btn.get_attribute("href")
        if href:
            if not href.startswith("http"):
                href = "https://www.sciencedirect.com" + href
            try:
                page.goto(href, wait_until="commit", timeout=min(timeout_ms, 25000))
            except Exception:
                pass
            try:
                page.wait_for_url(lambda u: "pdf.sciencedirectassets.com" in u or u.endswith(".pdf"), timeout=30000)
            except Exception:
                pass
            time.sleep(3.0)

            blocked = _check_block_on_current_page(page)
            if blocked:
                return blocked

            if "pdf.sciencedirectassets.com" in page.url or page.url.endswith(".pdf"):
                data, err = _fetch_via_inpage_js(page)
                saved = _try_save_bytes(data, err, out_path, "sciencedirect_js_blob", min_bytes)
                if saved:
                    return saved

    url, title, body = _safe_inspect_page(page)
    b_status, b_reason = detect_page_block(url, title, body)
    if b_status:
        return DownloadResult(status=b_status, error=b_reason)
    return DownloadResult(status="failed", error=f"ScienceDirect PDF button/asset unreachable at {page.url}")


def download_iop(page: Any, item: dict[str, Any], out_path: str, timeout_ms: int = 45000, min_bytes: int = DEFAULT_MIN_PDF_BYTES) -> DownloadResult:
    target = item.get("url") or (f"https://doi.org/{item['doi']}" if item.get("doi") else "")
    page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
    time.sleep(3.0)

    blocked = _check_block_on_current_page(page)
    if blocked:
        return blocked

    curr_url = page.url
    pdf_url = curr_url.split("?")[0].rstrip("/") + "/pdf"
    data, err = _fetch_via_request(page, pdf_url, timeout_ms)
    saved = _try_save_bytes(data, err, out_path, "iop_direct_pdf", min_bytes)
    if saved:
        return saved

    pdf_link = page.locator('a[href*="/pdf"], a:has-text("PDF")').first
    if pdf_link.count() > 0:
        href = pdf_link.get_attribute("href")
        if href:
            if not href.startswith("http"):
                href = "https://iopscience.iop.org" + href
            data, err = _fetch_via_request(page, href, timeout_ms)
            saved = _try_save_bytes(data, err, out_path, "iop_link_href", min_bytes)
            if saved:
                return saved

        ok, _, size = _try_click_download(page, pdf_link, out_path, timeout_ms, min_bytes)
        if ok:
            return DownloadResult(status="downloaded", local_file=os.path.basename(out_path), bytes_written=size, method="iop_click_download")

    url, title, body = _safe_inspect_page(page)
    b_status, b_reason = detect_page_block(url, title, body)
    if b_status:
        return DownloadResult(status=b_status, error=b_reason)
    return DownloadResult(status="failed", error=f"IOP PDF download failed at {curr_url}")


def download_wiley(page: Any, item: dict[str, Any], out_path: str, timeout_ms: int = 45000, min_bytes: int = DEFAULT_MIN_PDF_BYTES) -> DownloadResult:
    target = item.get("url") or (f"https://doi.org/{item['doi']}" if item.get("doi") else "")
    page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
    time.sleep(3.0)

    blocked = _check_block_on_current_page(page)
    if blocked:
        return blocked

    curr_url = page.url
    pdf_url = re.sub(r"/doi/(abs|full|epdf)/", "/doi/pdfdirect/", curr_url)
    if "/doi/pdfdirect/" not in pdf_url and "/doi/" in pdf_url:
        pdf_url = pdf_url.replace("/doi/", "/doi/pdfdirect/", 1)

    data, err = _fetch_via_request(page, pdf_url, timeout_ms)
    saved = _try_save_bytes(data, err, out_path, "wiley_pdfdirect", min_bytes)
    if saved:
        return saved

    pdf_link = page.locator('a[title="PDF"], a[href*="/doi/pdf"], a[href*="/pdfdirect/"], a:has-text("PDF")').first
    if pdf_link.count() > 0:
        href = pdf_link.get_attribute("href")
        if href:
            if not href.startswith("http"):
                base = curr_url.split("/doi/")[0]
                href = base + href
            data, err = _fetch_via_request(page, href, timeout_ms)
            saved = _try_save_bytes(data, err, out_path, "wiley_link_href", min_bytes)
            if saved:
                return saved

        ok, _, size = _try_click_download(page, pdf_link, out_path, timeout_ms, min_bytes)
        if ok:
            return DownloadResult(status="downloaded", local_file=os.path.basename(out_path), bytes_written=size, method="wiley_click_download")

    url, title, body = _safe_inspect_page(page)
    b_status, b_reason = detect_page_block(url, title, body)
    if b_status:
        return DownloadResult(status=b_status, error=b_reason)
    return DownloadResult(status="failed", error=f"Wiley PDF download failed at {curr_url}")


def download_acs(page: Any, item: dict[str, Any], out_path: str, timeout_ms: int = 45000, min_bytes: int = DEFAULT_MIN_PDF_BYTES) -> DownloadResult:
    target = item.get("url") or (f"https://doi.org/{item['doi']}" if item.get("doi") else "")
    page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
    time.sleep(5.0)

    blocked = _check_block_on_current_page(page)
    if blocked:
        return blocked

    try:
        client = page.context.new_cdp_session(page)
        client.send("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": os.path.dirname(os.path.abspath(out_path))})
    except Exception:
        pass

    btn = page.locator('a[href*="/doi/pdf/"], a[title*="PDF"], a:has-text("PDF")').first
    if btn.count() > 0:
        href = btn.get_attribute("href")
        if href:
            if not href.startswith("http"):
                href = "https://pubs.acs.org" + href
            data, err = _fetch_via_request(page, href, timeout_ms)
            saved = _try_save_bytes(data, err, out_path, "acs_link_href", min_bytes)
            if saved:
                return saved

        ok, _, size = _try_click_download(page, btn, out_path, timeout_ms, min_bytes)
        if ok:
            return DownloadResult(status="downloaded", local_file=os.path.basename(out_path), bytes_written=size, method="acs_click_download")

    url, title, body = _safe_inspect_page(page)
    b_status, b_reason = detect_page_block(url, title, body)
    if b_status:
        return DownloadResult(status=b_status, error=b_reason)
    return DownloadResult(status="failed", error=f"ACS PDF download failed at {page.url}")


def download_cnki(page: Any, item: dict[str, Any], out_path: str, timeout_ms: int = 45000, min_bytes: int = DEFAULT_MIN_PDF_BYTES) -> DownloadResult:
    target = item.get("url") or (f"https://doi.org/{item['doi']}" if item.get("doi") else "")
    page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
    time.sleep(4.0)

    blocked = _check_block_on_current_page(page)
    if blocked:
        return blocked

    btn = page.locator('#pdfDown, a#pdfDown, a:has-text("PDF下载"), a[href*="download.aspx"]').first
    if btn.count() > 0:
        ok, _, size = _try_click_download(page, btn, out_path, timeout_ms, min_bytes)
        if ok:
            return DownloadResult(status="downloaded", local_file=os.path.basename(out_path), bytes_written=size, method="cnki_click_download")

        # Sometimes CNKI opens a PDF viewer or triggers security check after click
        time.sleep(2.0)
        blocked = _check_block_on_current_page(page)
        if blocked:
            return blocked

    url, title, body = _safe_inspect_page(page)
    b_status, b_reason = detect_page_block(url, title, body)
    if b_status:
        return DownloadResult(status=b_status, error=b_reason)
    return DownloadResult(status="failed", error=f"CNKI PDF button not found or download failed at {page.url}")


def download_generic(page: Any, item: dict[str, Any], out_path: str, timeout_ms: int = 45000, min_bytes: int = DEFAULT_MIN_PDF_BYTES) -> DownloadResult:
    target = item.get("url") or (f"https://doi.org/{item['doi']}" if item.get("doi") else "")
    if target.lower().endswith(".pdf") or "arxiv.org/pdf/" in target.lower():
        data, err = _fetch_via_request(page, target, timeout_ms)
        saved = _try_save_bytes(data, err, out_path, "generic_direct_url", min_bytes)
        if saved:
            return saved

    page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
    time.sleep(4.0)

    blocked = _check_block_on_current_page(page)
    if blocked:
        return blocked

    # Check standard scholarly meta tag citation_pdf_url
    try:
        meta_pdf = page.evaluate(
            "() => { const m = document.querySelector('meta[name=\"citation_pdf_url\"]'); return m ? m.content : ''; }"
        )
    except Exception:
        meta_pdf = ""

    if meta_pdf:
        data, err = _fetch_via_request(page, meta_pdf, timeout_ms)
        saved = _try_save_bytes(data, err, out_path, "meta_citation_pdf_url", min_bytes)
        if saved:
            return saved

    btn = page.locator('a[href*=".pdf"], a[href*="/pdf"], a:has-text("Download PDF"), a:has-text("PDF")').first
    if btn.count() > 0:
        href = btn.get_attribute("href")
        if href:
            if href.startswith("/"):
                m_origin = re.match(r"^(https?://[^/]+)", page.url)
                if m_origin:
                    href = m_origin.group(1) + href
            if href.startswith("http"):
                data, err = _fetch_via_request(page, href, timeout_ms)
                saved = _try_save_bytes(data, err, out_path, "generic_link_href", min_bytes)
                if saved:
                    return saved

        ok, _, size = _try_click_download(page, btn, out_path, timeout_ms, min_bytes)
        if ok:
            return DownloadResult(status="downloaded", local_file=os.path.basename(out_path), bytes_written=size, method="generic_click_download")

    url, title, body = _safe_inspect_page(page)
    b_status, b_reason = detect_page_block(url, title, body)
    if b_status:
        return DownloadResult(status=b_status, error=b_reason)
    return DownloadResult(status="failed", error=f"No valid PDF found on page {page.url}")


ADAPTERS = {
    "ieee": download_ieee,
    "sciencedirect": download_sciencedirect,
    "iop": download_iop,
    "wiley": download_wiley,
    "acs": download_acs,
    "cnki": download_cnki,
}


def download_single_paper(
    page: Any,
    item: dict[str, Any],
    out_path: str,
    timeout_ms: int = 45000,
    min_bytes: int = DEFAULT_MIN_PDF_BYTES,
) -> DownloadResult:
    """Dispatch a paper entry to the appropriate publisher adapter."""
    publisher = item.get("publisher", "other")
    adapter = ADAPTERS.get(publisher, download_generic)
    try:
        return adapter(page, item, out_path, timeout_ms=timeout_ms, min_bytes=min_bytes)
    except Exception as exc:
        return DownloadResult(status="failed", error=str(exc))


def sleep_with_jitter(delay_min: float, delay_max: float) -> float:
    """Sleep for a randomized interval between delay_min and delay_max seconds."""
    low = max(0.0, float(delay_min))
    high = max(low, float(delay_max))
    duration = random.uniform(low, high)
    if duration > 0:
        time.sleep(duration)
    return duration
