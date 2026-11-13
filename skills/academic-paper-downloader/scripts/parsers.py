"""Adaptive literature list parsers and deduplication utilities.

Supports:
- Markdown review lists (tables, coded bullet/numbered lists, link lists)
- Plain DOI / URL lists (bare lines, pipe/tab/comma delimited)
- BibTeX (.bib) exports
- RIS (.ris) exports
"""

from __future__ import annotations

import csv
import os
import re
from typing import Any

DOI_RE = re.compile(r"(10\.\d{4,9}/[^\s\"'`]+)", re.IGNORECASE)
URL_RE = re.compile(r"(https?://[^\s\"'`]+)", re.IGNORECASE)
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
CODE_BRACKET_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)?\s*\*{0,2}\[([A-Za-z]{0,6}\d{1,5}[A-Za-z]?)\]\*{0,2}\s*(.+)$")
CODE_PREFIX_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)?\s*\*{0,2}([A-Za-z]{1,6}\d{1,5}[A-Za-z]?)\*{0,2}\s*[:：\-—]\s*(.+)$")


def _extract_md_links(text: str) -> list[tuple[str, str, int, int]]:
    """Extract Markdown links [label](https://...) with balanced parenthesis support."""
    results: list[tuple[str, str, int, int]] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "[":
            close_bracket = text.find("]", i + 1)
            if close_bracket != -1 and close_bracket + 1 < n and text[close_bracket + 1] == "(":
                label = text[i + 1 : close_bracket]
                url_start = close_bracket + 2
                if text[url_start:].lower().startswith(("http://", "https://")):
                    depth = 1
                    j = url_start
                    while j < n and depth > 0:
                        ch = text[j]
                        if ch in " \t\r\n":
                            break
                        if ch == "(":
                            depth += 1
                        elif ch == ")":
                            depth -= 1
                            if depth == 0:
                                break
                        j += 1
                    if depth == 0:
                        url = text[url_start:j]
                        results.append((label, url, i, j + 1))
                        i = j + 1
                        continue
        i += 1
    return results


def _strip_unbalanced_trailing(value: str) -> str:
    """Strip trailing punctuation while preserving balanced parentheses/brackets."""
    val = value.strip()
    while val and val[-1] in ".,;]}>\"'`":
        if val[-1] == ">" and val.count("<") >= val.count(">"):
            break
        if val[-1] == "]" and val.count("[") >= val.count("]"):
            break
        if val[-1] == "}" and val.count("{") >= val.count("}"):
            break
        val = val[:-1]
    while val.endswith(")") and val.count(")") > val.count("("):
        val = val[:-1]
    return val.strip()


def normalize_doi(raw: str | None) -> str:
    """Extract and normalize a DOI string (lowercase, without resolver URL prefix)."""
    if not raw:
        return ""
    cleaned = raw.strip()
    cleaned = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^doi:\s*", "", cleaned, flags=re.IGNORECASE)
    match = DOI_RE.search(cleaned)
    if not match:
        return ""
    doi = _strip_unbalanced_trailing(match.group(1))
    return doi.lower()


def normalize_url(raw: str | None) -> str:
    """Normalize a URL or convert DOI URLs to canonical https://doi.org/<doi>."""
    if not raw:
        return ""
    val = _strip_unbalanced_trailing(raw)
    if not val.lower().startswith(("http://", "https://")):
        doi = normalize_doi(val)
        return f"https://doi.org/{doi}" if doi else ""
    if re.match(r"^https?://(?:dx\.)?doi\.org/", val, re.IGNORECASE):
        doi = normalize_doi(val)
        return f"https://doi.org/{doi}" if doi else val
    return val.rstrip("/")


