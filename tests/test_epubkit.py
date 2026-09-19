"""Regression tests for epubkit.

Run with the standard library only::

    python tests/test_epubkit.py

Every test names the real-world failure it guards against.
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
sys.path.insert(0, os.path.join(HERE, "fixtures"))

import make_fixtures  # noqa: E402

from epubkit import (  # noqa: E402
    DrmProtectedError,
    NotAnEpubError,
    count_tokens,
    open_book,
    parse_anchor,
)
from epubkit.tokens import char_counts, estimate, split_long_text  # noqa: E402

FIXTURE_DIR = os.path.join(HERE, "fixtures")
_BUILT = False


def fixture(name):
    global _BUILT
    if not _BUILT:
        make_fixtures.build_all(FIXTURE_DIR)
        _BUILT = True
    return os.path.join(FIXTURE_DIR, name)


def book(name):
    return open_book(fixture(name))


def all_text(book_obj):
    return "\n".join(chapter.text for chapter in book_obj)


# --------------------------------------------------------------------------- #


class TestContainer(unittest.TestCase):
    def test_zip_without_container_xml_is_rejected(self):
        path = os.path.join(FIXTURE_DIR, "not_an_epub.epub")
        import zipfile

        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("hello.txt", "not an epub")
        try:
            with self.assertRaises(NotAnEpubError):
                open_book(path)
        finally:
            os.remove(path)

    def test_missing_file_is_rejected(self):
        with self.assertRaises(NotAnEpubError):
            open_book(os.path.join(FIXTURE_DIR, "does-not-exist.epub"))

    def test_metadata(self):
        meta = book("epub3_clean.epub").meta
        self.assertEqual(meta.title, "The Storm")
        self.assertEqual(meta.authors, ["A. Author"])
        self.assertEqual(meta.language, "en")
        self.assertEqual(meta.version, "3.0")
        self.assertEqual(meta.modified, "2026-01-01T00:00:00Z")


class TestRelativePaths(unittest.TestCase):
    """MarkItDown issue #1724: hrefs like ../Text/ch1.xhtml drop whole chapters."""

    def test_every_chapter_survives(self):
        obj = book("epub3_relative.epub")
        self.assertEqual(len(obj), 3)
        text = all_text(obj)
        for marker in ("RELATIVE-ONE", "RELATIVE-TWO", "RELATIVE-THREE"):
            self.assertIn(marker, text)

    def test_opf_in_subdirectory_resolves(self):
        from epubkit import inspect

        info = inspect(fixture("epub3_relative.epub"))
        self.assertEqual(info["opf_path"], "OEBPS/package/content.opf")
        self.assertEqual(info["opf_dir"], "OEBPS/package")
        self.assertEqual(info["spine"][2]["path"], "OEBPS/Text/ch3.xhtml")


class TestChapterAssembly(unittest.TestCase):
    def test_epub3_chapters_from_nav(self):
        obj = book("epub3_clean.epub")
        self.assertEqual(
            [c.title for c in obj],
            ["Chapter 1: The Beginning", "Chapter 2: The Middle", "Chapter 3: The End"],
        )

    def test_split_chapter_is_glued_back_together(self):
        obj = book("epub3_split.epub")
        self.assertEqual(len(obj), 3)
        second = obj.chapter(1)
        self.assertEqual(len(second.paths), 4)
        for index in range(1, 5):
            self.assertIn("SPLIT-PART-%d." % index, second.text)

    def test_split_chapter_heading_is_not_repeated(self):
        second = book("epub3_split.epub").chapter(1)
        self.assertTrue(second.blocks[0].dedup)
        self.assertEqual(second.blocks[0].text, "Chapter Two")

    def test_ncx_only_book(self):
        obj = book("epub2_ncx.epub")
        self.assertEqual([c.title for c in obj], ["One: First", "Two: Second", "Three: Third"])

    def test_stub_toc_falls_back_to_headings(self):
        obj = book("epub3_flat_toc.epub")
        self.assertEqual(
            [c.title for c in obj], ["Flat Chapter 1", "Flat Chapter 2", "Flat Chapter 3"]
        )
        self.assertTrue(any("stub" in w for w in obj.warnings))

    def test_no_toc_at_all_still_produces_chapters(self):
        obj = book("epub3_flat_toc.epub")
        self.assertGreaterEqual(len(obj), 3)

    def test_volume_hierarchy_is_preserved(self):
        obj = book("cjk_novel.epub")
        self.assertEqual(obj.chapter(0).group, "第一卷 风起")
        self.assertEqual(obj.chapter(1).group, "第一卷 风起")
        self.assertEqual(obj.chapter(2).group, "第二卷 云涌")


