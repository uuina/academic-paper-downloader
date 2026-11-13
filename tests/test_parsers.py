import os
import sys
import unittest

SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "skills", "academic-paper-downloader", "scripts")
)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from parsers import (  # noqa: E402
    classify_publisher,
    deduplicate_papers,
    normalize_doi,
    parse_bibtex,
    parse_markdown,
    parse_plain_list,
    parse_ris,
    sanitize_filename,
)


class TestParsers(unittest.TestCase):
    def test_normalize_doi_preserves_balanced_wiley_parens_and_strips_wrapper(self):
        wiley = "https://doi.org/10.1002/1521-4117(200001)10:1<1::AID-EX1>3.0.CO;2-0"
        self.assertEqual(
            normalize_doi(wiley),
            "10.1002/1521-4117(200001)10:1<1::aid-ex1>3.0.co;2-0",
        )
        wrapped = "(DOI: 10.1109/EXAMPLE.2024.0001)."
        self.assertEqual(normalize_doi(wrapped), "10.1109/example.2024.0001")
        self.assertEqual(normalize_doi("no doi here"), "")

    def test_classify_publisher(self):
        self.assertEqual(classify_publisher("10.1109/example.2024.1"), "ieee")
        self.assertEqual(classify_publisher("10.1016/j.example.2024.1"), "sciencedirect")
        self.assertEqual(classify_publisher("10.1088/1361-0000/abc"), "iop")
        self.assertEqual(classify_publisher("10.1002/example.1234"), "wiley")
        self.assertEqual(classify_publisher("10.1049/example.2024.1"), "wiley")
        self.assertEqual(classify_publisher("10.1021/example.2024.1"), "acs")
        self.assertEqual(classify_publisher("", "https://kns.cnki.net/kcms2/article/abstract"), "cnki")
        self.assertEqual(classify_publisher("10.1007/example-2023-1"), "springer")
        self.assertEqual(classify_publisher("", "https://arxiv.org/pdf/2401.00001.pdf"), "arxiv")
        self.assertEqual(classify_publisher("10.9999/unknown"), "other")

    def test_parse_markdown_table_and_list(self):
        md = """
# Review

| Code | Title | Year | DOI |
|---|---|---|---|
| S01 | Example Graph Survey | 2023 | https://doi.org/10.1109/EXAMPLE.2023.1 |
| S02 | Foundation Models Study | 2024 | 10.1016/j.example.2024.2 |

- [M01] Efficient Attention — https://doi.org/10.1088/1234-5678/3
- [M02] [Physics Surrogates](https://onlinelibrary.wiley.com/doi/10.1002/example.4) (2024)
"""
        entries = parse_markdown(md, source="review.md")
        self.assertEqual(len(entries), 4)
        self.assertEqual(entries[0]["code"], "S01")
        self.assertEqual(entries[0]["publisher"], "ieee")
        self.assertEqual(entries[1]["publisher"], "sciencedirect")
        self.assertEqual(entries[2]["code"], "M01")
        self.assertEqual(entries[2]["publisher"], "iop")
        self.assertEqual(entries[3]["code"], "M02")
        self.assertEqual(entries[3]["publisher"], "wiley")

    def test_parse_plain_list(self):
        txt = """
# comment
10.1109/EXAMPLE.2024.100

X01 | Sample Preprint | https://arxiv.org/pdf/2401.00001.pdf
"""
        entries = parse_plain_list(txt, source="dois.txt")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["doi"], "10.1109/example.2024.100")
        self.assertEqual(entries[1]["code"], "X01")
        self.assertEqual(entries[1]["title"], "Sample Preprint")
        self.assertEqual(entries[1]["publisher"], "arxiv")

    def test_parse_bibtex_nested_braces(self):
        bib = """
@comment{ignore me}
@article{doe2024example,
  title = {{Physics-Informed} Modeling of {3D} Flows},
  author = "Doe, Jane",
  year = {2024},
  doi = {10.1021/acs.example.2024.99}
}
"""
        entries = parse_bibtex(bib, source="refs.bib")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["code"], "doe2024example")
        self.assertEqual(entries[0]["title"], "Physics-Informed Modeling of 3D Flows")
        self.assertEqual(entries[0]["publisher"], "acs")
        self.assertEqual(entries[0]["year"], "2024")

    def test_parse_ris(self):
        ris = """
TY  - JOUR
ID  - R101
TI  - Example Signal Processing Paper
DO  - 10.1109/EXAMPLE.2022.555
PY  - 2022
ER  -

TY  - JOUR
T1  - Chinese Journal Sample
UR  - https://kns.cnki.net/kcms2/article/abstract?v=example
PY  - 2023/05/01
ER  -
"""
        entries = parse_ris(ris, source="refs.ris")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["code"], "R101")
        self.assertEqual(entries[0]["publisher"], "ieee")
        self.assertEqual(entries[1]["publisher"], "cnki")
        self.assertEqual(entries[1]["year"], "2023")

    def test_deduplicate_merges_aliases_and_assigns_codes(self):
        entries = [
            {"code": "S01", "title": "Graph Survey", "doi": "10.1109/EXAMPLE.1", "url": "", "year": "", "source": "a.md"},
            {"code": "doe2023", "title": "Graph Survey: A Comprehensive Review", "doi": "https://doi.org/10.1109/example.1", "url": "https://ieeexplore.ieee.org/document/999", "year": "2023", "source": "b.bib"},
            {"code": "", "title": "Standalone Paper", "doi": "", "url": "https://arxiv.org/pdf/2401.00001.pdf", "year": "2024", "source": "c.txt"},
        ]
        deduped = deduplicate_papers(entries)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0]["code"], "S01")
        self.assertEqual(deduped[0]["aliases"], ["S01", "doe2023"])
        self.assertEqual(deduped[0]["title"], "Graph Survey: A Comprehensive Review")
        self.assertEqual(deduped[0]["url"], "https://ieeexplore.ieee.org/document/999")
        self.assertEqual(deduped[0]["year"], "2023")
        self.assertEqual(deduped[1]["code"], "P001")

    def test_legacy_wiley_url_with_angle_brackets_and_parens(self):
        wiley_url = "https://doi.org/10.1002/1521-4117(200001)10:1<1::AID-EX1>3.0.CO;2-0"
        md_entries = parse_markdown(f"- [M01] [Legacy Wiley Paper]({wiley_url})", source="legacy.md")
        self.assertEqual(len(md_entries), 1)
        self.assertEqual(md_entries[0]["doi"], "10.1002/1521-4117(200001)10:1<1::aid-ex1>3.0.co;2-0")
        self.assertEqual(md_entries[0]["url"], "https://doi.org/10.1002/1521-4117(200001)10:1<1::aid-ex1>3.0.co;2-0")

        plain_entries = parse_plain_list(wiley_url, source="legacy.txt")
        self.assertEqual(len(plain_entries), 1)
        self.assertEqual(plain_entries[0]["url"], "https://doi.org/10.1002/1521-4117(200001)10:1<1::aid-ex1>3.0.co;2-0")

    def test_bibtex_quote_inside_braces_and_csv_comma_title(self):
        bib = """
@article{k1,
  title = {Characterization of a 5" Prototype Sensor},
  doi = {10.1109/EXAMPLE.1}
}
@article{k2,
  title = {Second Valid Entry},
  doi = {10.1016/j.example.2}
}
"""
        bib_entries = parse_bibtex(bib, source="quotes.bib")
        self.assertEqual(len(bib_entries), 2)
        self.assertEqual(bib_entries[0]["code"], "k1")
        self.assertEqual(bib_entries[1]["code"], "k2")

        csv_txt = 'S01,"A Study on X, Y, and Z",10.1109/EXAMPLE.3\nS02 10.1088/1234/4\n'
        plain_entries = parse_plain_list(csv_txt, source="list.txt")
        self.assertEqual(len(plain_entries), 2)
        self.assertEqual(plain_entries[0]["code"], "S01")
        self.assertEqual(plain_entries[0]["title"], "A Study on X, Y, and Z")
        self.assertEqual(plain_entries[1]["code"], "S02")
        self.assertEqual(plain_entries[1]["title"], "")

    def test_deduplicate_disambiguates_colliding_codes(self):
        entries = [
            {"code": "S01", "title": "First Distinct Paper", "doi": "10.1109/EXAMPLE.1"},
            {"code": "S01", "title": "Second Distinct Paper", "doi": "10.1016/j.example.2"},
        ]
        deduped = deduplicate_papers(entries)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0]["code"], "S01")
        self.assertEqual(deduped[1]["code"], "S01_2")
        self.assertNotIn("S01", deduped[1]["aliases"])

    def test_sanitize_filename(self):
        self.assertEqual(sanitize_filename('A/B:C*D?"E<F>G|H'), "A_B_C_D_E_F_G_H")


if __name__ == "__main__":
    unittest.main()
