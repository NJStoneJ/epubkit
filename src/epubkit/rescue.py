"""Last-resort chapter detection for books with no usable structure.

Project Gutenberg's Chinese catalogue is the motivating case. ``紅樓夢`` ships
as 32 ``.txt.html`` files of bare ``<p>`` elements: no headings, a one-entry
NCX, and the chapter titles sitting inside the prose as ordinary lines. By
markup there is exactly one chapter in that book. By text there are a hundred
and twenty.

So when a chapter comes out implausibly large, epubkit scans the text for
chapter markers and cuts there. This is a rescue, not a strategy: it only runs
on chapters above :data:`RESCUE_TOKENS`, only when it finds at least three
markers, and only when the resulting segments are of a plausible chapter size.
Everything else is left exactly as the book declared it.

The scan works line by line rather than on offsets, because these files are
hard-wrapped: a title as ordinary as ``第三回　賈雨村夤緣復舊職`` can be split
across two physical lines, and an offset-based cut would keep only ``第三回``.
"""

from __future__ import annotations

import re

from .model import Block, Chapter
from .nodes import collapse
from .tokens import count_tokens, split_long_text

RESCUE_TOKENS = 20000
RESCUE_MAX_SEGMENT_TOKENS = 12000
RESCUE_MIN_MARKERS = 3

# A single block larger than this is not a paragraph, it is a missing paragraph
# break. Text-derived EPUBs collapse whole chapters into one ``<p>``: 紅樓夢 has
# a 13,616-token block, and a chunker that cannot split it emits a 13,616-token
# "chunk", which is precisely the context bomb this library exists to avoid.
MAX_BLOCK_TOKENS = 800

_NUMERALS = "0-9０-９一二三四五六七八九十百千零〇两廿卅"

# What may follow "第X回" for it to be a title rather than the start of a
# sentence. This is the whole difference between the title
# "第四回　薄命女偏逢薄命郎" and the prose line
# "第四回中既將薛家母子在榮府內寄居等事略已表明…", and hard-wrapped
# sources make the two the same length, so length alone cannot separate them.
_TITLE_TAIL = r"(?=$|[\s\u3000：:、，,．.；;·—\-—])"

_CHAPTER_PATTERNS = (
    re.compile(r"^第\s*[%s]{1,6}\s*[回章節节卷篇部集]%s" % (_NUMERALS, _TITLE_TAIL)),
    re.compile(r"^(?:卷|回|章|節|节)\s*[%s]{1,6}\s*[、.．:：]" % _NUMERALS),
    re.compile(r"^(?:CHAPTER|Chapter|chapter)\s+(?:[IVXLCDM]{1,7}|\d{1,4})\b"),
    re.compile(r"^(?:PART|Part|BOOK|Book)\s+(?:[IVXLCDM]{1,7}|\d{1,4})\b"),
)

_MAX_TITLE_CHARS = 60
_MIN_TITLE_CHARS = 14
_MAX_CONTINUATION_CHARS = 40

_TRAILING_NOISE = "-—_=·*～~ \t\u3000\xa0"
_SEPARATOR_CHARS = set("-—_=·*～~ \t\u3000\xa0")
_SENTENCE_END = "。．.！？!?；;"

# A line only ever needs to be inspected if it plausibly starts a title.
_LINE_START = re.compile(r"^\s*(?:第|[卷回章節节]|CHAPTER|Chapter|chapter|PART|Part|BOOK|Book)")


def looks_like_heading(text):
    """Is this whole block a chapter title rather than prose?"""
    return _title_of(text or "") is not None