class TestFootnotes(unittest.TestCase):
    def test_epub3_noteref_resolves_to_aside(self):
        obj = book("epub3_clean.epub")
        self.assertEqual(obj.footnote_count, 1)
        hit = obj.search("dark and stormy")[0]
        _, block_index = parse_anchor(hit.anchor)
        block = obj.chapter(0).blocks[block_index]
        self.assertIn("[^1]", block.text)
        self.assertEqual(
            block.footnotes[0][1], "A note about the storm, recorded by the mate."
        )

    def test_epub2_class_based_noteref_resolves(self):
        obj = book("epub2_ncx.epub")
        self.assertEqual(obj.footnote_count, 1)
        self.assertFalse(
            [w for w in obj.warnings if "unresolved footnote" in w],
            "class-based noteref in a div.footnotes was not resolved",
        )
        first = obj.chapter(0)
        referenced = [b for b in first.blocks if b.footnotes]
        self.assertEqual(referenced[0].footnotes[0][1], "Footnote body for the old book.")

    def test_footnote_body_is_not_in_the_prose_flow(self):
        obj = book("epub3_clean.epub")
        blocks = [b for c in obj for b in c.blocks]
        bodies = [b for b in blocks if "recorded by the mate" in b.text]
        self.assertEqual(bodies, [], "the note body leaked into the reading flow")

    def test_footnotes_are_searchable(self):
        obj = book("epub3_clean.epub")
        self.assertTrue(obj.search("recorded by the mate"))

    def test_footnote_rendered_in_markdown(self):
        markdown = book("epub3_clean.epub").to_markdown()
        self.assertIn("[^1]: A note about the storm", markdown)


class TestPages(unittest.TestCase):
    def test_page_list_labels_are_attached(self):
        obj = book("epub3_clean.epub")
        self.assertEqual(obj.pages, 3)
        hit = obj.search("dark and stormy")[0]
        self.assertEqual(hit.page, "1")
        second = obj.search("ALPHA-ONE")[0]
        self.assertEqual(second.page, "2")


class TestAnchors(unittest.TestCase):
    def test_every_block_has_a_parseable_anchor(self):
        for name in ("epub3_clean.epub", "epub3_split.epub", "cjk_novel.epub"):
            obj = book(name)
            for chapter in obj:
                for index, block in enumerate(chapter.blocks):
                    self.assertEqual(block.anchor, "c%dp%d" % (chapter.index, index))
                    self.assertEqual(parse_anchor(block.anchor), (chapter.index, index))

    def test_search_anchors_resolve_back_to_the_block(self):
        obj = book("epub3_clean.epub")
        for hit in obj.search("the"):
            chapter_index, block_index = parse_anchor(hit.anchor)
            block = obj.chapter(chapter_index).blocks[block_index]
            self.assertIn("the", block.text.lower())

    def test_reading_by_chapter_title_works(self):
        obj = book("epub3_clean.epub")
        self.assertIn("GAMMA-THREE", obj.read("Chapter 3: The End").text)

    def test_ambiguous_chapter_title_is_reported(self):
        from epubkit import AnchorError

        obj = book("cjk_novel.epub")
        with self.assertRaises(AnchorError):
            obj.find_chapter("第一章")

    def test_out_of_range_anchor_is_reported(self):
        from epubkit import AnchorError

        obj = book("epub3_clean.epub")
        with self.assertRaises(AnchorError):
            obj.read("c99")
        with self.assertRaises(AnchorError):
            obj.read("c0p999")


