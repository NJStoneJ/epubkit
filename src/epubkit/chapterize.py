"""Assemble spine documents into addressable chapters.

The grouping rule is deliberately simple, because simple is what survives
contact with real books:

    A spine document starts a new chapter the first time the table of contents
    points at it. Every other spine document is appended to the chapter that is
    already open.

That one rule handles the two shapes that break naive converters:

* A chapter split across eight ``xhtml`` files (routine in Chinese EPUBs
  produced by Calibre) is glued back into one chapter, because only the first
  file is a TOC destination.
* Front matter, cover pages and part-title pages that the TOC omits stay where
  they belong instead of becoming junk chapters.

When there is no usable TOC, the same rule is applied to headings instead.
"""

from __future__ import annotations

import posixpath
import re

from .container import EpubArchive
from .extract import Extractor
from .model import Book, Chapter, block_anchor
from .nodes import parse_markup
from .rescue import rescue_chapters, split_oversized_blocks
from .toc import (
    annotate_ancestors,
    chapter_starts,
    choose_toc,
    parse_nav,
    parse_ncx,
)

_CONTENT_TYPES = frozenset(
    "application/xhtml+xml text/html application/x-dtbook+xml".split()
)

_TITLE_NOISE_RE = re.compile(
    r"[\s\u3000\u00a0·・:：.。、,，;；\-—_=「」『』【】()（）\[\]<>《》]+"
)


def build_book(source, archive=None):
    """Parse an EPUB into a :class:`Book`. This is the main entry point."""
    warnings = []
    own_archive = archive is None
    if own_archive:
        archive = EpubArchive(source)
    try:
        return _build(archive, source, warnings)
    finally:
        if own_archive:
            archive.close()