def split_oversized_blocks(chapters, max_tokens=MAX_BLOCK_TOKENS, warnings=None):
    """Re-cut blocks that are too large to be a paragraph.

    Splitting happens here rather than inside the chunker so that the pieces
    become ordinary, separately addressable blocks. That keeps one rule
    everywhere -- ``cNpM`` is always unique and always resolves to exactly the
    text you were shown -- instead of a special "this chunk is block 43, part 3"
    case that every consumer would have to know about.

    Returns the number of blocks that were split. Text is preserved: the pieces
    are sentence-boundary slices, so they rejoin into the original.
    """
    split_count = 0
    for chapter in chapters:
        rebuilt = []
        for block in chapter.blocks:
            if block.is_heading or block.tokens <= max_tokens:
                rebuilt.append(block)
                continue
            pieces = split_long_text(block.text, max_tokens)
            if len(pieces) < 2:
                rebuilt.append(block)
                continue
            for piece in pieces:
                clone = Block(
                    block.kind,
                    piece,
                    level=block.level,
                    page=block.page,
                    src_path=block.src_path,
                    src_id=block.src_id,
                )
                clone.tokens = count_tokens(piece)
                # ``raw`` is only read by chapter rescue, which has already run
                # by this point; keeping a stale copy would be worse than none.
                rebuilt.append(clone)

            # Footnotes belong to the piece that carries their [^n] marker, or
            # to the last piece if the marker was lost. Dropping them would be
            # silent data loss.
            for number, note in block.footnotes:
                marker = "[^%d]" % number
                target = next(
                    (clone for clone in rebuilt[-len(pieces):] if marker in clone.text),
                    rebuilt[-1],
                )
                target.footnotes.append((number, note))

            split_count += 1
        chapter.blocks = rebuilt
        chapter.tokens = sum(item.tokens for item in rebuilt)

    if split_count and warnings is not None:
        warnings.append(
            "split %d oversized block(s) at sentence boundaries; the source had "
            "no paragraph break there" % split_count
        )
    return split_count


def rescue_chapters(chapters, warnings=None):
    """Split implausibly large chapters at textual chapter markers."""
    out = []
    rescued = 0
    for chapter in chapters:
        split = _rescue_one(chapter)
        if split is None:
            out.append(chapter)
            continue
        out.extend(split)
        rescued += 1

    if rescued and warnings is not None:
        warnings.append(
            "recovered chapter boundaries from the text itself in %d oversized "
            "chapter(s); the book's own markup declared no structure there" % rescued
        )
    return out


def _rescue_one(chapter):
    if chapter.tokens <= RESCUE_TOKENS:
        return None

    marker_count = _count_markers(chapter)
    if marker_count < RESCUE_MIN_MARKERS:
        return None
    if chapter.tokens / float(marker_count + 1) > RESCUE_MAX_SEGMENT_TOKENS:
        return None

    groups = []
    current_title = chapter.title
    current_blocks = []

    for block in chapter.blocks:
        if not _has_markers(block):
            current_blocks.append(block)
            continue
        for kind, text in _cut(block):
            if kind == "heading":
                groups.append((current_title, current_blocks))
                current_title = text
                current_blocks = [_block("heading", text, block, level=2)]
            else:
                current_blocks.append(_block("para", text, block))
    groups.append((current_title, current_blocks))

    rebuilt = _rebuild(groups)
    if len(rebuilt) < RESCUE_MIN_MARKERS:
        return None
    return rebuilt


# --------------------------------------------------------------------------- #
# Marker detection
# --------------------------------------------------------------------------- #


def _count_markers(chapter):
    total = 0
    for block in chapter.blocks:
        if block.dedup:
            continue
        if looks_like_heading(block.text):
            total += 1
        elif block.raw and block.tokens >= 1000:
            total += len(_titles_in(block))
    return total


def _has_markers(block):
    if block.dedup:
        return False
    if looks_like_heading(block.text):
        return True
    # Only trust a marker buried in a blob when the blob is big enough that a
    # bare line is clearly structural rather than prose.
    return bool(block.raw) and block.tokens >= 1000 and bool(_titles_in(block))


def _titles_in(block):
    source = block.raw if block.raw else block.text
    return [title for title in (_title_of(line) for line in source.split("\n")) if title]


def _title_of(line):
    """Return the title if this line starts a chapter, else ``None``."""
    stripped = line.strip().strip(_TRAILING_NOISE)
    if not stripped or len(stripped) > _MAX_TITLE_CHARS:
        return None
    if not _LINE_START.match(stripped):
        return None
    for pattern in _CHAPTER_PATTERNS:
        if pattern.match(stripped):
            return stripped
    return None


# --------------------------------------------------------------------------- #
# Cutting
# --------------------------------------------------------------------------- #