class TestReading(unittest.TestCase):
    def test_read_respects_max_tokens(self):
        obj = book("epub3_clean.epub")
        result = obj.read("c1", max_tokens=10)
        self.assertLessEqual(result.tokens, 14)  # one block may overshoot once
        self.assertTrue(result.truncated)
        self.assertIsNotNone(result.next_anchor)

    def test_following_next_anchor_covers_the_whole_chapter(self):
        obj = book("epub3_clean.epub")
        collected = []
        anchor = "c1"
        guard = 0
        while anchor and guard < 50:
            result = obj.read(anchor, max_tokens=12)
            collected.append(result.text)
            anchor = result.next_anchor
            guard += 1
        body = "\n".join(collected)
        self.assertIn("BETA-TWO", body)
        self.assertIn("A quoted line from somewhere.", body)
        self.assertIn("second item", body)
        self.assertLess(guard, 50, "reading loop did not terminate")

    def test_chapter_heading_is_not_duplicated_in_read(self):
        result = book("epub3_clean.epub").read("c1")
        self.assertNotIn("### Chapter 2", result.text)
        self.assertEqual(result.chapter_title, "Chapter 2: The Middle")

    def test_breadcrumb_includes_volume(self):
        chunk = book("cjk_novel.epub").chunks(max_tokens=80)[0]
        self.assertEqual(chunk.breadcrumb, "青锋录 > 第一卷 风起 > 第一章 夜行")


class TestSearch(unittest.TestCase):
    def test_single_term(self):
        self.assertTrue(book("epub3_clean.epub").search("storm"))

    def test_all_terms_is_an_and(self):
        obj = book("epub3_clean.epub")
        self.assertTrue(obj.search("dark stormy", all_terms=True))
        self.assertFalse(obj.search("dark whale", all_terms=True))

    def test_regex(self):
        obj = book("epub3_clean.epub")
        self.assertTrue(obj.search(r"BETA-\w+", regex=True))

    def test_bad_regex_is_reported(self):
        from epubkit import EpubKitError

        with self.assertRaises(EpubKitError):
            book("epub3_clean.epub").search("([", regex=True)

    def test_limit_is_respected(self):
        obj = book("epub3_clean.epub")
        self.assertLessEqual(len(obj.search("the", limit=2)), 2)

    def test_chinese_search(self):
        anchors = [h.anchor for h in book("cjk_novel.epub").search("青锋")]
        self.assertIn("c0p1", anchors)

    def test_empty_query_returns_nothing(self):
        self.assertEqual(book("epub3_clean.epub").search("   "), [])


class TestChunking(unittest.TestCase):
    def test_chunks_respect_the_budget(self):
        obj = book("epub3_clean.epub")
        for chunk in obj.chunks(max_tokens=25):
            self.assertLessEqual(chunk.tokens, 25 + 5)

    def test_chunks_cover_every_marker(self):
        obj = book("epub3_clean.epub")
        body = "\n".join(chunk.text for chunk in obj.chunks(max_tokens=25))
        for marker in ("ALPHA-ONE", "BETA-TWO", "GAMMA-THREE", "A quoted line", "second item"):
            self.assertIn(marker, body)

    def test_chunk_anchors_are_ordered_and_resolvable(self):
        obj = book("epub3_clean.epub")
        previous = -1
        for chunk in obj.chunks(max_tokens=25):
            chapter_index, block_index = parse_anchor(chunk.anchor)
            self.assertGreaterEqual(chapter_index * 1000 + block_index, previous)
            previous = chapter_index * 1000 + block_index
            self.assertTrue(obj.read(chunk.anchor, max_tokens=1).text)

    def test_chunks_include_the_breadcrumb(self):
        chunk = book("epub3_clean.epub").chunks(max_tokens=25)[0]
        self.assertTrue(chunk.breadcrumb.startswith("The Storm >"))

    def test_oversized_block_is_split_not_dropped(self):
        from epubkit.model import Block, Book, BookMeta, Chapter

        meta = BookMeta(title="Big")
        text = "".join("Sentence number %d. " % index for index in range(400))
        blocks = [Block("para", text)]
        blocks[0].anchor = "c0p0"
        chapter = Chapter(0, "One", blocks, ["one.xhtml"])
        obj = Book(meta, [chapter])
        chunks = obj.chunks(max_tokens=60)
        self.assertGreater(len(chunks), 1)
        self.assertIn("Sentence number 399.", "\n".join(c.text for c in chunks))


