import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "skills", "academic-paper-downloader", "scripts")
)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from publishers import detect_page_block, download_single_paper  # noqa: E402

CLI_SCRIPT = os.path.join(SCRIPTS_DIR, "paper_downloader.py")


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def body(self) -> bytes:
        return self._body


class _FakeRequest:
    def __init__(self, handler):
        self._handler = handler

    def get(self, url: str, timeout: int = 45000):
        return self._handler(url)


class _FakeLocator:
    def __init__(self, href: str | None = None, count: int = 0):
        self._href = href
        self._count = count

    @property
    def first(self):
        return self

    def count(self) -> int:
        return self._count

    def get_attribute(self, name: str):
        return self._href if name == "href" else None

    def click(self):
        return None


class _FakePage:
    def __init__(self, start_url: str, title: str = "Article", body_text: str = "", request_handler=None, locator_href: str | None = None, js_result=None):
        self.url = start_url
        self._title = title
        self._body_text = body_text
        self.request = _FakeRequest(request_handler or (lambda u: _FakeResponse(b"")))
        self._locator_href = locator_href
        self._js_result = js_result

    def goto(self, url: str, wait_until: str = "load", timeout: int = 45000):
        if "pdfft" in url:
            self.url = "https://pdf.sciencedirectassets.com/sample.pdf"
        elif "doi.org/10.1109/" in url:
            self.url = "https://ieeexplore.ieee.org/document/99988877/"
        elif "doi.org/10.1088/" in url:
            self.url = "https://iopscience.iop.org/article/10.1088/1234/5"
        elif "doi.org/10.1002/" in url:
            self.url = "https://onlinelibrary.wiley.com/doi/full/10.1002/example.1"
        else:
            self.url = url

    def title(self) -> str:
        return self._title

    def evaluate(self, script: str, arg=None):
        if "innerText" in script:
            return self._body_text
        if self._js_result is not None:
            return self._js_result
        return ""

    def locator(self, selector: str):
        if self._locator_href:
            return _FakeLocator(href=self._locator_href, count=1)
        return _FakeLocator(href=None, count=0)

    def wait_for_url(self, predicate, timeout: int = 30000):
        return None