def normalize_title(title: str | None) -> str:
    """Normalize a paper title for fuzzy matching and deduplication."""
    if not title:
        return ""
    t = title.lower()
    t = re.sub(r"[\\/*?:\"<>|\-_.,;:()\[\]{}'`~!@#$%^&+=]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def sanitize_filename(name: str, max_length: int = 160) -> str:
    """Sanitize a string into a safe cross-platform filename."""
    cleaned = re.sub(r"[\\/*?:\"<>|\r\n\t]+", "_", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip(" .")
    return cleaned or "untitled"


def classify_publisher(doi: str = "", url: str = "") -> str:
    """Classify an entry into a supported publisher adapter key."""
    combined = f"{doi} {url}".lower()
    if "10.1109/" in combined or "ieeexplore.ieee.org" in combined:
        return "ieee"
    if (
        "10.1016/" in combined
        or "sciencedirect.com" in combined
        or "linkinghub.elsevier.com" in combined
        or "elsevier" in combined
    ):
        return "sciencedirect"
    if "10.1088/" in combined or "iopscience.iop.org" in combined:
        return "iop"
    if (
        "10.1002/" in combined
        or "10.1049/" in combined
        or "10.1111/" in combined
        or "onlinelibrary.wiley.com" in combined
    ):
        return "wiley"
    if "10.1021/" in combined or "pubs.acs.org" in combined:
        return "acs"
    if "cnki.net" in combined:
        return "cnki"
    if "10.1007/" in combined or "link.springer.com" in combined or "10.1038/" in combined or "nature.com" in combined:
        return "springer"
    if "10.1108/" in combined or "emerald.com" in combined:
        return "emerald"
    if "arxiv.org" in combined or "10.48550/arxiv" in combined:
        return "arxiv"
    return "other"


def _clean_markdown_text(text: str) -> str:
    links = _extract_md_links(text)
    if links:
        parts: list[str] = []
        last = 0
        for label, _, start, end in links:
            parts.append(text[last:start])
            parts.append(label)
            last = end
        parts.append(text[last:])
        text = "".join(parts)
    text = URL_RE.sub("", text)
    text = re.sub(r"(?:https?://(?:dx\.)?doi\.org/)?10\.\d{4,9}/[^\s\"'`]+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bdoi\s*[:：]?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[`*_~]+", "", text)
    text = re.sub(r"[《》「」【】]", "", text)
    text = re.sub(r"\s*[|｜]\s*", " ", text)
    text = re.sub(r"^[\s\-—–:：,;.()\[\]]+|[\s\-—–:：,;.()\[\]]+$", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _make_entry(
    code: str = "",
    title: str = "",
    doi: str = "",
    url: str = "",
    year: str = "",
    source: str = "",
) -> dict[str, Any]:
    norm_doi = normalize_doi(doi) or normalize_doi(url)
    norm_url = normalize_url(url)
    if norm_doi and (not norm_url or re.match(r"^https?://(?:dx\.)?doi\.org/", norm_url, re.IGNORECASE)):
        norm_url = f"https://doi.org/{norm_doi}"
    clean_title = re.sub(r"\s+", " ", (title or "").strip())
    return {
        "code": code.strip(),
        "title": clean_title,
        "doi": norm_doi,
        "url": norm_url,
        "year": year.strip(),
        "publisher": classify_publisher(norm_doi, norm_url),
        "source": source,
    }


def _parse_markdown_table(lines: list[str], source: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:\-|]+\|$", lines[i + 1].strip()):
            headers = [c.strip().lower() for c in line.strip("|").split("|")]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                row_text = " | ".join(cells)
                doi = normalize_doi(row_text)
                urls = [u for _, u, _, _ in _extract_md_links(row_text)] + URL_RE.findall(row_text)
                url = next((normalize_url(u) for u in urls if normalize_url(u)), "")

                code = ""
                title = ""
                year = ""
                for idx, cell in enumerate(cells):
                    hdr = headers[idx] if idx < len(headers) else ""
                    if not code and any(k in hdr for k in ("code", "id", "编号", "序号", "#")):
                        m_code = re.search(r"[A-Za-z]{0,6}\d{1,5}[A-Za-z]?", cell)
                        if m_code:
                            code = m_code.group(0)
                    elif not title and any(k in hdr for k in ("title", "paper", "题名", "标题", "文献", "名称")):
                        title = _clean_markdown_text(cell)
                    elif not year and any(k in hdr for k in ("year", "年份", "date")):
                        m_yr = re.search(r"(19|20)\d{2}", cell)
                        if m_yr:
                            year = m_yr.group(0)

                if not code and cells:
                    m_first = re.fullmatch(r"\[?([A-Za-z]{1,6}\d{1,5}[A-Za-z]?)\]?", cells[0].strip("*` "))
                    if m_first:
                        code = m_first.group(1)

                if not title:
                    candidates = [_clean_markdown_text(c) for c in cells]
                    candidates = [c for c in candidates if c and c != code and not re.fullmatch(r"\d{4}", c)]
                    if candidates:
                        title = max(candidates, key=len)

                if doi or url:
                    entries.append(_make_entry(code=code, title=title, doi=doi, url=url, year=year, source=source))
                i += 1
            continue
        i += 1
    return entries


def parse_markdown(text: str, source: str = "markdown") -> list[dict[str, Any]]:
    """Parse a Markdown literature review table or list."""
    lines = text.splitlines()
    entries = _parse_markdown_table(lines, source)
    seen_lines: set[int] = set()

    # Mark table lines so we don't double-parse table rows as list items
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:\-|]+\|$", lines[i + 1].strip()):
            seen_lines.add(i)
            seen_lines.add(i + 1)
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                seen_lines.add(i)
                i += 1
            continue
        i += 1

    for idx, raw_line in enumerate(lines):
        if idx in seen_lines:
            continue
        line = raw_line.strip()
        if not line or line.startswith("```"):
            continue

        doi = normalize_doi(line)
        md_links = _extract_md_links(line)
        raw_urls = URL_RE.findall(line)
        url = ""
        if md_links:
            url = normalize_url(md_links[0][1])
        elif raw_urls:
            url = normalize_url(raw_urls[0])

        if not doi and not url:
            continue

        code = ""
        rest = line
        m_bracket = CODE_BRACKET_RE.match(line)
        m_prefix = CODE_PREFIX_RE.match(line)
        if m_bracket:
            code, rest = m_bracket.group(1), m_bracket.group(2)
        elif m_prefix:
            code, rest = m_prefix.group(1), m_prefix.group(2)

        title = ""
        if md_links and len(_clean_markdown_text(rest)) < 8:
            title = _clean_markdown_text(md_links[0][0])
        if not title:
            title = _clean_markdown_text(rest)
        if not title and md_links:
            title = _clean_markdown_text(md_links[0][0])

        m_yr = re.search(r"\b(19\d{2}|20\d{2})\b", line)
        year = m_yr.group(1) if m_yr else ""
        entries.append(_make_entry(code=code, title=title, doi=doi, url=url, year=year, source=source))

    return entries


def parse_plain_list(text: str, source: str = "doi_list") -> list[dict[str, Any]]:
    """Parse plain DOI/URL lists or delimited text (pipe, tab, or comma)."""
    entries: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        delimiter = None
        if "|" in line:
            delimiter = "|"
        elif "\t" in line:
            delimiter = "\t"
        elif "," in line and not line.lower().startswith("http"):
            # Only treat comma as delimiter when multiple columns look present
            parts_probe = [p.strip() for p in line.split(",")]
            if any(normalize_doi(p) or normalize_url(p) for p in parts_probe):
                delimiter = ","

        if delimiter:
            if delimiter == ",":
                parts = [p.strip() for p in next(csv.reader([line])) if p.strip()]
            else:
                parts = [p.strip() for p in line.split(delimiter) if p.strip()]
            code = ""
            title = ""
            doi = ""
            url = ""
            leftovers: list[str] = []
            for p in parts:
                p_doi = normalize_doi(p)
                p_url = normalize_url(p) if p.lower().startswith(("http://", "https://")) else ""
                if p_doi and not doi:
                    doi = p_doi
                    if p_url:
                        url = p_url
                elif p_url and not url:
                    url = p_url
                elif not code and re.fullmatch(r"[A-Za-z]{0,6}\d{1,5}[A-Za-z]?", p):
                    code = p
                else:
                    leftovers.append(p)
            if leftovers:
                title = max(leftovers, key=len)
            if doi or url:
                entries.append(_make_entry(code=code, title=title, doi=doi, url=url, source=source))
            continue

        doi = normalize_doi(line)
        m_url = URL_RE.search(line)
        url = normalize_url(m_url.group(1)) if m_url else ""
        if doi or url:
            leftover = URL_RE.sub("", line)
            leftover = DOI_RE.sub("", leftover)
            leftover = re.sub(r"\bdoi\s*[:：]?\s*", "", leftover, flags=re.IGNORECASE)
            leftover = re.sub(r"^[\s\-:,;|]+|[\s\-:,;|]+$", "", leftover).strip()
            code = ""
            if re.fullmatch(r"[A-Za-z]{1,6}\d{1,5}[A-Za-z]?", leftover):
                code = leftover
                leftover = ""
            entries.append(_make_entry(code=code, title=leftover, doi=doi, url=url, source=source))

    return entries


def _clean_bibtex_value(val: str) -> str:
    val = re.sub(r"\\url\{([^}]*)\}", r"\1", val)
    val = re.sub(r"\s+", " ", val).strip()
    # Iteratively strip outer braces while preserving content
    while len(val) >= 2 and ((val[0] == "{" and val[-1] == "}") or (val[0] == '"' and val[-1] == '"')):
        if val[0] == "{":
            depth = 0
            balanced = True
            for idx, ch in enumerate(val):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0 and idx < len(val) - 1:
                        balanced = False
                        break
            if not balanced:
                break
        val = val[1:-1].strip()
    val = val.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", val).strip()


def parse_bibtex(text: str, source: str = "bibtex") -> list[dict[str, Any]]:
    """Parse BibTeX entries using a brace-aware scanner."""
    entries: list[dict[str, Any]] = []
    pos = 0
    length = len(text)

    while pos < length:
        at_idx = text.find("@", pos)
        if at_idx == -1:
            break
        m_head = re.match(r"@([A-Za-z]+)\s*([{(])", text[at_idx:])
        if not m_head:
            pos = at_idx + 1
            continue
        entry_type = m_head.group(1).lower()
        open_ch = m_head.group(2)
        close_ch = "}" if open_ch == "{" else ")"
        start = at_idx + m_head.end()
        depth = 1
        brace_depth = 0
        i = start
        in_quotes = False
        while i < length and depth > 0:
            ch = text[i]
            prev = text[i - 1] if i > 0 else ""
            if ch == '"' and prev != "\\":
                if depth == 1 and brace_depth == 0:
                    in_quotes = not in_quotes
            elif not in_quotes:
                if open_ch == "{":
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                else:
                    if ch == "{" and prev != "\\":
                        brace_depth += 1
                    elif ch == "}" and prev != "\\" and brace_depth > 0:
                        brace_depth -= 1
                    elif brace_depth == 0:
                        if ch == "(":
                            depth += 1
                        elif ch == ")":
                            depth -= 1
            i += 1
        body = text[start : i - 1].strip()
        pos = i

        if entry_type in ("comment", "preamble", "string"):
            continue

        comma_idx = body.find(",")
        if comma_idx == -1:
            continue
        citekey = body[:comma_idx].strip()
        fields_str = body[comma_idx + 1 :]

        fields: dict[str, str] = {}
        idx = 0
        flen = len(fields_str)
        while idx < flen:
            while idx < flen and fields_str[idx] in " \t\r\n,":
                idx += 1
            if idx >= flen:
                break
            m_name = re.match(r"([A-Za-z0-9_\-]+)\s*=", fields_str[idx:])
            if not m_name:
                break
            fname = m_name.group(1).lower()
            idx += m_name.end()
            while idx < flen and fields_str[idx] in " \t\r\n":
                idx += 1
            if idx >= flen:
                break

            if fields_str[idx] == "{":
                bdepth = 1
                vstart = idx + 1
                idx += 1
                while idx < flen and bdepth > 0:
                    if fields_str[idx] == "{" and fields_str[idx - 1] != "\\":
                        bdepth += 1
                    elif fields_str[idx] == "}" and fields_str[idx - 1] != "\\":
                        bdepth -= 1
                    idx += 1
                raw_val = fields_str[vstart : idx - 1]
            elif fields_str[idx] == '"':
                vstart = idx + 1
                idx += 1
                while idx < flen and not (fields_str[idx] == '"' and fields_str[idx - 1] != "\\"):
                    idx += 1
                raw_val = fields_str[vstart:idx]
                idx += 1
            else:
                vstart = idx
                while idx < flen and fields_str[idx] not in ",\r\n":
                    idx += 1
                raw_val = fields_str[vstart:idx]

            fields[fname] = _clean_bibtex_value(raw_val)

        doi = normalize_doi(fields.get("doi", ""))
        url = normalize_url(fields.get("url", ""))
        title = fields.get("title", "")
        year = fields.get("year", "")
        if doi or url or title:
            entries.append(_make_entry(code=citekey, title=title, doi=doi, url=url, year=year, source=source))

    return entries


def parse_ris(text: str, source: str = "ris") -> list[dict[str, Any]]:
    """Parse RIS bibliographic records."""
    entries: list[dict[str, Any]] = []
    current: dict[str, list[str]] = {}
    last_tag = ""

    def flush_record(rec: dict[str, list[str]]) -> None:
        if not rec:
            return
        title = " ".join(rec.get("TI") or rec.get("T1") or rec.get("CT") or [])
        doi_raw = (rec.get("DO") or [""])[0]
        url_raw = (rec.get("UR") or rec.get("L1") or rec.get("L2") or [""])[0]
        code = (rec.get("ID") or [""])[0]
        year_raw = (rec.get("PY") or rec.get("Y1") or [""])[0]
        m_yr = re.search(r"(19|20)\d{2}", year_raw)
        year = m_yr.group(0) if m_yr else ""
        doi = normalize_doi(doi_raw) or normalize_doi(url_raw)
        url = normalize_url(url_raw)
        if doi or url or title:
            entries.append(_make_entry(code=code, title=title, doi=doi, url=url, year=year, source=source))

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r\n")
        m_tag = re.match(r"^([A-Z0-9]{2})\s{1,2}-\s*(.*)$", line)
        if m_tag:
            tag = m_tag.group(1)
            val = m_tag.group(2).strip()
            if tag == "ER":
                flush_record(current)
                current = {}
                last_tag = ""
            else:
                if tag == "TY" and current:
                    flush_record(current)
                    current = {}
                current.setdefault(tag, []).append(val)
                last_tag = tag
        elif last_tag and line.strip():
            current.setdefault(last_tag, []).append(line.strip())

    flush_record(current)
    return entries


def detect_format(path: str, text: str) -> str:
    """Detect input literature list format from extension and content."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".bib":
        return "bibtex"
    if ext == ".ris":
        return "ris"
    if ext in (".md", ".markdown"):
        return "markdown"

    if re.search(r"@(?:article|inproceedings|proceedings|book|misc|phdthesis|mastersthesis)\s*[{(]", text, re.I):
        return "bibtex"
    if re.search(r"^TY\s{1,2}-\s+", text, re.M):
        return "ris"
    if re.search(r"^\|.*\|\s*\n\|[\s:\-|]+\|", text, re.M) or MD_LINK_RE.search(text):
        return "markdown"
    return "doi"


def parse_file(path: str, fmt: str = "auto") -> list[dict[str, Any]]:
    """Parse a literature list file into normalized paper entries."""
    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read()
    resolved_fmt = detect_format(path, text) if fmt == "auto" else fmt
    source_name = os.path.basename(path)
    if resolved_fmt == "markdown":
        return parse_markdown(text, source=source_name)
    if resolved_fmt == "bibtex":
        return parse_bibtex(text, source=source_name)
    if resolved_fmt == "ris":
        return parse_ris(text, source=source_name)
    if resolved_fmt == "doi":
        return parse_plain_list(text, source=source_name)
    raise ValueError(f"Unsupported format: {resolved_fmt}")


def deduplicate_papers(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate parsed entries across DOIs, URLs, and normalized titles."""
    deduped: list[dict[str, Any]] = []
    key_to_idx: dict[str, int] = {}

    for raw in entries:
        doi = normalize_doi(raw.get("doi", ""))
        url = normalize_url(raw.get("url", ""))
        title = (raw.get("title") or "").strip()
        norm_title = normalize_title(title)
        code = (raw.get("code") or "").strip()
        year = (raw.get("year") or "").strip()
        source = (raw.get("source") or "").strip()

        keys: list[str] = []
        if doi:
            keys.append(f"doi:{doi}")
        if url and not url.startswith("https://doi.org/"):
            keys.append(f"url:{url.lower()}")
        elif url and not doi:
            keys.append(f"url:{url.lower()}")
        if norm_title and len(norm_title) >= 8:
            keys.append(f"title:{norm_title}")

        matched_idx = None
        for k in keys:
            if k in key_to_idx:
                matched_idx = key_to_idx[k]
                break

        if matched_idx is None:
            record = {
                "code": code,
                "aliases": [code] if code else [],
                "title": title,
                "doi": doi,
                "url": url or (f"https://doi.org/{doi}" if doi else ""),
                "year": year,
                "publisher": classify_publisher(doi, url),
                "sources": [source] if source else [],
            }
            matched_idx = len(deduped)
            deduped.append(record)
        else:
            rec = deduped[matched_idx]
            if code:
                if not rec["code"]:
                    rec["code"] = code
                if code not in rec["aliases"]:
                    rec["aliases"].append(code)
            if len(title) > len(rec["title"]):
                rec["title"] = title
            if doi and not rec["doi"]:
                rec["doi"] = doi
            if url and (not rec["url"] or (rec["url"].startswith("https://doi.org/") and not url.startswith("https://doi.org/"))):
                rec["url"] = url
            if year and not rec["year"]:
                rec["year"] = year
            rec["publisher"] = classify_publisher(rec["doi"], rec["url"])
            if source and source not in rec["sources"]:
                rec["sources"].append(source)

        for k in keys:
            key_to_idx[k] = matched_idx

    # Ensure every record has a deterministic, globally unique non-empty code
    used_codes: set[str] = set()
    auto_idx = 1
    for rec in deduped:
        code = rec["code"]
        if code:
            if code.lower() in used_codes:
                suffix = 2
                while f"{code}_{suffix}".lower() in used_codes:
                    suffix += 1
                new_code = f"{code}_{suffix}"
                rec["aliases"] = [new_code if a.lower() == code.lower() else a for a in rec["aliases"]]
                if new_code not in rec["aliases"]:
                    rec["aliases"].append(new_code)
                rec["code"] = new_code
            used_codes.add(rec["code"].lower())
        else:
            while f"p{auto_idx:03d}" in used_codes:
                auto_idx += 1
            rec["code"] = f"P{auto_idx:03d}"
            if rec["code"] not in rec["aliases"]:
                rec["aliases"].append(rec["code"])
            used_codes.add(rec["code"].lower())
            auto_idx += 1

    return deduped
