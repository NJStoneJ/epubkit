"""The addressable book model.

The whole point of epubkit is here: a book is not a blob of markdown, it is a
tree of chapters and blocks, every one of which has a stable anchor. An agent
can therefore outline, search, quote and re-read without ever holding the whole
book in its context window.

Anchor grammar::

    c3        chapter 3
    c3p12     block 12 of chapter 3
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .errors import AnchorError, EpubKitError
from .tokens import count_tokens, split_long_text

ANCHOR_RE = re.compile(r"^c(\d+)(?:p(\d+))?$", re.IGNORECASE)


def chapter_anchor(index):
    return "c%d" % index


def block_anchor(chapter_index, block_index):
    return "c%dp%d" % (chapter_index, block_index)


def parse_anchor(anchor):
    """``"c3p12"`` -> ``(3, 12)``; ``"c3"`` -> ``(3, None)``."""
    match = ANCHOR_RE.match((anchor or "").strip())
    if not match:
        raise AnchorError("bad anchor %r -- expected 'c3' or 'c3p12'" % (anchor,))
    chapter = int(match.group(1))
    block = int(match.group(2)) if match.group(2) is not None else None
    return chapter, block


# --------------------------------------------------------------------------- #
# Blocks and chapters
# --------------------------------------------------------------------------- #


class Block:
    """One addressable unit of content inside a chapter."""

    __slots__ = (
        "kind",
        "text",
        "level",
        "page",
        "anchor",
        "footnotes",
        "src_path",
        "src_id",
        "tokens",
        "dedup",
        "raw",
    )

    def __init__(self, kind, text="", level=0, page=None, src_path="", src_id=""):
        self.kind = kind
        self.text = text
        self.level = level
        self.page = page
        self.src_path = src_path
        self.src_id = src_id
        self.anchor = ""
        self.footnotes = []
        self.dedup = False
        # Kept only when the source had hard line breaks that ``text`` collapsed
        # away. Some txt-derived EPUBs bury whole chapters inside one <p>, and
        # the line breaks are the only surviving structure.
        self.raw = ""
        self.tokens = count_tokens(text)

    @property
    def is_heading(self):
        return self.kind == "heading"

    def __repr__(self):
        return "Block(%s, %r, %s)" % (self.kind, self.text[:32], self.anchor)


class Chapter:
    """A chapter: an ordered list of blocks plus where it came from."""

    __slots__ = (
        "index",
        "title",
        "blocks",
        "paths",
        "depth",
        "anchor",
        "tokens",
        "synthetic",
        "title_from_toc",
        "group",
        "toc_sections",
    )

    def __init__(self, index, title, blocks, paths, depth=0, synthetic=False,
                 title_from_toc=False, group=""):
        self.index = index
        self.title = title
        self.blocks = blocks
        self.paths = list(paths)
        self.depth = depth
        self.synthetic = synthetic
        self.title_from_toc = title_from_toc
        self.group = group
        self.toc_sections = []
        self.anchor = chapter_anchor(index)
        self.tokens = sum(block.tokens for block in blocks)

    @property
    def text(self):
        """The chapter body, excluding a heading that merely repeats the title."""
        return "\n\n".join(
            block.text for block in self.blocks if block.text and not block.dedup
        )

    @property
    def headings(self):
        return [block for block in self.blocks if block.is_heading and block.text]

    @property
    def page_start(self):
        for block in self.blocks:
            if block.page is not None:
                return block.page
        return None

    @property
    def page_end(self):
        for block in reversed(self.blocks):
            if block.page is not None:
                return block.page
        return None

    def __len__(self):
        return len(self.blocks)

    def __repr__(self):
        return "Chapter(%d, %r, %d blocks)" % (self.index, self.title, len(self.blocks))


# --------------------------------------------------------------------------- #
# Plain data carriers
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class BookMeta:
    title: str = ""
    authors: list = field(default_factory=list)
    language: str = ""
    identifier: str = ""
    publisher: str = ""
    date: str = ""
    description: str = ""
    subjects: list = field(default_factory=list)
    rights: str = ""
    series: str = ""
    series_index: str = ""
    modified: str = ""
    version: str = ""

    def to_dict(self):
        return {
            "title": self.title,
            "authors": list(self.authors),
            "language": self.language,
            "identifier": self.identifier,
            "publisher": self.publisher,
            "date": self.date,
            "description": self.description,
            "subjects": list(self.subjects),
            "rights": self.rights,
            "series": self.series,
            "series_index": self.series_index,
            "modified": self.modified,
            "version": self.version,
        }


@dataclass(slots=True)
class TocEntry:
    title: str
    path: str
    fragment: str = ""
    depth: int = 0
    order: int = 0
    ancestors: list = field(default_factory=list)
    parent_location: tuple = ("", "")

    @property
    def href(self):
        return self.path + ("#" + self.fragment if self.fragment else "")

    @property
    def location(self):
        return (self.path, self.fragment)


@dataclass(slots=True)
class Hit:
    anchor: str
    chapter_index: int
    chapter_title: str
    text: str
    kind: str = "para"
    page: object = None
    position: int = 0
    truncated: bool = False

    def to_dict(self):
        return {
            "anchor": self.anchor,
            "chapter": self.chapter_title,
            "chapter_index": self.chapter_index,
            "page": self.page,
            "kind": self.kind,
            "truncated": self.truncated,
            "text": self.text,
        }


@dataclass(slots=True)
class Chunk:
    anchor: str
    anchor_end: str
    chapter_index: int
    chapter_title: str
    section: str
    breadcrumb: str
    text: str
    tokens: int
    page_start: object = None
    page_end: object = None

    def to_dict(self):
        return {
            "anchor": self.anchor,
            "anchor_end": self.anchor_end,
            "chapter_index": self.chapter_index,
            "chapter": self.chapter_title,
            "section": self.section,
            "breadcrumb": self.breadcrumb,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "tokens": self.tokens,
            "text": self.text,
        }


@dataclass(slots=True)
class ReadResult:
    anchor: str
    anchor_end: str
    next_anchor: object
    chapter_index: int
    chapter_title: str
    breadcrumb: str
    text: str
    tokens: int
    truncated: bool
    blocks_read: int

    def to_dict(self):
        return {
            "anchor": self.anchor,
            "anchor_end": self.anchor_end,
            "next_anchor": self.next_anchor,
            "chapter": self.chapter_title,
            "chapter_index": self.chapter_index,
            "breadcrumb": self.breadcrumb,
            "tokens": self.tokens,
            "truncated": self.truncated,
            "blocks_read": self.blocks_read,
            "text": self.text,
        }


@dataclass(slots=True)
class MarkdownResult:
    text: str
    truncated: bool
    next_chapter: object
    chapters_rendered: list
    characters: int

    @property
    def tokens(self):
        return count_tokens(self.text)

    def to_dict(self):
        return {
            "characters": len(self.text),
            "tokens": self.tokens,
            "truncated": self.truncated,
            "next_chapter": self.next_chapter,
            "chapters_rendered": list(self.chapters_rendered),
            "text": self.text,
        }


# --------------------------------------------------------------------------- #
# Book
# --------------------------------------------------------------------------- #


class Book:
    """A parsed EPUB with stable, addressable structure."""

    def __init__(self, meta, chapters, toc=None, source="", warnings=None):
        self.meta = meta
        self.chapters = list(chapters)
        self.toc = list(toc or [])
        self.source = source
        self.warnings = list(warnings or [])

    # -- basics ------------------------------------------------------------ #

    def __len__(self):
        return len(self.chapters)

    def __iter__(self):
        return iter(self.chapters)

    def __getitem__(self, index):
        return self.chapter(index)

    def __repr__(self):
        return "Book(%r, %d chapters)" % (self.meta.title, len(self.chapters))

    @property
    def tokens(self):
        return sum(chapter.tokens for chapter in self.chapters)

    @property
    def footnote_count(self):
        return sum(len(block.footnotes) for chapter in self.chapters
                   for block in chapter.blocks)

    @property
    def pages(self):
        seen = set()
        for chapter in self.chapters:
            for block in chapter.blocks:
                if block.page is not None:
                    seen.add(block.page)
        return len(seen)

    def stats(self):
        return {
            "title": self.meta.title,
            "authors": list(self.meta.authors),
            "language": self.meta.language,
            "chapters": len(self.chapters),
            "blocks": sum(len(chapter.blocks) for chapter in self.chapters),
            "characters": sum(len(chapter.text) for chapter in self.chapters),
            "tokens": self.tokens,
            "footnotes": self.footnote_count,
            "print_pages": self.pages,
            "warnings": list(self.warnings),
        }

    # -- lookup ------------------------------------------------------------ #

    def chapter(self, index):
        if not isinstance(index, int):
            raise AnchorError("chapter index must be an int, got %r" % (index,))
        if index < 0:
            index += len(self.chapters)
        if not 0 <= index < len(self.chapters):
            raise AnchorError(
                "chapter %d out of range -- this book has %d chapters"
                % (index, len(self.chapters))
            )
        return self.chapters[index]

    def find_chapter(self, query):
        """Locate a chapter by title (exact first, then unique substring)."""
        if isinstance(query, int):
            return self.chapter(query)
        needle = str(query or "").strip().lower()
        if not needle:
            raise AnchorError("empty chapter query")

        for chapter in self.chapters:
            if chapter.title.lower() == needle:
                return chapter

        partial = [c for c in self.chapters if needle in c.title.lower()]
        if len(partial) == 1:
            return partial[0]
        if partial:
            titles = ", ".join(repr(c.title) for c in partial[:8])
            raise AnchorError(
                "%r matches %d chapters -- be more specific: %s"
                % (query, len(partial), titles)
            )
        raise AnchorError("no chapter title contains %r" % (query,))

    def resolve(self, target):
        """Accept an anchor (``c3p12``) or a chapter title; return both parts."""
        if isinstance(target, int):
            return target, None
        text = str(target or "").strip()
        if ANCHOR_RE.match(text):
            return parse_anchor(text)
        return self.find_chapter(text).index, None

    # -- outline ----------------------------------------------------------- #

    def outline(self, depth=2):
        """A cheap map of the book: chapters, sections, token costs.

        This is the tool an agent should call first. It is deliberately small --
        for a 700k-character book it is a few hundred tokens, which is what
        makes "read the book" affordable instead of impossible.
        """
        nodes = []
        for chapter in self.chapters:
            entry = {
                "anchor": chapter.anchor,
                "title": chapter.title,
                "tokens": chapter.tokens,
                "blocks": len(chapter.blocks),
                "page": chapter.page_start,
                "depth": chapter.depth,
                "group": chapter.group,
                "sections": [],
            }
            if depth and depth > 1:
                entry["sections"] = self._sections(chapter, depth)
            nodes.append(entry)
        return nodes

    @staticmethod
    def _sections(chapter, depth):
        """Sub-sections, merged from in-text headings and the TOC.

        Both sources matter. Headings are authoritative when present, but
        Calibre-produced books routinely declare sub-sections only in the
        navigation document, and ignoring those throws away the book's own
        idea of its structure.
        """
        blocks = chapter.blocks
        found = {}
        order = []

        for index, block in enumerate(blocks):
            if not (block.is_heading and block.text) or block.dedup:
                continue
            if block.level > depth:
                continue
            total = 0
            for follow in blocks[index + 1 :]:
                if follow.is_heading and follow.level <= block.level:
                    break
                total += follow.tokens
            found[block.anchor] = {
                "anchor": block.anchor,
                "title": block.text,
                "level": block.level,
                "tokens": total,
                "source": "heading",
            }
            order.append((index, block.anchor))

        for index, title, anchor in chapter.toc_sections:
            if anchor in found:
                continue
            total = 0
            for offset, follow in enumerate(blocks[index:]):
                if offset and follow.is_heading and not follow.dedup and follow.text:
                    break
                total += follow.tokens
            found[anchor] = {
                "anchor": anchor,
                "title": title,
                "level": 0,
                "tokens": total,
                "source": "toc",
            }
            order.append((index, anchor))

        order.sort()
        return [found[anchor] for _, anchor in order]

    # -- reading ----------------------------------------------------------- #

    def read(self, target, max_tokens=None, include_footnotes=True):
        """Read from an anchor or chapter title, optionally capped by tokens."""
        chapter_index, block_index = self.resolve(target)
        chapter = self.chapter(chapter_index)
        start = 0 if block_index is None else block_index
        if block_index is not None and block_index >= len(chapter.blocks):
            raise AnchorError(
                "block %d out of range -- %s has %d blocks"
                % (block_index, chapter.anchor, len(chapter.blocks))
            )

        parts = []
        used = 0
        truncated = False
        index = start
        first_index = None
        last_index = None

        while index < len(chapter.blocks):
            block = chapter.blocks[index]
            if block.dedup:
                # The chapter heading is already reported as ``chapter_title``.
                index += 1
                continue

            text = self._render_block(block, include_footnotes)
            cost = count_tokens(text)

            if max_tokens is not None:
                if used and used + cost > max_tokens:
                    truncated = True
                    break
                if not used and cost > max_tokens:
                    pieces = split_long_text(text, max_tokens)
                    if pieces:
                        parts.append(pieces[0])
                        used += count_tokens(pieces[0])
                    truncated = True
                    if first_index is None:
                        first_index = index
                    last_index = index
                    index += 1
                    break

            if text.strip():
                parts.append(text)
                used += cost
                if first_index is None:
                    first_index = index
            last_index = index
            index += 1

        body = "\n\n".join(parts)
        anchor_index = start if first_index is None else first_index
        end_index = start if last_index is None else last_index

        # ``next_anchor`` has to survive the end of a chapter, or a reader that
        # just follows it stops dead at every chapter boundary. When this
        # chapter is exhausted, hand off to the first block of the next chapter
        # that actually has content.
        if index < len(chapter.blocks):
            next_anchor = block_anchor(chapter_index, index)
        else:
            next_anchor = self._first_anchor_after(chapter_index)

        return ReadResult(
            anchor=block_anchor(chapter_index, anchor_index),
            anchor_end=block_anchor(chapter_index, end_index),
            next_anchor=next_anchor,
            chapter_index=chapter_index,
            chapter_title=chapter.title,
            breadcrumb=self._breadcrumb(chapter),
            text=body,
            tokens=count_tokens(body),
            truncated=truncated,
            blocks_read=sum(1 for i in range(start, end_index + 1)
                            if not chapter.blocks[i].dedup),
        )

    def _first_anchor_after(self, chapter_index):
        """Anchor of the first readable block of the next non-empty chapter."""
        for position, later in enumerate(self.chapters):
            if position <= chapter_index:
                continue
            for block_index, block in enumerate(later.blocks):
                if block.dedup or not block.text.strip():
                    continue
                return block_anchor(position, block_index)
        return None

    # -- search ------------------------------------------------------------ #

    def search(self, query, limit=20, all_terms=False, regex=False,
               ignore_case=True, max_tokens=400, chapter=None,
               include_footnotes=True):
        """Find blocks matching ``query`` and return anchored snippets.

        ``all_terms=True`` splits the query on whitespace and requires every
        term to appear in the same block. ``regex=True`` treats the query as a
        regular expression instead. Footnote bodies are searched too, because
        a term that only appears in a note is still a term the reader is
        looking for.
        """
        if not query or not str(query).strip():
            return []

        pattern = None
        terms = []
        if regex:
            flags = re.IGNORECASE if ignore_case else 0
            try:
                pattern = re.compile(query, flags)
            except re.error as exc:
                raise EpubKitError("invalid regular expression: %s" % exc) from exc
        else:
            terms = str(query).split() if all_terms else [str(query)]
            if ignore_case:
                terms = [term.lower() for term in terms]
            if not terms:
                return []

        hits = []
        for book_chapter in self.chapters:
            if chapter is not None and book_chapter.index != chapter:
                continue
            for block in book_chapter.blocks:
                haystack = block.text
                if include_footnotes and block.footnotes:
                    haystack += "".join(
                        "\n[^%d] %s" % (number, text)
                        for number, text in block.footnotes
                    )
                if not haystack:
                    continue
                position = _match_position(haystack, terms, pattern, ignore_case)
                if position is None:
                    continue
                snippet = _snippet(haystack, position, max_tokens)
                hits.append(
                    Hit(
                        anchor=block.anchor,
                        chapter_index=book_chapter.index,
                        chapter_title=book_chapter.title,
                        text=snippet,
                        kind=block.kind,
                        page=block.page,
                        position=position,
                        truncated=len(snippet) < len(haystack),
                    )
                )
                if len(hits) >= limit:
                    return hits
        return hits

    # -- chunking ---------------------------------------------------------- #

    def chunks(self, max_tokens=800, overlap_blocks=0):
        """Pack the book into token-bounded chunks that keep their anchors.

        Blocks are joined with a blank line, which costs tokens too, so the
        running total carries a separator charge. Without it a chunk of many
        small blocks reports more tokens than the budget it was measured
        against -- measured at up to 8 tokens over on real books.
        """
        separator_cost = count_tokens("\n\n")
        out = []
        for chapter in self.chapters:
            buffer = []
            buffer_tokens = 0

            for block in chapter.blocks:
                if block.dedup:
                    continue
                text = self._render_block(block, True)
                if not text.strip():
                    continue
                cost = count_tokens(text) + separator_cost

                if cost > max_tokens:
                    if buffer:
                        out.append(self._make_chunk(chapter, buffer))
                        buffer, buffer_tokens = [], 0
                    # Only reachable for a block that is itself over budget.
                    # ``split_oversized_blocks`` removes those at parse time,
                    # so this is a backstop for programmatically built books.
                    for piece in split_long_text(text, max_tokens):
                        pseudo = Block(
                            block.kind,
                            piece,
                            level=block.level,
                            page=block.page,
                            src_path=block.src_path,
                        )
                        pseudo.anchor = block.anchor
                        out.append(self._make_chunk(chapter, [pseudo]))
                    continue

                if buffer and buffer_tokens + cost > max_tokens:
                    done = buffer
                    out.append(self._make_chunk(chapter, done))
                    buffer = done[-overlap_blocks:] if overlap_blocks else []
                    buffer_tokens = sum(
                        count_tokens(self._render_block(item, True)) + separator_cost
                        for item in buffer
                    )

                buffer.append(block)
                buffer_tokens += cost

            if buffer:
                out.append(self._make_chunk(chapter, buffer))
        return out

    def _make_chunk(self, chapter, blocks):
        section = chapter.title
        for block in blocks:
            if block.is_heading and block.text and not block.dedup:
                section = block.text
                break
        parts = []
        for block in blocks:
            text = self._render_block(block, True)
            if text.strip():
                parts.append(text)
        body = "\n\n".join(parts)
        pages = [block.page for block in blocks if block.page is not None]
        return Chunk(
            anchor=blocks[0].anchor,
            anchor_end=blocks[-1].anchor,
            chapter_index=chapter.index,
            chapter_title=chapter.title,
            section=section,
            breadcrumb=self._breadcrumb(chapter, section),
            text=body,
            tokens=count_tokens(body),
            page_start=pages[0] if pages else None,
            page_end=pages[-1] if pages else None,
        )

    # -- rendering --------------------------------------------------------- #

    def to_markdown(self, anchors=False, include_footnotes=True, start=None,
                    max_tokens=None, end=None):
        """Render the book as Markdown.

        ``anchors=True`` interleaves ``<!-- c3p12 -->`` comments so downstream
        consumers can still cite precisely. Off by default because it roughly
        doubles the token cost of the document.
        """
        return self.render_markdown(
            anchors=anchors,
            include_footnotes=include_footnotes,
            start=start,
            end=end,
            max_tokens=max_tokens,
        ).text

    def render_markdown(self, anchors=False, include_footnotes=True, start=None,
                        end=None, max_tokens=None):
        """Render Markdown and report where it stopped.

        ``to_markdown()`` throws that bookkeeping away, which is fine for a
        human but wrong for an agent: a caller that gets a truncated document
        needs to know which chapter to ask for next. ``start``/``end`` are
        inclusive chapter indexes.
        """
        lines = []
        if self.meta.title:
            lines.append("# %s" % self.meta.title)
            if self.meta.authors:
                lines.append("")
                lines.append("*%s*" % ", ".join(self.meta.authors))

        used = count_tokens("\n".join(lines))
        rendered = []
        next_chapter = None
        truncated = False

        for chapter in self.chapters:
            if start is not None and chapter.index < start:
                continue
            if end is not None and chapter.index > end:
                next_chapter = chapter.index
                break
            lines.append("")
            if anchors:
                lines.append("<!-- %s -->" % chapter.anchor)
            lines.append("")
            lines.append("## %s" % (chapter.title or "Untitled"))
            rendered.append(chapter.index)

            for block in chapter.blocks:
                if block.dedup:
                    continue
                text = self._render_block(block, include_footnotes)
                if not text.strip():
                    continue
                cost = count_tokens(text)
                if max_tokens is not None and used + cost > max_tokens:
                    lines.append("")
                    lines.append("<!-- truncated at %s -->" % block.anchor)
                    text = "\n".join(lines)
                    return MarkdownResult(
                        text=text,
                        truncated=True,
                        next_chapter=chapter.index,
                        chapters_rendered=rendered,
                        characters=len(text),
                    )
                if anchors:
                    lines.append("")
                    lines.append("<!-- %s -->" % block.anchor)
                lines.append("")
                lines.append(text)
                used += cost

        text = "\n".join(lines)
        return MarkdownResult(
            text=text,
            truncated=truncated,
            next_chapter=next_chapter,
            chapters_rendered=rendered,
            characters=len(text),
        )

    def _breadcrumb(self, chapter, section=None):
        parts = [self.meta.title or "Untitled"]
        if chapter.group and chapter.group not in parts:
            parts.append(chapter.group)
        if chapter.title and chapter.title not in parts:
            parts.append(chapter.title)
        if section and section not in parts:
            parts.append(section)
        return " > ".join(parts)

    def _render_block(self, block, include_footnotes=True):
        kind = block.kind
        text = block.text or ""

        if kind == "heading":
            body = "%s %s" % ("#" * min(6, max(1, block.level + 2)), text)
        elif kind == "quote":
            body = "\n".join("> " + line for line in text.splitlines())
        elif kind == "code":
            body = "```\n%s\n```" % text
        elif kind == "hr":
            body = "---"
        elif kind == "list":
            body = text
        else:
            body = text

        if include_footnotes and block.footnotes:
            notes = []
            for number, note_text in block.footnotes:
                notes.append("[^%d]: %s" % (number, note_text))
            body = body + "\n\n" + "\n".join(notes)
        return body


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _match_position(text, terms, pattern, ignore_case):
    if pattern is not None:
        match = pattern.search(text)
        return match.start() if match else None

    haystack = text.lower() if ignore_case else text
    best = None
    for term in terms:
        found = haystack.find(term)
        if found < 0:
            return None
        best = found if best is None else min(best, found)
    return best


def _snippet(text, position, max_tokens):
    if max_tokens is None or count_tokens(text) <= max_tokens:
        return text
    total = count_tokens(text)
    ratio = len(text) / float(max(1, total))
    window = max(120, int(max_tokens * ratio))
    start = max(0, position - window // 3)
    end = min(len(text), start + window)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return prefix + text[start:end].strip() + suffix
