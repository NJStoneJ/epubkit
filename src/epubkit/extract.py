"""Turn XHTML documents into typed, addressable blocks.

Three things here are worth more than they look:

* Footnote bodies are pulled out of the prose flow and re-attached to the
  paragraph that references them. Generic converters flatten notes into the
  body text, which is the single biggest source of noise when you read a book
  through an LLM.
* Print page numbers from the EPUB 3 ``page-list`` nav are tracked, so a
  citation can be anchored to the paper edition rather than to a byte offset.
* Anything the walker does not recognise is still descended into, so text is
  never silently dropped just because the markup is unusual.
"""

from __future__ import annotations

import re

from .model import Block
from .nodes import collapse, epub_type
from .tokens import count_tokens

_HEADING_LEVELS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

_SKIP_TAGS = frozenset("script style head title nav".split())

_TEXT_TAGS = frozenset("p pre blockquote figcaption caption summary".split())

_LIST_TAGS = frozenset("li dd dt".split())

_STRUCTURAL_TAGS = frozenset("table hr img image svg figure".split())

_BLOCK_LEVEL = (
    _TEXT_TAGS
    | _LIST_TAGS
    | _STRUCTURAL_TAGS
    | _HEADING_LEVELS.keys()
    | frozenset(
        "div section article aside ul ol dl thead tbody tfoot tr main header footer".split()
    )
)

NOTE_TYPES = ("footnote", "endnote", "rearnote", "footnotes", "endnotes", "note")

NOTE_CLASS_STRONG = frozenset(
    "footnote footnotes endnote endnotes rearnote rearnotes notebody note-body".split()
)
NOTE_CLASS_WEAK = frozenset("fn note notes annotation comment".split())

_NOTEREF_HINTS = ("noteref", "note-ref", "fnref", "fn-ref", "footnoteref")

_CLASS_SPLIT = re.compile(r"[\s\-_]+")


def note_kind(node):
    """Classify a node as a note body: ``"strong"``, ``"weak"`` or ``""``.

    ``strong`` means "this is definitely a note body, keep it out of the prose".
    ``weak`` means "probably a note body, but only pull it out if something
    actually references it" -- safer, because ``class="note"`` is also used for
    ordinary callout boxes.
    """
    kind = epub_type(node).lower()
    if "noteref" in kind:
        return ""
    for token in NOTE_TYPES:
        if token in kind:
            return "strong"

    classes = (node.get("class") or "").lower()
    tokens = set(_CLASS_SPLIT.split(classes)) - {""}
    if tokens & NOTE_CLASS_STRONG:
        return "strong"
    if tokens & NOTE_CLASS_WEAK:
        return "weak"
    return ""


def looks_like_noteref(node, known_ids):
    """EPUB 2 has no ``epub:type``; it uses classes or bare ``#id`` links."""
    classes = (node.get("class") or "").lower()
    if any(hint in classes for hint in _NOTEREF_HINTS):
        return True
    href = node.get("href") or ""
    if href.startswith("#") and href[1:] in known_ids:
        return True
    return False