def _cut(block):
    """Split a block into ``("heading"|"body", text)`` pieces at its markers."""
    source = block.raw if block.raw else block.text
    lines = source.split("\n")
    out = []
    buffer = []
    index = 0

    while index < len(lines):
        line = lines[index]
        title = _title_of(line)
        if title is None:
            if not _is_separator(line):
                buffer.append(line)
            index += 1
            continue

        title, index = _extend_title(title, lines, index)

        if buffer:
            text = collapse(" ".join(buffer))
            if text:
                out.append(("body", text))
            buffer = []
        out.append(("heading", title))
        index += 1

    if buffer:
        text = collapse(" ".join(buffer))
        if text:
            out.append(("body", text))
    return out


def _extend_title(title, lines, index):
    """Join a hard-wrapped title with the line that continues it.

    Only when the first line is too short to be a title on its own *and* the
    next line looks like a continuation rather than the start of the prose.
    Rule lines between the two are stepped over, because the source shape::

        第五回
        ——————————————————
        游幻境指迷十二釵　飲仙醪曲演紅樓夢

    is exactly as common as the plain wrapped one.
    """
    if len(title) >= _MIN_TITLE_CHARS:
        return title, index

    probe = index + 1
    while probe < len(lines) and (
        not lines[probe].strip() or _is_separator(lines[probe])
    ):
        probe += 1
    if probe >= len(lines):
        return title, index

    extra = lines[probe].strip()
    if not _looks_like_continuation(extra) or _title_of(extra) is not None:
        return title, index
    return (title + " " + extra).strip(), probe


def _is_separator(line):
    stripped = line.strip()
    if len(stripped) < 3:
        return False
    return all(char in _SEPARATOR_CHARS for char in stripped)


# --------------------------------------------------------------------------- #
# Rebuilding
# --------------------------------------------------------------------------- #


def _block(kind, text, source_block, level=0):
    block = Block(kind, text, level=level, page=source_block.page,
                  src_path=source_block.src_path)
    block.tokens = count_tokens(text)
    return block


def _rebuild(groups):
    chapters = []
    for title, blocks in groups:
        blocks = [block for block in blocks if block.text.strip()]
        if not blocks:
            continue
        chapter = Chapter(len(chapters), title, blocks, [], depth=0, synthetic=True)
        _repair_split_title(chapter)
        first = chapter.blocks[0]
        if first.is_heading and _norm(first.text) == _norm(chapter.title):
            first.dedup = True
        chapters.append(chapter)
    return chapters


def _repair_split_title(chapter):
    """Rejoin a title that the producer split across several elements.

    ``紅樓夢`` again: the source is::

        <p>第三回</p>
        <p>————————————————</p>
        <p>　　　　　賈雨村夤緣復舊職　林黛玉拋父進京都</p>

    Without this the chapter is called "第三回" and the real title reads as the
    chapter's opening line. The separator is what makes this safe to detect --
    a short heading followed by a rule followed by a title-like line is not a
    shape ordinary prose produces.
    """
    if len(chapter.title) >= _MIN_TITLE_CHARS:
        return
    if not chapter.blocks or not chapter.blocks[0].is_heading:
        return

    absorbed = []
    seen_rule = False
    continuation = None
    for block in chapter.blocks[1:]:
        text = block.text.strip()
        if _is_separator(text):
            seen_rule = True
            absorbed.append(block)
            continue
        if seen_rule and _looks_like_continuation(text):
            absorbed.append(block)
            continuation = text
        break

    if continuation is None or not absorbed:
        return

    heading = chapter.blocks[0]
    heading.text = (chapter.title + " " + continuation).strip()
    heading.tokens = count_tokens(heading.text)
    chapter.title = heading.text

    dropped = {id(block) for block in absorbed}
    chapter.blocks = [block for block in chapter.blocks if id(block) not in dropped]
    chapter.tokens = sum(block.tokens for block in chapter.blocks)


def _looks_like_continuation(text):
    if not text or len(text) > _MAX_CONTINUATION_CHARS:
        return False
    if text[-1] in _SENTENCE_END:
        return False
    if text.startswith(("- ", "1. ")):
        return False
    return _title_of(text) is None


_NOISE_RE = re.compile(
    r"[\s\u3000\u00a0·・:：.。、,，;；\-—_=「」『』【】()（）\[\]<>《》]+"
)


def _norm(text):
    return _NOISE_RE.sub("", (text or "").lower())
