"""epubkit -- the EPUB toolkit for AI agents.

A book is not a blob of text. It is a tree of chapters and blocks, and every
one of them has a stable anchor, so an agent can outline, search, quote and
re-read without ever holding the whole book in its context window.

    >>> from epubkit import open_book
    >>> book = open_book("novel.epub")
    >>> book.outline()[0]["title"]
    'Chapter 1'
    >>> book.search("the whale", limit=3)[0].anchor
    'c1p4'
    >>> book.read("c1p4", max_tokens=800).text

Zero runtime dependencies.
"""

from .api import inspect, open_book
from .container import EpubArchive, ManifestItem
from .errors import (
    AnchorError,
    DrmProtectedError,
    EpubKitError,
    NotAnEpubError,
    TocError,
)
from .model import (
    Block,
    Book,
    BookMeta,
    Chapter,
    Chunk,
    Hit,
    MarkdownResult,
    ReadResult,
    TocEntry,
    parse_anchor,
)
from .tokens import count_tokens, use_tiktoken

__version__ = "0.1.0"

__all__ = [
    "open_book",
    "inspect",
    "EpubArchive",
    "ManifestItem",
    "Book",
    "BookMeta",
    "Chapter",
    "Block",
    "Chunk",
    "Hit",
    "MarkdownResult",
    "ReadResult",
    "TocEntry",
    "parse_anchor",
    "count_tokens",
    "use_tiktoken",
    "EpubKitError",
    "NotAnEpubError",
    "DrmProtectedError",
    "AnchorError",
    "TocError",
    "__version__",
]
