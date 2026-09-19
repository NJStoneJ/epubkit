"""XHTML / XML parsing, with zero third-party dependencies.

Two parsers, one output shape:

* :mod:`xml.etree.ElementTree` for well-formed XHTML -- the overwhelmingly
  common case, and the only case the EPUB spec actually permits.
* A tolerant stack-based :class:`html.parser.HTMLParser` tree for files that
  are not well formed, which real-world EPUBs manage to produce anyway.

Both are normalised into :class:`Node`, so nothing downstream ever has to care
which one ran.
"""

from __future__ import annotations

import re
from html.entities import html5 as _HTML5_ENTITIES
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

NS = {
    "container": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "xhtml": "http://www.w3.org/1999/xhtml",
    "epub": "http://www.idpf.org/2007/ops",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
}

TEXT = "#text"

_DOCTYPE_RE = re.compile(r"<!DOCTYPE.*?>", re.IGNORECASE | re.DOTALL)
_ENTITY_RE = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")
_XML_ENC_RE = re.compile(rb'<\?xml[^>]*encoding=["\']([\w.-]+)["\']', re.IGNORECASE)
_WS_RE = re.compile(r"[ \t\r\n\f\v\u00a0\u3000]+")


# --------------------------------------------------------------------------- #
# Node
# --------------------------------------------------------------------------- #


class Node:
    """A normalised element, or a text run when ``tag == "#text"``."""

    __slots__ = ("tag", "attrib", "children", "data")

    def __init__(self, tag, attrib=None, data=""):
        self.tag = tag
        self.attrib = attrib if attrib is not None else {}
        self.children = []
        self.data = data

    @property
    def is_text(self):
        return self.tag == TEXT

    def get(self, *names, default=None):
        for name in names:
            if name in self.attrib:
                return self.attrib[name]
        return default

    def epub_type(self):
        """Return the ``epub:type`` value, whatever prefix the file used."""
        for key, value in self.attrib.items():
            if key == "epub:type" or key.endswith("}type"):
                return value
        return ""

    def text_content(self):
        if self.is_text:
            return self.data
        return "".join(child.text_content() for child in self.children)

    def elements(self):
        """Iterate element children (text runs excluded)."""
        for child in self.children:
            if not child.is_text:
                yield child

    def iter(self):
        """Depth-first over this node and all element descendants."""
        yield self
        for child in self.children:
            if not child.is_text:
                yield from child.iter()

    def find(self, *tags):
        wanted = set(tags)
        for node in self.iter():
            if node.tag in wanted:
                return node
        return None

    def find_all(self, *tags):
        wanted = set(tags)
        return [node for node in self.iter() if node.tag in wanted]

    def __repr__(self):
        return "Node(%r, %d children)" % (self.tag, len(self.children))


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def local(tag):
    """``{uri}local`` -> ``local``, lowercased."""
    if not tag:
        return ""
    if tag.startswith("{"):
        tag = tag.split("}", 1)[1]
    return tag.lower()


def collapse(text):
    """Collapse all runs of whitespace -- including nbsp and ideographic space."""
    if not text:
        return ""
    return _WS_RE.sub(" ", text).strip()


def epub_type(node):
    """Module-level twin of :meth:`Node.epub_type`, tolerant of plain objects."""
    getter = getattr(node, "epub_type", None)
    if callable(getter):
        return getter()
    for key, value in getattr(node, "attrib", {}).items():
        if key == "epub:type" or key.endswith("}type"):
            return value
    return ""


# --------------------------------------------------------------------------- #
# Bytes -> str
# --------------------------------------------------------------------------- #


def decode_bytes(data):
    """Decode EPUB member bytes, honouring the XML declaration when present."""
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8", "replace")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace")
    match = _XML_ENC_RE.match(data[:200])
    if match:
        try:
            return data.decode(match.group(1).decode("ascii", "replace"))
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", "replace")


# --------------------------------------------------------------------------- #
# ElementTree -> Node
# --------------------------------------------------------------------------- #