class TestPublishersAndCLI(unittest.TestCase):
    def test_detect_page_block(self):
        st, _ = detect_page_block("https://validate.perfdrive.com/abc", "Radware", "")
        self.assertEqual(st, "captcha_required")

        st_cf, _ = detect_page_block("https://sciencedirect.com/article", "Just a moment...", "Verify you are human")
        self.assertEqual(st_cf, "captcha_required")

        st_cnki, _ = detect_page_block("https://kns.cnki.net/verify", "安全验证 - 中国知网", "请完成滑动验证")
        self.assertEqual(st_cnki, "captcha_required")

        st_rate, _ = detect_page_block("https://ieeexplore.ieee.org", "Error", "429 Too Many Requests")
        self.assertEqual(st_rate, "rate_limited")

        st_pay, _ = detect_page_block("https://example.com", "Article", "Please purchase PDF or institutional login")
        self.assertEqual(st_pay, "unauthorized_or_paywall")

        st_ok, _ = detect_page_block("https://ieeexplore.ieee.org/document/1", "Paper Title", "Abstract text")
        self.assertIsNone(st_ok)

    def test_ieee_adapter_mock(self):
        valid_pdf = b"%PDF-1.6\n" + (b"a" * 2048)
        page = _FakePage(
            start_url="https://doi.org/10.1109/EXAMPLE.1",
            request_handler=lambda u: _FakeResponse(valid_pdf) if "arnumber=99988877" in u else _FakeResponse(b"nope"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "P001_IEEE.pdf")
            res = download_single_paper(page, {"publisher": "ieee", "doi": "10.1109/EXAMPLE.1"}, out_path)
            self.assertEqual(res.status, "downloaded")
            self.assertEqual(res.method, "ieee_getPDF")
            self.assertTrue(os.path.exists(out_path))

    def test_iop_and_wiley_adapters_mock(self):
        valid_pdf = b"%PDF-1.5\n" + (b"b" * 2048)
        with tempfile.TemporaryDirectory() as tmp:
            iop_page = _FakePage(
                start_url="https://doi.org/10.1088/1234/5",
                request_handler=lambda u: _FakeResponse(valid_pdf) if u.endswith("/pdf") else _FakeResponse(b"html"),
            )
            iop_out = os.path.join(tmp, "P002_IOP.pdf")
            res_iop = download_single_paper(iop_page, {"publisher": "iop", "doi": "10.1088/1234/5"}, iop_out)
            self.assertEqual(res_iop.status, "downloaded")
            self.assertEqual(res_iop.method, "iop_direct_pdf")

            wiley_page = _FakePage(
                start_url="https://doi.org/10.1002/example.1",
                request_handler=lambda u: _FakeResponse(valid_pdf) if "/doi/pdfdirect/" in u else _FakeResponse(b"html"),
            )
            wiley_out = os.path.join(tmp, "P003_Wiley.pdf")
            res_wiley = download_single_paper(wiley_page, {"publisher": "wiley", "doi": "10.1002/example.1"}, wiley_out)
            self.assertEqual(res_wiley.status, "downloaded")
            self.assertEqual(res_wiley.method, "wiley_pdfdirect")

    def test_sciencedirect_and_generic_adapters_mock(self):
        import base64

        valid_pdf = b"%PDF-1.7\n" + (b"c" * 2048)
        b64_pdf = base64.b64encode(valid_pdf).decode("ascii")
        with tempfile.TemporaryDirectory() as tmp:
            sd_page = _FakePage(
                start_url="https://www.sciencedirect.com/science/article/pii/EXAMPLE123",
                locator_href="/science/article/pii/EXAMPLE123/pdfft",
                js_result={"b64": b64_pdf},
            )
            sd_out = os.path.join(tmp, "P005_SD.pdf")
            res_sd = download_single_paper(
                sd_page,
                {"publisher": "sciencedirect", "url": "https://www.sciencedirect.com/science/article/pii/EXAMPLE123"},
                sd_out,
            )
            self.assertEqual(res_sd.status, "downloaded")
            self.assertEqual(res_sd.method, "sciencedirect_js_blob")
            self.assertTrue(os.path.exists(sd_out))

            gen_page = _FakePage(
                start_url="https://arxiv.org/pdf/2401.00001.pdf",
                request_handler=lambda u: _FakeResponse(valid_pdf) if u.endswith(".pdf") else _FakeResponse(b"html"),
            )
            gen_out = os.path.join(tmp, "P006_Arxiv.pdf")
            res_gen = download_single_paper(
                gen_page,
                {"publisher": "arxiv", "url": "https://arxiv.org/pdf/2401.00001.pdf"},
                gen_out,
            )
            self.assertEqual(res_gen.status, "downloaded")
            self.assertEqual(res_gen.method, "generic_direct_url")

    def test_captcha_stops_adapter_cleanly(self):
        page = _FakePage(
            start_url="https://kns.cnki.net/example",
            title="安全验证",
            body_text="请完成安全验证后继续",
        )
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "P004_CNKI.pdf")
            res = download_single_paper(page, {"publisher": "cnki", "url": "https://kns.cnki.net/example"}, out_path)
            self.assertEqual(res.status, "captcha_required")
            self.assertFalse(os.path.exists(out_path))

    def test_cli_help_and_end_to_end_smoke(self):
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        for args in ([], ["parse", "--help"], ["scan", "--help"], ["download", "--help"], ["check-cdp", "--help"], ["status", "--help"]):
            cmd = [sys.executable, CLI_SCRIPT] + (args if args else ["--help"])
            proc = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            parsed_json = os.path.join(tmp, "parsed.json")
            state_json = os.path.join(tmp, "missing_papers.json")
            pdf_dir = os.path.join(tmp, "papers")

            p1 = subprocess.run(
                [
                    sys.executable,
                    CLI_SCRIPT,
                    "parse",
                    "--input",
                    os.path.join(repo_root, "examples", "sample_review.md"),
                    os.path.join(repo_root, "examples", "sample_refs.bib"),
                    os.path.join(repo_root, "examples", "sample_dois.txt"),
                    "--output",
                    parsed_json,
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(p1.returncode, 0, msg=p1.stderr)
            with open(parsed_json, "r", encoding="utf-8") as f:
                parsed = json.load(f)
            # S01 & M02 overlap across sample_review.md, sample_refs.bib, and sample_dois.txt
            self.assertEqual(parsed["total"], 7)

            p2 = subprocess.run(
                [sys.executable, CLI_SCRIPT, "scan", "--input", parsed_json, "--pdf-dir", pdf_dir, "--state", state_json],
                capture_output=True,
                text=True,
            )
            self.assertEqual(p2.returncode, 0, msg=p2.stderr)

            p3 = subprocess.run(
                [sys.executable, CLI_SCRIPT, "download", "--state", state_json, "--pdf-dir", pdf_dir, "--dry-run"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(p3.returncode, 0, msg=p3.stderr)
            self.assertIn("Dry run: 7 paper(s)", p3.stdout)

            p4 = subprocess.run(
                [sys.executable, CLI_SCRIPT, "status", "--state", state_json],
                capture_output=True,
                text=True,
            )
            self.assertEqual(p4.returncode, 0, msg=p4.stderr)
            self.assertIn("Total: 7 | Downloaded: 0 | Pending: 7", p4.stdout)

            # Unreachable port should exit cleanly with code 1 and launch instructions
            p5 = subprocess.run(
                [sys.executable, CLI_SCRIPT, "check-cdp", "--cdp-url", "http://127.0.0.1:59999", "--timeout", "1.0"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(p5.returncode, 1)
            self.assertIn("--remote-debugging-port=9222", p5.stderr)


if __name__ == "__main__":
    unittest.main()
