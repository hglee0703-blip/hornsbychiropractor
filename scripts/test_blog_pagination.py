"""Check blog navigation and automatic overflow using isolated published-post fixtures."""

from datetime import date, timedelta
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_blog as blog


class ListingParser(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.cards = []
        self.canonicals = []
        self.meta = {}
        self.nav_links = []
        self.nav_labels = []
        self.current_pages = []
        self.title = ""
        self.schema_text = ""
        self._nav_depth = 0
        self._title = False
        self._schema = False
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "blog-card" in attrs.get("class", "").split():
            self.cards.append(attrs.get("href"))
        if tag == "link" and attrs.get("rel") == "canonical":
            self.canonicals.append(attrs.get("href"))
        if tag == "meta":
            self.meta[attrs.get("property", attrs.get("name", ""))] = attrs.get("content")
        if tag == "nav":
            self._nav_depth += 1
            self.nav_labels.append(attrs.get("aria-label", ""))
        if self._nav_depth and tag == "a":
            self.nav_links.append(attrs.get("href"))
        if self._nav_depth and attrs.get("aria-current") == "page":
            self.current_pages.append(attrs)
        if tag == "title":
            self._title = True
        if tag == "script" and attrs.get("id") == "blog-schema":
            self._schema = True

    def handle_endtag(self, tag):
        if tag == "nav":
            self._nav_depth -= 1
        if tag == "title":
            self._title = False
        if tag == "script":
            self._schema = False

    def handle_data(self, data):
        if self._title:
            self.title += data
        if self._schema:
            self.schema_text += data


class BlogPaginationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.blog_dir = self.root / "blog"
        self.blog_dir.mkdir()
        for key, value in (("REPO_ROOT", self.root), ("BLOG_DIR", self.blog_dir)):
            patcher = patch.object(blog, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        clock = patch.object(blog, "sydney_today", return_value="2026-10-25")
        clock.start()
        self.addCleanup(clock.stop)

    def write_post(self, slug, published="2026-01-01"):
        folder = self.blog_dir / slug
        folder.mkdir(exist_ok=True)
        (folder / "index.html").write_text(
            '<!doctype html><html><head>'
            f'<link rel="canonical" href="{blog.SITE_DOMAIN}/blog/{slug}/">'
            f'<meta name="description" content="Meta description for {slug}.">'
            '</head><body><article>'
            f'<h1>Article {slug}</h1><p class="post-meta">Andy Lee · Lower back pain</p>'
            f'<time datetime="{published}">{published}</time>'
            '</article></body></html>', encoding="utf-8",
        )

    def card(self, slug):
        return (
            f'<a class="blog-card" href="/blog/{slug}/">\n'
            '          <p class="eyebrow">Lower back pain</p>\n'
            f'          <h2>Curated heading for {slug}</h2>\n'
            f'          <p>Carefully edited summary for {slug} &amp; everyday movement.</p>\n'
            '        </a>'
        )

    def seed(self, count, omitted_cards=()):
        slugs = [f"post-{i:03}" for i in range(count)]
        for i, slug in enumerate(slugs):
            self.write_post(slug, (date(2026, 8, 1) - timedelta(days=i)).isoformat())
        cards = "\n        ".join(self.card(slug) for slug in slugs if slug not in omitted_cards)
        (self.blog_dir / "index.html").write_text(
            '<!doctype html>\n<html lang="en-AU">\n  <head>\n'
            '    <title>Back and Neck Pain Guides | Hornsby Chiropractor</title>\n'
            '    <meta name="description" content="Practical clinic guides.">\n'
            f'    <link rel="canonical" href="{blog.SITE_DOMAIN}/blog/">\n'
            '    <meta property="og:title" content="Back and Neck Pain Guides | Hornsby Chiropractor">\n'
            f'    <meta property="og:url" content="{blog.SITE_DOMAIN}/blog/">\n'
            '    <meta property="og:description" content="Practical clinic guides.">\n'
            '    <meta name="twitter:title" content="Back and Neck Pain Guides | Hornsby Chiropractor">\n'
            '    <meta name="twitter:description" content="Practical clinic guides.">\n'
            '  </head>\n  <body>\n    <header>Clinic header stays here.</header>\n'
            '    <main>\n      <section class="page-intro"><h1>Back pain, neck pain and everyday movement</h1></section>\n'
            f'      <section class="blog-list" aria-label="Blog posts">\n        {cards}\n      </section>\n'
            '    </main>\n    <footer>Clinic footer stays here.</footer>\n  </body>\n</html>\n',
            encoding="utf-8",
        )
        entries = (
            f'  <url><loc>{blog.SITE_DOMAIN}/services/</loc><lastmod>2025-01-02</lastmod>'
            '<changefreq>monthly</changefreq><priority>0.8</priority></url>\n'
            f'  <url><loc>{blog.SITE_DOMAIN}/blog/</loc><lastmod>2026-08-01</lastmod></url>\n'
        )
        entries += "".join(
            f'  <url><loc>{blog.SITE_DOMAIN}/blog/{slug}/</loc><lastmod>2026-08-01</lastmod></url>\n'
            for slug in slugs
        )
        (self.root / "sitemap.xml").write_text(
            blog.SITEMAP_HEADER + entries + blog.SITEMAP_FOOTER, encoding="utf-8",
        )
        return slugs

    def listing_paths(self):
        return [self.blog_dir / "index.html"] + sorted(
            (self.blog_dir / "page").glob("*/index.html"), key=lambda path: int(path.parent.name),
        )

    def listings(self):
        return [ListingParser(path.read_text(encoding="utf-8")) for path in self.listing_paths()]

    def snapshot(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes() for path in self.listing_paths()} | {
            "sitemap.xml": (self.root / "sitemap.xml").read_bytes(),
        }

    def test_boundaries_keep_every_article_once(self):
        for count, sizes in ((0, [0]), (12, [12]), (13, [12, 1]), (24, [12, 12]),
                             (25, [12, 12, 1]), (46, [12, 12, 12, 10])):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as folder:
                original_root, original_blog = self.root, self.blog_dir
                self.root, self.blog_dir = Path(folder), Path(folder) / "blog"
                self.blog_dir.mkdir()
                try:
                    with patch.object(blog, "REPO_ROOT", self.root), patch.object(blog, "BLOG_DIR", self.blog_dir):
                        slugs = self.seed(count)
                        self.assertEqual(blog.rebuild_blog_listing(), self.blog_dir / "index.html")
                        pages = self.listings()
                        self.assertEqual([len(page.cards) for page in pages], sizes)
                        self.assertEqual([href for page in pages for href in page.cards],
                                         [f"/blog/{slug}/" for slug in slugs])
                        self.assertFalse((self.blog_dir / "page" / "1" / "index.html").exists())
                finally:
                    self.root, self.blog_dir = original_root, original_blog

    def test_new_post_overflows_without_losing_edited_cards(self):
        slugs = self.seed(48)
        blog.rebuild_blog_listing()
        self.write_post("brand-new", "2026-10-25")
        blog.prepend_blog_card({
            "slug": "brand-new", "title": "New guide & practical help", "category": "Neck & posture",
            "intro_summary": "A new guide to everyday movement.",
        })
        pages = self.listings()
        self.assertEqual([len(page.cards) for page in pages], [12, 12, 12, 12, 1])
        self.assertEqual([href for page in pages for href in page.cards],
                         ["/blog/brand-new/"] + [f"/blog/{slug}/" for slug in slugs])
        all_html = "\n".join(path.read_text(encoding="utf-8") for path in self.listing_paths())
        for slug in slugs:
            self.assertIn(self.card(slug), all_html)
        self.assertIn("New guide &amp; practical help", all_html)
        first_snapshot = self.snapshot()
        blog.rebuild_blog_listing()
        self.assertEqual(self.snapshot(), first_snapshot, "Rebuilding must be idempotent")

    def test_missing_catalog_cards_are_added_in_date_order(self):
        slugs = self.seed(13, omitted_cards=("post-000", "post-012"))
        blog.rebuild_blog_listing()
        self.assertEqual([href for page in self.listings() for href in page.cards],
                         [f"/blog/{slug}/" for slug in slugs])

    def test_legacy_update_date_does_not_reorder_existing_articles(self):
        slugs = self.seed(13)
        legacy_path = self.blog_dir / slugs[-1] / "index.html"
        legacy = legacy_path.read_text(encoding="utf-8")
        legacy = legacy.replace(
            '<time datetime="2026-07-20">2026-07-20</time>',
            'Updated <time datetime="2026-10-24">24 October 2026</time>',
        )
        legacy_path.write_text(legacy, encoding="utf-8")
        blog.rebuild_blog_listing()
        self.assertEqual([href for page in self.listings() for href in page.cards],
                         [f"/blog/{slug}/" for slug in slugs])
        # A newly published article must still lead even if an old article has
        # a later maintenance date. Existing chronological/editorial order wins.
        self.write_post("new-publication", "2026-10-23")
        blog.prepend_blog_card({
            "slug": "new-publication", "title": "A new publication", "category": "Neck & posture",
            "intro_summary": "New advice for everyday movement.",
        })
        expected = ["/blog/new-publication/"] + [f"/blog/{slug}/" for slug in slugs]
        self.assertEqual([href for page in self.listings() for href in page.cards], expected)
        blog.rebuild_blog_listing()
        self.assertEqual([href for page in self.listings() for href in page.cards], expected)

    def test_page_metadata_and_schema_match_visible_articles(self):
        self.seed(25)
        blog.rebuild_blog_listing()
        for number, page in enumerate(self.listings(), 1):
            with self.subTest(page=number):
                url = blog.SITE_DOMAIN + ("/blog/" if number == 1 else f"/blog/page/{number}/")
                self.assertEqual(page.canonicals, [url])
                self.assertEqual(page.meta["og:url"], url)
                schema = json.loads(page.schema_text)
                self.assertEqual(schema["url"], url)
                self.assertEqual([post["url"] for post in schema["blogPost"]],
                                 [blog.SITE_DOMAIN + href for href in page.cards])
                self.assertEqual(page.meta["og:title"], page.title)
                self.assertEqual(page.meta["twitter:title"], page.title)
                if number > 1:
                    self.assertIn(str(number), page.title)

    def test_navigation_reaches_all_pages_and_has_valid_endpoints(self):
        self.seed(46)
        blog.rebuild_blog_listing()
        pages = self.listings()
        valid = {"/blog/", "/blog/page/2/", "/blog/page/3/", "/blog/page/4/"}
        for index, page in enumerate(pages):
            self.assertTrue(page.nav_labels and all(page.nav_labels), "Navigation needs an accessible label")
            self.assertEqual(len(page.current_pages), 1)
            self.assertTrue(set(page.nav_links) <= valid)
            if index < 3:
                self.assertIn(f"/blog/page/{index + 2}/", page.nav_links)
            if index > 0:
                self.assertIn("/blog/" if index == 1 else f"/blog/page/{index}/", page.nav_links)
        self.assertNotIn("page", blog.extract_existing_topics())
        self.assertEqual(len(blog.extract_post_catalog()), 46)

    def test_sitemap_preserves_unrelated_entries_and_prunes_stale_pages(self):
        self.seed(25)
        blog.rebuild_blog_listing()
        xml = ET.parse(self.root / "sitemap.xml")
        namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        entries = {item.find("s:loc", namespace).text: item for item in xml.getroot()}
        service = entries[blog.SITE_DOMAIN + "/services/"]
        self.assertEqual(service.find("s:lastmod", namespace).text, "2025-01-02")
        self.assertEqual(service.find("s:changefreq", namespace).text, "monthly")
        self.assertEqual(service.find("s:priority", namespace).text, "0.8")
        for number in (2, 3):
            self.assertIn(blog.SITE_DOMAIN + f"/blog/page/{number}/", entries)
        (self.blog_dir / "post-024" / "index.html").unlink()
        blog.rebuild_blog_listing()
        self.assertEqual([len(page.cards) for page in self.listings()], [12, 12])
        self.assertFalse((self.blog_dir / "page" / "3" / "index.html").exists())
        xml_text = (self.root / "sitemap.xml").read_text(encoding="utf-8")
        self.assertNotIn(blog.SITE_DOMAIN + "/blog/page/3/", xml_text)
        self.assertIn(blog.SITE_DOMAIN + "/blog/page/2/", xml_text)

    def test_dry_run_restore_restores_shifted_pages_and_removes_new_page(self):
        self.seed(24)
        blog.rebuild_blog_listing()
        before = self.snapshot()
        blog._snapshot_before_dry_run()
        self.write_post("dry-run-test-post", "2026-10-25")
        blog.prepend_blog_card({
            "slug": "dry-run-test-post", "title": "Dry run", "category": "Neck & posture",
            "intro_summary": "Temporary article.",
        })
        blog.update_sitemap("dry-run-test-post")
        self.assertEqual(len(self.listing_paths()), 3)
        (self.blog_dir / "dry-run-test-post" / "index.html").unlink()
        blog._restore_listing(self.blog_dir / "index.html")
        blog._restore_sitemap(self.root / "sitemap.xml")
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
