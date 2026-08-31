import unittest
from unittest.mock import patch
from pathlib import Path

import scripts.generate_blog as gb


class TestReferenceFallback(unittest.TestCase):
    def setUp(self):
        self.assets_dir = gb.ASSETS_IMG_DIR
        self.assets_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        # cleanup any test artifacts created under assets/blog-images
        for p in self.assets_dir.glob("test-fallback*"):
            try:
                p.unlink()
            except Exception:
                pass

    @patch("scripts.generate_blog.extract_reference_urls", return_value=[])
    @patch("scripts.generate_blog._http_get_json", return_value={"esearchresult": {"idlist": ["12345", "23456"]}})
    @patch("scripts.generate_blog._first_pmc_figure", return_value={"image_bytes": b"\xff\xd8\xff\x00", "label": "Fig 1", "caption": "cap", "license": "CC BY 4.0"})
    def test_build_reference_images_fallback_pmc(self, mock_first, mock_json, mock_extract):
        article = {
            "slug": "test-fallback",
            "title": "Test Title for Fallback",
            "meta_description": "desc",
            "category": "Testing",
            "intro_summary": "a brief summary",
            "html_body": "<p>No pubmed references here</p>",
            "faq": [],
        }

        images, notes = gb.build_reference_images(article)

        # At least one image entry should be returned
        self.assertTrue(len(images) >= 1, "Expected at least one image returned from fallback")

        # And the image file should have been written to assets/blog-images
        found_file = False
        for img in images:
            pub = img.get("public_path") or ""
            if pub.startswith("/assets/blog-images/"):
                path = gb.REPO_ROOT / pub.lstrip("/")
                if path.exists():
                    found_file = True
                    break

        self.assertTrue(found_file, "Expected the fallback image file to be written into assets/blog-images")


if __name__ == "__main__":
    unittest.main()