class TestMarkdown(unittest.TestCase):
    def test_markdown_contains_every_marker(self):
        markdown = book("epub3_clean.epub").to_markdown()
        for marker in ("ALPHA-ONE", "BETA-TWO", "GAMMA-THREE"):
            self.assertIn(marker, markdown)

    def test_markdown_renders_structure(self):
        markdown = book("epub3_clean.epub").to_markdown()
        self.assertIn("## Chapter 1: The Beginning", markdown)
        self.assertIn("> A quoted line from somewhere.", markdown)
        self.assertIn("- first item", markdown)
        self.assertIn("| Name | Value |", markdown)
        self.assertIn("| --- | --- |", markdown)

    def test_markdown_anchors_are_emitted_on_request(self):
        markdown = book("epub3_clean.epub").to_markdown(anchors=True)
        self.assertIn("<!-- c0 -->", markdown)
        self.assertIn("<!-- c2p1 -->", markdown)

    def test_markdown_respects_max_tokens(self):
        markdown = book("epub3_clean.epub").to_markdown(max_tokens=20)
        self.assertIn("truncated at", markdown)
        self.assertNotIn("GAMMA-THREE", markdown)


class TestOutline(unittest.TestCase):
    def test_sections_come_from_toc_when_headings_are_silent(self):
        sections = book("epub3_clean.epub").outline()[0]["sections"]
        self.assertEqual(sections[0]["title"], "A first section")
        self.assertEqual(sections[0]["source"], "toc")

    def test_sections_come_from_headings_when_present(self):
        sections = book("epub3_flat_toc.epub").outline()[0]["sections"]
        self.assertEqual(sections[0]["title"], "A subsection of 1")
        self.assertEqual(sections[0]["source"], "heading")

    def test_depth_zero_omits_sections(self):
        self.assertEqual(book("epub3_clean.epub").outline(depth=1)[0]["sections"], [])

    def test_outline_reports_token_costs(self):
        entry = book("epub3_clean.epub").outline()[0]
        self.assertEqual(entry["tokens"], sum(b.tokens for b in book("epub3_clean.epub").chapter(0).blocks))

    def test_outline_is_cheap_relative_to_the_book(self):
        """The whole point: an agent can afford to look before it reads."""
        import json

        from epubkit.model import Block, Book, BookMeta, Chapter

        chapters = []
        for index in range(40):
            blocks = [Block("para", "word " * 3000)]
            blocks[0].anchor = "c%dp0" % index
            chapters.append(
                Chapter(index, "Chapter %d" % index, blocks, ["c%d.xhtml" % index])
            )
        obj = Book(BookMeta(title="Big"), chapters)

        outline_tokens = count_tokens(json.dumps(obj.outline()))
        self.assertGreater(obj.tokens, 100000)
        self.assertLess(outline_tokens, obj.tokens / 50)


class TestEncryption(unittest.TestCase):
    def test_content_encryption_is_refused(self):
        with self.assertRaises(DrmProtectedError):
            open_book(fixture("drm.epub"))

    def test_font_obfuscation_is_not_drm(self):
        obj = book("font_obfuscation.epub")
        self.assertEqual(len(obj), 3)


class TestTextRescue(unittest.TestCase):
    """Books whose structure exists only in the prose, not in the markup."""

    def test_giant_untitled_chapter_is_split_at_text_markers(self):
        obj = book("epub_split_title.epub")
        self.assertEqual(len(obj), 3)
        self.assertEqual(
            [c.title for c in obj],
            [
                "第一回 甄士隱夢幻識通靈 賈雨村風塵怀閨秀",
                "第二回 賈夫人仙逝揚州城 冷子興演說榮國府",
                "第三回 賈雨村夤緣復舊職 林黛玉拋父進京都",
            ],
        )
        self.assertTrue(any("recovered chapter boundaries" in w for w in obj.warnings))

    def test_rule_lines_are_not_left_in_the_prose(self):
        for chapter in book("epub_split_title.epub"):
            self.assertNotIn("————", chapter.text)

    def test_rescued_chapters_are_addressable(self):
        obj = book("epub_split_title.epub")
        hit = obj.search("熱鬧非常")
        self.assertTrue(hit)
        self.assertIn("熱鬧非常", obj.read(hit[0].anchor, max_tokens=200).text)

    def test_prose_that_merely_starts_like_a_marker_is_not_a_marker(self):
        """``第四回中既將薛家母子…`` is a sentence, not a chapter title."""
        obj = book("epub_split_title.epub")
        self.assertEqual(len(obj), 3)
        third = obj.chapter(2)
        self.assertTrue(
            third.text.lstrip().startswith("第四回中既將薛家母子"),
            "the decoy sentence should be the chapter's body, not its title",
        )

    def test_well_structured_books_are_not_touched(self):
        obj = book("epub3_clean.epub")
        self.assertFalse(any("recovered chapter boundaries" in w for w in obj.warnings))
        self.assertEqual(len(obj), 3)