def to_nodes(element):
    """Convert an ElementTree element into a :class:`Node` tree.

    Text and tail runs become ``#text`` children, so document order survives
    and downstream code never touches ``.text`` / ``.tail``.
    """
    node = Node(local(element.tag), dict(element.attrib))
    if element.text:
        node.children.append(Node(TEXT, data=element.text))
    for child in element:
        node.children.append(to_nodes(child))
        if child.tail:
            node.children.append(Node(TEXT, data=child.tail))
    return node


def _sanitize(text):
    """Remove the DOCTYPE and rewrite named HTML entities as numeric refs."""
    text = _DOCTYPE_RE.sub("", text)

    def replace(match):
        char = _HTML5_ENTITIES.get(match.group(1) + ";")
        if not char:
            return match.group(0)
        return "".join("&#%d;" % ord(c) for c in char)

    return _ENTITY_RE.sub(replace, text)


# --------------------------------------------------------------------------- #
# Tolerant HTML fallback
# --------------------------------------------------------------------------- #

_VOID = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)

_SKIP_CONTENT = frozenset("script style head title".split())

_HEADINGS = "h1 h2 h3 h4 h5 h6".split()

_CLOSES = {
    "p": {"p"},
    "li": {"li"},
    "dt": {"dt", "dd"},
    "dd": {"dt", "dd"},
    "tr": {"tr", "td", "th"},
    "td": {"td", "th"},
    "th": {"td", "th"},
    "option": {"option"},
    "optgroup": {"option", "optgroup"},
    "thead": {"tbody", "tfoot", "tr", "td", "th"},
    "tbody": {"tbody", "tfoot", "tr", "td", "th"},
    "tfoot": {"tbody", "tr", "td", "th"},
    "nav": {"nav"},
    "aside": {"aside"},
    "section": {"section"},
    "article": {"article"},
    "blockquote": {"blockquote"},
}
for _tag in _HEADINGS:
    _CLOSES[_tag] = set(_HEADINGS) | {"p"}


class _TolerantParser(HTMLParser):
    """Build a :class:`Node` tree from markup that is not well formed."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("div")
        self._stack = [self.root]
        self._skip = 0

    @property
    def _current(self):
        return self._stack[-1]

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_CONTENT:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in _VOID:
            self._current.children.append(Node(tag, _attrs(attrs)))
            return
        closable = _CLOSES.get(tag)
        if closable:
            while len(self._stack) > 1 and self._stack[-1].tag in closable:
                self._stack.pop()
        node = Node(tag, _attrs(attrs))
        self._current.children.append(node)
        self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        if self._skip or tag in _SKIP_CONTENT:
            return
        self._current.children.append(Node(tag, _attrs(attrs)))

    def handle_endtag(self, tag):
        if tag in _SKIP_CONTENT:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data):
        if self._skip or not data:
            return
        self._current.children.append(Node(TEXT, data=data))


def _attrs(attrs):
    out = {}
    for key, value in attrs:
        if key not in out:
            out[key] = value if value is not None else ""
    return out


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def parse_markup(data):
    """Parse XHTML/XML bytes into a :class:`Node` tree.

    Never raises on malformed input; worst case the tolerant parser runs and
    some structure is lost, but text still comes through.
    """
    text = decode_bytes(data) if isinstance(data, (bytes, bytearray)) else data
    if not text:
        return Node("div")
    text = text.lstrip("\ufeff")
    if not text.strip():
        return Node("div")

    try:
        return to_nodes(ET.fromstring(text))
    except ET.ParseError:
        pass

    cleaned = _sanitize(text)
    try:
        return to_nodes(ET.fromstring(cleaned))
    except ET.ParseError:
        pass

    parser = _TolerantParser()
    try:
        parser.feed(cleaned)
        parser.close()
    except Exception:  # noqa: BLE001 - html.parser can raise on hostile input
        pass
    return parser.root