class Extractor:
    """Stateful across the whole book, because footnotes cross document lines."""

    def __init__(self, page_list=None, warnings=None):
        self.page_list = list(page_list or [])
        self.warnings = warnings if warnings is not None else []
        self.note_defs = {}
        self.note_weak_defs = {}
        self._note_numbers = {}
        self._next_note = 1

        self.path = ""
        self.page_map = {}
        self.page = None
        self.blocks = []
        self.id_positions = {}

    # -- pass 1: collect note bodies and page markers ---------------------- #

    def collect_notes(self, root):
        for node in root.iter():
            node_id = node.get("id")
            if node_id:
                kind = note_kind(node)
                if kind:
                    text = collapse(node.text_content())
                    if text:
                        target = (
                            self.note_defs if kind == "strong" else self.note_weak_defs
                        )
                        target.setdefault(node_id, text)
                    continue

            # The EPUB 2 shape: <div class="footnotes"><p id="fn1">...</p></div>
            # The container usually has no id of its own, so this check must not
            # be gated on one.
            classes = (node.get("class") or "").lower()
            if not any(token in classes for token in ("footnote", "endnote", "rearnote")):
                continue
            for child in node.elements():
                child_id = child.get("id")
                if not child_id or child_id in self.note_defs:
                    continue
                text = collapse(child.text_content())
                if text:
                    self.note_defs[child_id] = text

    def collect_page_map(self, root, path):
        """Map fragment ids in this document to printed page labels."""
        out = {}
        for entry_path, fragment, label in self.page_list:
            if fragment and (not entry_path or entry_path == path):
                out.setdefault(fragment, label)

        for node in root.iter():
            kind = epub_type(node).lower()
            if "pagebreak" not in kind:
                continue
            node_id = node.get("id") or ""
            if not node_id:
                href = node.get("href") or ""
                if href.startswith("#"):
                    node_id = href[1:]
            if not node_id:
                continue
            label = node.get("title") or collapse(node.text_content())
            if label:
                out.setdefault(node_id, label)
        return out

    # -- pass 2: extract blocks -------------------------------------------- #

    def extract(self, root, path):
        self.path = path
        self.page_map = self.collect_page_map(root, path)
        self.page = None
        self.blocks = []
        self.id_positions = {}

        body = None
        for node in root.iter():
            if node.tag == "body":
                body = node
                break
        self.walk(body if body is not None else root, "")
        return self.blocks

    # -- walking ------------------------------------------------------------ #

    def walk(self, node, parent_tag=""):
        for child in node.elements():
            tag = child.tag
            if tag in _SKIP_TAGS:
                continue
            if note_kind(child) == "strong" or child.get("id") in self.note_defs:
                continue

            self.enter(child)

            if tag in _HEADING_LEVELS:
                self.emit_heading(child)
            elif tag in _TEXT_TAGS:
                self.emit_text(child)
            elif tag in _LIST_TAGS:
                self.emit_list_item(child, parent_tag)
                self.walk(child, tag)
            elif tag == "table":
                self.emit_table(child)
            elif tag == "hr":
                self._add(Block("hr", page=self.page, src_path=self.path))
            elif tag in ("img", "image", "svg"):
                self.emit_image(child)
            elif tag == "figure":
                if not self.emit_figure(child):
                    self.walk(child, tag)
            elif tag in ("td", "th"):
                self.emit_text(child)
            else:
                self.walk(child, tag)

    def enter(self, node):
        """Track printed page labels and record where each id's content starts.

        ``id_positions`` is what lets a TOC entry pointing into the middle of a
        document (``book.htm.html#pgepubid00022``, the Project Gutenberg shape)
        be turned into a real chapter split.
        """
        node_id = node.get("id")
        if node_id and node_id not in self.id_positions:
            self.id_positions[node_id] = len(self.blocks)
        if not self.page_map or not node_id:
            return
        label = self.page_map.get(node_id)
        if label is not None:
            self.page = label

    # -- emitters ---------------------------------------------------------- #

    def emit_heading(self, node):
        block = Block(
            "heading",
            level=_HEADING_LEVELS[node.tag],
            page=self.page,
            src_path=self.path,
            src_id=node.get("id") or "",
        )
        block.text = self.inline_text(node, block)
        if block.text:
            self._add(block)

    def emit_text(self, node):
        kind = "para"
        if node.tag == "pre":
            kind = "code"
        elif node.tag == "blockquote":
            kind = "quote"

        block = Block(kind, page=self.page, src_path=self.path,
                      src_id=node.get("id") or "")
        if node.tag == "pre":
            block.text = _raw_text(node)
        else:
            block.text = self.inline_text(node, block)
        if block.text:
            self._add(block)

    def emit_list_item(self, node, parent_tag):
        block = Block("list", page=self.page, src_path=self.path,
                      src_id=node.get("id") or "")
        text = self.inline_text(node, block)
        if not text:
            return
        if parent_tag == "dl":
            block.text = text if node.tag == "dt" else "- " + text
        elif parent_tag == "ol":
            block.text = "1. " + text
        else:
            block.text = "- " + text
        self._add(block)

    def emit_table(self, node):
        rows = []
        for row in node.iter():
            if row.tag != "tr":
                continue
            cells = []
            for cell in row.elements():
                if cell.tag in ("td", "th"):
                    cells.append(collapse(cell.text_content()).replace("|", "\\|"))
            if cells:
                rows.append(cells)
        if not rows:
            return

        width = max(len(row) for row in rows)
        lines = []
        for index, row in enumerate(rows):
            padded = row + [""] * (width - len(row))
            lines.append("| " + " | ".join(padded) + " |")
            if index == 0:
                lines.append("|" + "|".join([" --- "] * width) + "|")
        self._add(
            Block("table", "\n".join(lines), page=self.page, src_path=self.path,
                  src_id=node.get("id") or "")
        )

    def emit_image(self, node):
        alt = collapse(node.get("alt") or node.get("title") or "")
        src = node.get("src") or ""
        if not alt and not src:
            return
        text = "![%s](%s)" % (alt, src) if src else alt
        self._add(Block("image", text, page=self.page, src_path=self.path,
                        src_id=node.get("id") or ""))

    def emit_figure(self, node):
        caption = ""
        image = None
        for child in node.elements():
            if child.tag == "figcaption" and not caption:
                caption = collapse(child.text_content())
            elif child.tag in ("img", "image", "svg") and image is None:
                image = child
        if image is None or not caption:
            return False
        src = image.get("src") or ""
        text = "![%s](%s)" % (caption, src) if src else caption
        self._add(Block("figure", text, page=self.page, src_path=self.path,
                        src_id=node.get("id") or ""))
        return True

    # -- inline text ------------------------------------------------------- #

    def inline_text(self, node, block):
        parts = []

        def walk(current):
            for child in current.children:
                if child.is_text:
                    parts.append(child.data)
                    continue
                child_id = child.get("id")
                if child_id and child_id not in self.id_positions:
                    # An inline anchor lands in the block currently being built.
                    self.id_positions[child_id] = len(self.blocks)
                tag = child.tag
                if tag in _BLOCK_LEVEL:
                    continue
                if tag == "br":
                    parts.append(" ")
                    continue
                if tag in ("img", "image"):
                    alt = child.get("alt") or child.get("title")
                    if alt:
                        parts.append(alt)
                    continue
                if tag == "a":
                    if self._is_noteref(child):
                        marker = self.register_noteref(child, block)
                        if marker:
                            parts.append(marker)
                        continue
                if tag == "ruby":
                    # <ruby>青锋<rt>qīng fēng</rt></ruby> -> 青锋(qīng fēng).
                    # The base text is a bare text node, not an element, which
                    # is exactly what makes this easy to get wrong.
                    base = []
                    reading = []
                    for part in child.children:
                        if part.is_text:
                            base.append(part.data)
                        elif part.tag in ("rt", "rp"):
                            reading.append(collapse(part.text_content()))
                        else:
                            base.append(collapse(part.text_content()))
                    word = collapse("".join(base))
                    note = "".join(reading)
                    parts.append("%s(%s)" % (word, note) if note else word)
                    continue
                walk(child)

        walk(node)
        joined = "".join(parts)
        if "\n" in joined:
            block.raw = joined
        return collapse(joined)

    def _is_noteref(self, node):
        kind = epub_type(node).lower()
        if "noteref" in kind:
            return True
        return looks_like_noteref(node, self.note_defs) or looks_like_noteref(
            node, self.note_weak_defs
        )

    def register_noteref(self, node, block):
        href = node.get("href") or ""
        if not href.startswith("#"):
            return ""
        target = href[1:]
        if not target:
            return ""

        number = self._note_numbers.get(target)
        if number is None:
            number = self._next_note
            self._next_note += 1
            self._note_numbers[target] = number

        text = self.note_defs.get(target) or self.note_weak_defs.get(target) or ""
        if not text:
            self.warnings.append("unresolved footnote reference %r" % target)
        if not any(existing == number for existing, _ in block.footnotes):
            block.footnotes.append((number, text))
        return "[^%d]" % number

    # -- bookkeeping ------------------------------------------------------- #

    def _add(self, block):
        block.tokens = count_tokens(block.text)
        self.blocks.append(block)


def _raw_text(node):
    lines = [line.rstrip() for line in node.text_content().splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)