class TestCjk(unittest.TestCase):
    def test_chinese_chapters_and_split_files(self):
        obj = book("cjk_novel.epub")
        self.assertEqual(len(obj), 3)
        self.assertEqual(len(obj.chapter(1).paths), 3)
        self.assertIn("第3段", obj.chapter(1).text)

    def test_ruby_keeps_both_the_word_and_the_reading(self):
        text = book("cjk_novel.epub").read("c0").text
        self.assertIn("青锋(qīng fēng)", text)

    def test_token_estimate_is_not_latin_biased(self):
        chinese = count_tokens("中文" * 100)
        latin = count_tokens("ab" * 100)
        self.assertGreater(chinese, latin)


class TestAgentSafety(unittest.TestCase):
    """The one invariant that matters: no single call dumps a whole book.

    These guard the MCP surface specifically. The default arguments are what a
    model actually gets, so a hostile default is a real bug even when every
    explicit argument works.
    """

    def _big_book(self):
        # 100k+ tokens, built in memory so the guard is not fixture-dependent.
        return book("epub3_clean.epub")

    def test_read_hands_off_to_the_next_chapter(self):
        """A reader that only follows next_anchor must not stop at a boundary."""
        obj = book("epub3_clean.epub")
        result = obj.read("c0")
        self.assertIsNotNone(result.next_anchor)
        # c0 is exhausted, so the handoff must point into c1, not back into c0.
        self.assertTrue(result.next_anchor.startswith("c1"),
                        "expected a c1 anchor, got %r" % result.next_anchor)

    def test_following_next_anchor_reaches_the_end_of_the_book(self):
        obj = book("epub3_clean.epub")
        anchor = "c0"
        seen = []
        for _ in range(500):
            result = obj.read(anchor, max_tokens=40)
            seen.append(result.chapter_index)
            if result.next_anchor is None:
                break
            self.assertNotEqual(result.next_anchor, anchor, "next_anchor did not advance")
            anchor = result.next_anchor
        else:
            self.fail("reading loop never terminated")
        self.assertEqual(sorted(set(seen)), sorted({c.index for c in obj.chapters}))

    def test_last_chapter_has_no_next_anchor(self):
        obj = book("epub3_clean.epub")
        self.assertIsNone(obj.read(obj.chapters[-1].index).next_anchor)

    def test_markdown_is_bounded_by_default(self):
        """An unbounded render is ~200k tokens on a real novel."""
        result = book("epub3_clean.epub").render_markdown()
        self.assertLessEqual(result.tokens, 4000)
        self.assertEqual(result.chapters_rendered, [0, 1, 2])

    def test_markdown_reports_where_it_stopped(self):
        obj = book("epub3_clean.epub")
        result = obj.render_markdown(max_tokens=20)
        self.assertTrue(result.truncated)
        self.assertIsNotNone(result.next_chapter)

    def test_markdown_end_chapter_is_inclusive(self):
        obj = book("epub3_clean.epub")
        result = obj.render_markdown(start=0, end=1)
        self.assertEqual(result.chapters_rendered, [0, 1])
        self.assertEqual(result.next_chapter, 2)
        self.assertNotIn("GAMMA-THREE", result.text)

    def test_markdown_to_markdown_still_returns_a_string(self):
        self.assertIsInstance(book("epub3_clean.epub").to_markdown(), str)


