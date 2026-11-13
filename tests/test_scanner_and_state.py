import json
import os
import sys
import tempfile
import unittest

SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "skills", "academic-paper-downloader", "scripts")
)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import paper_downloader  # noqa: E402
from publishers import save_verified_pdf, verify_pdf_bytes, verify_pdf_file  # noqa: E402


class TestScannerAndState(unittest.TestCase):
    def test_verify_pdf_bytes_and_atomic_save(self):
        valid_pdf = b"%PDF-1.5\n" + (b"0" * 1500)
        ok, _ = verify_pdf_bytes(valid_pdf)
        self.assertTrue(ok)

        fake_html = b"<!DOCTYPE html><html><body>Login required</body></html>" + (b" " * 2000)
        ok_html, reason_html = verify_pdf_bytes(fake_html)
        self.assertFalse(ok_html)
        self.assertIn("%PDF-", reason_html)

        tiny_pdf = b"%PDF-1.5\nshort"
        ok_tiny, reason_tiny = verify_pdf_bytes(tiny_pdf)
        self.assertFalse(ok_tiny)
        self.assertIn("too small", reason_tiny)

        with tempfile.TemporaryDirectory() as tmp:
            bad_path = os.path.join(tmp, "bad.pdf")
            ok_save, _ = save_verified_pdf(fake_html, bad_path)
            self.assertFalse(ok_save)
            self.assertFalse(os.path.exists(bad_path))

            good_path = os.path.join(tmp, "good.pdf")
            ok_good, _ = save_verified_pdf(valid_pdf, good_path)
            self.assertTrue(ok_good)
            self.assertTrue(os.path.exists(good_path))
            ok_file, _, size = verify_pdf_file(good_path)
            self.assertTrue(ok_file)
            self.assertEqual(size, len(valid_pdf))

    def test_scan_detects_matched_missing_and_fake_pdfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_dir = os.path.join(tmp, "pdfs")
            os.makedirs(pdf_dir)
            list_path = os.path.join(tmp, "papers.md")
            state_path = os.path.join(tmp, "missing_papers.json")

            with open(list_path, "w", encoding="utf-8") as f:
                f.write(
                    "| Code | Title | DOI |\n"
                    "|---|---|---|\n"
                    "| S01 | Valid Local Paper | 10.1109/EXAMPLE.1 |\n"
                    "| S02 | Fake HTML Paper | 10.1016/j.example.2 |\n"
                    "| S03 | Title Matched Paper | 10.1088/1234/3 |\n"
                )

            # S01: valid PDF matched by code prefix
            with open(os.path.join(pdf_dir, "S01_Valid Local Paper.pdf"), "wb") as f:
                f.write(b"%PDF-1.4\n" + (b"x" * 2048))

            # S02: HTML error page masquerading as PDF -> must NOT count as downloaded
            with open(os.path.join(pdf_dir, "S02_Fake HTML Paper.pdf"), "wb") as f:
                f.write(b"<html><body>Radware challenge</body></html>" + (b" " * 2048))

            # S03: valid PDF matched by normalized title without code prefix
            with open(os.path.join(pdf_dir, "Title Matched Paper.pdf"), "wb") as f:
                f.write(b"%PDF-1.7\n" + (b"y" * 2048))

            rc = paper_downloader.main(["scan", "--input", list_path, "--pdf-dir", pdf_dir, "--state", state_path])
            self.assertEqual(rc, 0)

            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)

            self.assertEqual(state["summary"]["total"], 3)
            self.assertEqual(state["summary"]["downloaded"], 2)
            self.assertEqual(state["summary"]["pending"], 1)
            self.assertEqual(len(state["invalid_pdfs"]), 1)
            self.assertEqual(state["invalid_pdfs"][0]["file"], "S02_Fake HTML Paper.pdf")

            by_code = {p["code"]: p for p in state["papers"]}
            self.assertEqual(by_code["S01"]["status"], "downloaded")
            self.assertEqual(by_code["S02"]["status"], "pending")
            self.assertEqual(by_code["S03"]["status"], "downloaded")


if __name__ == "__main__":
    unittest.main()