def _build(archive, source, warnings):
    nav_item = _find_nav_item(archive)
    ncx_item = _find_ncx_item(archive)

    nav_entries = []
    pages = []
    if nav_item is not None:
        try:
            nav_entries, pages = parse_nav(
                archive.read(nav_item.path), _dirname(nav_item.path)
            )
        except Exception as exc:  # noqa: BLE001 - a bad nav must not kill the parse
            warnings.append("EPUB 3 navigation document unreadable: %s" % exc)

    ncx_entries = []
    if ncx_item is not None:
        try:
            ncx_entries = parse_ncx(
                archive.read(ncx_item.path), _dirname(ncx_item.path)
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append("NCX unreadable: %s" % exc)

    toc_entries, toc_source = choose_toc(nav_entries, ncx_entries)
    annotate_ancestors(toc_entries)
    if not toc_entries:
        warnings.append(
            "no usable table of contents -- chapter boundaries inferred from headings"
        )

    # -- read every spine document once -------------------------------- #

    extractor = Extractor(pages, warnings)
    trees = []
    for item, linear in archive.spine:
        if item.media_type and item.media_type not in _CONTENT_TYPES:
            continue
        if nav_item is not None and item.id == nav_item.id:
            continue
        if ncx_item is not None and item.id == ncx_item.id:
            continue
        try:
            data = archive.read(item.path)
        except KeyError:
            warnings.append("spine references a file that is not in the archive: %r" % item.path)
            continue
        root = parse_markup(data)
        trees.append((item, linear, root))
        extractor.collect_notes(root)

    blocks_by_path = {}
    positions_by_path = {}
    for item, _linear, root in trees:
        blocks_by_path[item.path] = extractor.extract(root, item.path)
        positions_by_path[item.path] = dict(extractor.id_positions)

    # -- decide where chapters begin ------------------------------------ #

    spine_paths = [item.path for item, _, _ in trees]
    starts = [
        _Start(entry.path, entry.fragment, entry)
        for entry in chapter_starts(toc_entries, spine_paths)
    ]
    trust_toc = True

    if not starts:
        starts = _heading_starts(trees, blocks_by_path)
        trust_toc = False
        if starts:
            warnings.append("no TOC destinations matched the spine; used headings instead")
    elif len(starts) < 2:
        heading_starts = _heading_starts(trees, blocks_by_path)
        if len(heading_starts) > len(starts):
            # A one-entry TOC ("Contents") is a stub. Trust the headings instead
            # -- and their titles too, or the stub entry leaks into every title.
            warnings.append(
                "table of contents is a stub (1 usable entry); used headings instead"
            )
            starts = heading_starts
            trust_toc = False

    starts_by_path = {}
    for start in starts:
        starts_by_path.setdefault(start.path, []).append(start)

    # -- group, splitting documents at fragment boundaries ---------------- #

    pending = []
    for item, _linear, _root in trees:
        blocks = blocks_by_path.get(item.path) or []
        doc_starts = starts_by_path.get(item.path)

        if not doc_starts:
            if not pending:
                pending.append(_Pending(paths=[item.path], blocks=[], entry=None))
            if pending[-1].paths[-1] != item.path:
                pending[-1].paths.append(item.path)
            pending[-1].blocks.extend(blocks)
            continue

        cuts = _resolve_cuts(doc_starts, positions_by_path.get(item.path) or {})
        for start, segment in _segment(blocks, cuts):
            if start is None:
                # Blocks before this document's first chapter start belong to
                # whatever chapter is already open.
                if not pending:
                    pending.append(_Pending(paths=[item.path], blocks=[], entry=None))
                if pending[-1].paths[-1] != item.path:
                    pending[-1].paths.append(item.path)
                pending[-1].blocks.extend(segment)
            else:
                pending.append(
                    _Pending(paths=[item.path], blocks=list(segment), entry=start.entry)
                )

    chapters = []
    for group in pending:
        chapter = _finalize(group, len(chapters))
        if chapter is not None:
            chapters.append(chapter)

    chapters = rescue_chapters(chapters, warnings)
    split_oversized_blocks(chapters, warnings=warnings)

    for index, chapter in enumerate(chapters):
        chapter.index = index
        chapter.anchor = "c%d" % index
        for block_index, block in enumerate(chapter.blocks):
            block.anchor = block_anchor(index, block_index)

    _attach_sections(chapters, toc_entries)

    meta = archive.meta
    book = Book(
        meta=meta,
        chapters=chapters,
        toc=toc_entries,
        source=str(source) if source else "",
        warnings=_dedupe(warnings),
    )
    if toc_source == "ncx":
        book.warnings.append("chapter structure came from the EPUB 2 NCX")
    return book


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


class _Pending:
    __slots__ = ("paths", "blocks", "entry")

    def __init__(self, paths, blocks, entry):
        self.paths = paths
        self.blocks = blocks
        self.entry = entry


class _Start:
    """A place where a chapter begins.

    ``entry`` is ``None`` when the start came from a heading rather than from
    the navigation document, in which case the title comes from the heading.
    ``block_index`` is set when the start was derived from a heading, because
    headings usually carry no ``id`` and therefore no fragment to look up.
    """

    __slots__ = ("path", "fragment", "entry", "block_index")

    def __init__(self, path, fragment="", entry=None, block_index=None):
        self.path = path
        self.fragment = fragment
        self.entry = entry
        self.block_index = block_index


def _resolve_cuts(doc_starts, positions):
    """Turn chapter starts into ``(block_index, start)`` pairs, in order."""
    cuts = []
    for start in doc_starts:
        if start.block_index is not None:
            cuts.append((start.block_index, start))
            continue
        index = 0 if not start.fragment else positions.get(start.fragment)
        if index is None:
            # The TOC points at an id this document does not contain.
            continue
        cuts.append((index, start))
    if not cuts:
        cuts = [(0, doc_starts[0])]
    cuts.sort(key=lambda pair: pair[0])

    out = []
    seen = set()
    for index, start in cuts:
        if index in seen:
            continue  # two TOC entries on the same block would make an empty chapter
        seen.add(index)
        out.append((index, start))
    return out


def _segment(blocks, cuts):
    """Split a document's blocks into ``(start_or_None, block_slice)`` segments.

    ``start_or_None`` is ``None`` only for a leading run of blocks that precedes
    the document's first chapter start -- those belong to the previous chapter.
    """
    out = []
    if cuts[0][0] > 0:
        out.append((None, blocks[: cuts[0][0]]))
    for order, (index, start) in enumerate(cuts):
        end = cuts[order + 1][0] if order + 1 < len(cuts) else len(blocks)
        out.append((start, blocks[index:end]))
    return out


def _finalize(group, index):
    blocks = [block for block in group.blocks if block.text.strip() or block.kind == "hr"]
    if not blocks:
        return None

    if group.entry is not None and group.entry.title:
        title = group.entry.title
        depth = group.entry.depth
        group_title = " > ".join(group.entry.ancestors)
        from_toc = True
    else:
        heading = next((b for b in blocks if b.is_heading and b.text), None)
        title = heading.text if heading else ""
        depth = max(0, (heading.level - 1)) if heading else 0
        group_title = ""
        from_toc = False

    if not title:
        if all(block.kind in ("image", "figure", "hr") for block in blocks):
            title = "Cover"
        else:
            title = "Untitled section %d" % (index + 1)

    chapter = Chapter(
        index=index,
        title=title,
        blocks=blocks,
        paths=list(dict.fromkeys(group.paths)),
        depth=depth,
        synthetic=not from_toc,
        title_from_toc=from_toc,
        group=group_title,
    )

    # The chapter heading is usually repeated as the first block of the first
    # document. Render it once, as the chapter title.
    first = chapter.blocks[0]
    if first.is_heading and _norm(first.text) == _norm(title):
        first.dedup = True
    return chapter


def _heading_starts(trees, blocks_by_path):
    """Fallback chapter starts: every top-level heading, in every document.

    "Top-level" is judged per document -- the shallowest heading level that
    document actually uses. A file with one ``<h1>`` and six ``<h2>``s is one
    chapter with six sections. A file whose headings are all ``<h2>`` is split
    at every ``<h2>``, which is the shape of books that mark chapters up
    properly but ship a TOC with no usable destinations.

    This used to stop at the first heading of each document, so a single-file
    EPUB with fifty ``<h1>``s came out as one chapter.
    """
    out = []
    for item, _linear, _root in trees:
        headings = [
            (position, block)
            for position, block in enumerate(blocks_by_path.get(item.path) or [])
            if block.is_heading and block.level > 0
        ]
        if not headings:
            continue
        shallowest = min(block.level for _position, block in headings)
        if shallowest > 2:
            # Only deep headings present: that is emphasis, not structure.
            continue
        for position, block in headings:
            if block.level == shallowest:
                out.append(_Start(item.path, block_index=position))
    return out


def _attach_sections(chapters, toc_entries):
    """Map TOC sub-entries onto block anchors.

    A TOC entry like ``Text/ch1.xhtml#sec3`` names a block that may never have
    been a heading. Resolving it gives the outline the book's own sub-structure
    instead of only whatever the prose happened to mark up.
    """
    index_by_src = {}
    for chapter in chapters:
        for position, block in enumerate(chapter.blocks):
            if block.src_id:
                index_by_src.setdefault(
                    (block.src_path, block.src_id), (chapter, position, block)
                )

    for entry in toc_entries:
        if not entry.path or not entry.fragment or entry.depth <= 0:
            continue
        found = index_by_src.get((entry.path, entry.fragment))
        if found is None:
            continue
        chapter, position, block = found
        chapter.toc_sections.append((position, entry.title, block.anchor))


def _find_nav_item(archive):
    for item in archive.manifest.values():
        if item.has("nav"):
            return item
    return None


def _find_ncx_item(archive):
    if archive.spine_toc_id:
        item = archive.manifest.get(archive.spine_toc_id)
        if item is not None:
            return item
    for item in archive.manifest.values():
        if "dtbncx" in (item.media_type or ""):
            return item
    return None


def _dirname(path):
    return posixpath.dirname(path) if "/" in path else ""


def _norm(text):
    return _TITLE_NOISE_RE.sub("", (text or "").lower())


def _dedupe(warnings):
    seen = set()
    out = []
    for warning in warnings:
        if warning in seen:
            continue
        seen.add(warning)
        out.append(warning)
    return out[:40]