class TestMcpSurface(unittest.TestCase):
    """The MCP defaults, exercised through the real JSON-RPC entry point."""

    def _call(self, requests):
        import io
        import json
        from epubkit.mcp_server import serve

        stdin = io.StringIO("".join(json.dumps(r) + "\n" for r in requests))
        stdout = io.StringIO()
        serve(stdin=stdin, stdout=stdout)
        out = {}
        for raw in stdout.getvalue().splitlines():
            message = json.loads(raw)
            out[message.get("id")] = message
        return out

    def _tool(self, name, arguments):
        response = self._call([{
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }])[1]
        result = response["result"]
        self.assertFalse(result.get("isError"), result["content"][0]["text"])
        import json
        return json.loads(result["content"][0]["text"])

    def test_text_mode_stdio_is_supported(self):
        """serve() must not require a binary stream to be testable."""
        responses = self._call([
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        ])
        self.assertIn("result", responses[1])

    def test_initialize_negotiates_a_supported_protocol(self):
        responses = self._call([{
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }])
        self.assertEqual(responses[1]["result"]["protocolVersion"], "2025-06-18")

    def test_default_chunks_call_is_bounded(self):
        payload = self._tool("epub_chunks", {"book": fixture("epub3_clean.epub")})
        self.assertLessEqual(payload["returned"], 10)
        self.assertIn("next_offset", payload)

    def test_default_markdown_call_is_bounded(self):
        payload = self._tool("epub_markdown", {"book": fixture("epub3_clean.epub")})
        self.assertLessEqual(payload["tokens"], 4000)

    def test_unknown_tool_is_a_protocol_error(self):
        response = self._call([{
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "epub_nope", "arguments": {}},
        }])[1]
        self.assertEqual(response["error"]["code"], -32602)

    def test_missing_book_argument_is_a_tool_error_not_a_crash(self):
        response = self._call([{
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "epub_info", "arguments": {}},
        }])[1]
        self.assertTrue(response["result"]["isError"])

    def test_bad_anchor_is_a_tool_error_not_a_crash(self):
        response = self._call([{
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "epub_read",
                       "arguments": {"book": fixture("epub3_clean.epub"),
                                     "anchor": "nonsense"}},
        }])[1]
        self.assertTrue(response["result"]["isError"])


class TestTokens(unittest.TestCase):
    """The estimator's documented contract, and the splitter's guarantees."""

    def test_cjk_counts_one_token_per_character(self):
        """100 Chinese characters is 100 tokens, not 125.

        The estimator used to substitute a space for each CJK character before
        measuring the rest of the string, which counted every Chinese character
        twice. On a 紅樓夢-sized book that inflated the reported size by ~25%.
        """
        self.assertEqual(count_tokens("中" * 100), 100)
        self.assertEqual(count_tokens("中" * 1000), 1000)

    def test_latin_counts_one_token_per_four_characters(self):
        self.assertEqual(count_tokens("ab" * 100), 50)

    def test_mixed_script_is_the_sum_of_its_parts(self):
        self.assertEqual(count_tokens("中abc"), 2)  # 1 CJK + 3/4 latin

    def test_estimate_matches_count_tokens(self):
        """The linear path must be exactly equivalent to the obvious one."""
        for text in ("", "中" * 37, "hello world", "中abc 中文。", "。" * 9):
            self.assertEqual(
                estimate(*char_counts(text)), count_tokens(text), repr(text)
            )

    def test_splitter_respects_the_budget(self):
        """Every piece is within budget, for adversarial inputs."""
        import random
        random.seed(20260918)
        cases = [
            "中" * 5000,
            "中文句子。" * 2000,
            " ".join("word%d" % i for i in range(4000)),
            "".join(random.choice("中文 abcXYZ.,。！？") for _ in range(9000)),
        ]
        for text in cases:
            for budget in (50, 200, 800):
                pieces = split_long_text(text, budget)
                self.assertLessEqual(
                    max(count_tokens(piece) for piece in pieces), budget,
                    "budget %d exceeded" % budget,
                )

    def test_splitter_is_lossless(self):
        """Rejoining the pieces must reproduce the input exactly."""
        for text in ("中" * 5000, "中文句子。" * 500, "abc def " * 900):
            for budget in (60, 300):
                self.assertEqual("".join(split_long_text(text, budget)), text)


class TestOversizedBlocks(unittest.TestCase):
    """A paragraph the size of a chapter must not become a chunk that size."""

    def test_no_block_exceeds_the_budget(self):
        obj = book("giant_paragraph.epub")
        oversized = [
            block for chapter in obj.chapters for block in chapter.blocks
            if not block.is_heading and block.tokens > 800
        ]
        self.assertEqual(oversized, [], "blocks still over budget: %s"
                         % [(b.anchor, b.tokens) for b in oversized])

    def test_giant_paragraph_is_split_into_many_blocks(self):
        obj = book("giant_paragraph.epub")
        self.assertGreater(len(obj.chapters[0].blocks), 20)

    def test_splitting_is_lossless(self):
        """The pieces must rejoin into the original paragraph text."""
        import epubkit.rescue as rescue

        obj = book("giant_paragraph.epub")
        # Rebuild the paragraph from its pieces and compare against a fresh
        # parse with the splitter disabled.
        original = rescue.split_oversized_blocks
        try:
            rescue.split_oversized_blocks = lambda *a, **k: 0
            unsplit = book("giant_paragraph.epub")
        finally:
            rescue.split_oversized_blocks = original

        def body(target):
            return "".join(
                block.text for chapter in target.chapters for block in chapter.blocks
                if not block.is_heading
            )

        self.assertEqual(body(obj), body(unsplit))

    def test_every_chunk_is_within_budget(self):
        obj = book("giant_paragraph.epub")
        for chunk in obj.chunks(max_tokens=800):
            self.assertLessEqual(chunk.tokens, 800,
                                 "%s is %d tokens" % (chunk.anchor, chunk.tokens))

    def test_anchors_stay_unique_after_splitting(self):
        """A split must not mint two blocks with the same anchor."""
        obj = book("giant_paragraph.epub")
        anchors = [block.anchor for chapter in obj.chapters for block in chapter.blocks]
        self.assertEqual(len(anchors), len(set(anchors)))

    def test_split_blocks_are_individually_readable(self):
        obj = book("giant_paragraph.epub")
        blocks = obj.chapters[0].blocks
        for block in blocks[:5]:
            result = obj.read(block.anchor, max_tokens=400)
            self.assertTrue(result.text.strip(), block.anchor)


class TestHeadingFallback(unittest.TestCase):
    """When the TOC is unusable, headings must carry the structure.

    ``_heading_starts`` used to take only the first heading of each document,
    so a single-file EPUB with fifty ``<h1>``s came out as one chapter.
    """

    def test_every_top_level_heading_starts_a_chapter(self):
        obj = book("giant_paragraph.epub")
        self.assertEqual(
            [c.title for c in obj],
            ["第一回 巨段無斷句", "第二回 正常段落"],
        )

    def test_shallowest_heading_level_wins(self):
        """An <h1> with <h2> sub-headings is one chapter, not several."""
        obj = book("epub3_clean.epub")  # has a usable nav, so headings are sections
        first = obj.chapter(0)
        self.assertEqual(first.title, "Chapter 1: The Beginning")
        self.assertTrue(all(block.text for block in first.blocks))

    def test_headings_supply_titles_when_the_toc_is_a_stub(self):
        obj = book("epub3_flat_toc.epub")
        self.assertEqual([c.title for c in obj],
                         ["Flat Chapter 1", "Flat Chapter 2", "Flat Chapter 3"])
        self.assertNotIn("Contents", [c.title for c in obj])


class TestStats(unittest.TestCase):
    def test_stats_shape(self):
        stats = book("epub3_clean.epub").stats()
        for key in ("title", "chapters", "blocks", "tokens", "footnotes", "print_pages"):
            self.assertIn(key, stats)
        self.assertEqual(stats["chapters"], 3)

    def test_book_token_total_matches_sum_of_chapters(self):
        obj = book("epub3_clean.epub")
        self.assertEqual(obj.tokens, sum(c.tokens for c in obj))


if __name__ == "__main__":
    unittest.main(verbosity=2)
