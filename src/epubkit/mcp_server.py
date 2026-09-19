"""A Model Context Protocol server, implemented on the standard library.

No ``mcp`` package, no ``fastmcp``, no dependencies. The stdio transport is
newline-delimited JSON-RPC 2.0, which is a few dozen lines to speak directly --
and keeping it that way means ``uvx epubkit-mcp`` starts instantly and never
fails to install.

Run it with::

    epubkit-mcp

The tool set is deliberately shaped around a reading loop rather than around
conversion: ``outline`` to see the book cheaply, ``search`` to find a place,
``read`` to read it, ``chunks`` when you need everything. Dumping a whole book
into a context window is the thing this server exists to avoid.
"""

from __future__ import annotations

import io
import json
import os
import sys
import traceback

from . import __version__
from .api import open_book
from .errors import EpubKitError

SERVER_NAME = "epubkit"
DEFAULT_PROTOCOL = "2025-06-18"
SUPPORTED_PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18")

INSTRUCTIONS = (
    "epubkit gives you an addressable view of an EPUB. Every chapter has an "
    "anchor like 'c3' and every block has one like 'c3p12'.\n\n"
    "Read a book like this:\n"
    "1. epub_info -- size, chapter count, token count. Cheap. Do this first.\n"
    "2. epub_outline -- the chapter map with token costs per chapter. Also "
    "cheap (roughly 1-2% of the book). Read this before reading any prose.\n"
    "3. epub_search -- find a term, get anchors back. Better than guessing.\n"
    "4. epub_read -- read from an anchor, with a token budget. Returns "
    "next_anchor so you can continue without re-reading.\n\n"
    "Never convert the whole book to Markdown just to answer a question about "
    "one part of it; that is exactly what the anchors are for. Use epub_chunks "
    "only when you genuinely need the whole text, and page through it with "
    "offset/limit."
)


# --------------------------------------------------------------------------- #
# Book cache
# --------------------------------------------------------------------------- #

_CACHE = {}
_CACHE_LIMIT = 4


def load_book(path):
    """Parse a book, reusing the previous parse while the file is unchanged."""
    if not path:
        raise EpubKitError("a book path is required")
    real = os.path.abspath(os.path.expanduser(str(path)))
    if not os.path.isfile(real):
        raise EpubKitError("no such file: %s" % real)

    stat = os.stat(real)
    stamp = (stat.st_mtime_ns, stat.st_size)
    entry = _CACHE.get(real)
    if entry is not None and entry[0] == stamp:
        return entry[1]

    book = open_book(real)
    _CACHE[real] = (stamp, book)
    while len(_CACHE) > _CACHE_LIMIT:
        _CACHE.pop(next(iter(_CACHE)))
    return book


# --------------------------------------------------------------------------- #
# Tool implementations
# --------------------------------------------------------------------------- #


def tool_info(args):
    book = load_book(args.get("book"))
    payload = book.stats()
    payload["meta"] = book.meta.to_dict()
    return payload


def tool_outline(args):
    book = load_book(args.get("book"))
    depth = int(args.get("depth") or 2)
    return {
        "title": book.meta.title,
        "chapters": len(book),
        "tokens": book.tokens,
        "outline": book.outline(depth=depth),
    }


def tool_read(args):
    book = load_book(args.get("book"))
    max_tokens = args.get("max_tokens")
    result = book.read(
        args.get("anchor"),
        max_tokens=int(max_tokens) if max_tokens else None,
    )
    return result.to_dict()


def tool_search(args):
    book = load_book(args.get("book"))
    hits = book.search(
        args.get("query"),
        limit=int(args.get("limit") or 20),
        all_terms=bool(args.get("all_terms")),
        regex=bool(args.get("regex")),
        max_tokens=int(args.get("snippet_tokens") or 400),
    )
    return {"query": args.get("query"), "count": len(hits),
            "hits": [hit.to_dict() for hit in hits]}


def tool_chunks(args):
    book = load_book(args.get("book"))
    max_tokens = int(args.get("max_tokens") or 800)
    offset = int(args.get("offset") or 0)
    # Deliberately small. A page of chunks is still a lot of context, and the
    # whole point of this server is that no single call dumps a book.
    limit = int(args.get("limit") or 10)
    chunks = book.chunks(max_tokens=max_tokens)
    window = chunks[offset : offset + limit]
    return {
        "total_chunks": len(chunks),
        "offset": offset,
        "returned": len(window),
        "next_offset": offset + len(window) if offset + len(window) < len(chunks) else None,
        "chunks": [chunk.to_dict() for chunk in window],
    }


def tool_markdown(args):
    book = load_book(args.get("book"))
    # Bounded by default: an unbounded render of a novel is ~200k tokens, which
    # is exactly the failure mode this server exists to prevent. An explicit 0
    # means "I know, give me everything".
    requested = args.get("max_tokens")
    if requested is None:
        max_tokens = 4000
    else:
        max_tokens = int(requested) or None
    result = book.render_markdown(
        anchors=bool(args.get("anchors")),
        start=args.get("start_chapter"),
        end=args.get("end_chapter"),
        max_tokens=max_tokens,
    )
    return result.to_dict()


def tool_list(args):
    directory = os.path.abspath(os.path.expanduser(str(args.get("directory") or ".")))
    if not os.path.isdir(directory):
        raise EpubKitError("not a directory: %s" % directory)
    found = []
    for root, _dirs, files in os.walk(directory):
        for name in files:
            if name.lower().endswith(".epub"):
                full = os.path.join(root, name)
                found.append({"path": full, "bytes": os.path.getsize(full)})
    found.sort(key=lambda item: item["path"])
    return {"directory": directory, "count": len(found), "books": found}


# --------------------------------------------------------------------------- #
# Tool schemas
# --------------------------------------------------------------------------- #

_BOOK_PROP = {"type": "string", "description": "Path to a .epub file"}

TOOLS = (
    {
        "name": "epub_info",
        "description": (
            "Metadata and size of an EPUB without extracting any prose: title, "
            "authors, chapter count, block count, estimated tokens, footnote "
            "count, print page count, and any notes about what the parser had "
            "to work around. Call this first."
        ),
        "handler": tool_info,
        "schema": {
            "type": "object",
            "properties": {"book": _BOOK_PROP},
            "required": ["book"],
        },
    },
    {
        "name": "epub_outline",
        "description": (
            "The chapter map of an EPUB: for each chapter, its anchor, title, "
            "token cost, page number and sub-sections. Roughly 1-2% of the "
            "book's token count, so this is the cheap way to understand a book "
            "before reading any of it."
        ),
        "handler": tool_outline,
        "schema": {
            "type": "object",
            "properties": {
                "book": _BOOK_PROP,
                "depth": {
                    "type": "integer",
                    "description": "1 = chapters only, 2 = include sections (default 2)",
                },
            },
            "required": ["book"],
        },
    },
    {
        "name": "epub_read",
        "description": (
            "Read a book from an anchor, optionally bounded by a token budget. "
            "anchor is 'c3' for a whole chapter, 'c3p12' to start at a block, or "
            "a chapter title. Returns the text plus next_anchor, so a long "
            "chapter can be read in pieces without re-reading anything."
        ),
        "handler": tool_read,
        "schema": {
            "type": "object",
            "properties": {
                "book": _BOOK_PROP,
                "anchor": {
                    "type": "string",
                    "description": "'c3', 'c3p12', or a chapter title",
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Stop after roughly this many tokens. Omit to read to the end of the chapter.",
                },
            },
            "required": ["book", "anchor"],
        },
    },
    {
        "name": "epub_search",
        "description": (
            "Search an EPUB and get anchors back. Footnote text is searched "
            "too. Use this instead of reading a whole book to find one passage; "
            "the returned anchors can be passed straight to epub_read."
        ),
        "handler": tool_search,
        "schema": {
            "type": "object",
            "properties": {
                "book": _BOOK_PROP,
                "query": {"type": "string"},
                "limit": {"type": "integer", "description": "default 20"},
                "all_terms": {
                    "type": "boolean",
                    "description": "Require every whitespace-separated term to appear in the same block.",
                },
                "regex": {"type": "boolean", "description": "Treat query as a regular expression."},
                "snippet_tokens": {
                    "type": "integer",
                    "description": "Token budget per snippet, default 400",
                },
            },
            "required": ["book", "query"],
        },
    },
    {
        "name": "epub_chunks",
        "description": (
            "Token-bounded chunks of the whole book, each carrying its anchor, "
            "breadcrumb and page range. Paginated with offset/limit (default "
            "10 chunks per call). Use this for indexing or bulk analysis, not "
            "for answering a single question."
        ),
        "handler": tool_chunks,
        "schema": {
            "type": "object",
            "properties": {
                "book": _BOOK_PROP,
                "max_tokens": {"type": "integer", "description": "default 800"},
                "offset": {"type": "integer", "description": "default 0"},
                "limit": {
                    "type": "integer",
                    "description": "Chunks per call, default 10. Keep this small; follow next_offset to page through.",
                },
            },
            "required": ["book"],
        },
    },
    {
        "name": "epub_markdown",
        "description": (
            "Render a chapter range of an EPUB as Markdown. Expensive: prefer "
            "epub_outline plus epub_read unless you actually need a document. "
            "Capped at ~4000 tokens per call by default and reports "
            "next_chapter when it stops, so page with start_chapter. "
            "Set anchors=true to keep <!-- c3p12 --> comments for citation."
        ),
        "handler": tool_markdown,
        "schema": {
            "type": "object",
            "properties": {
                "book": _BOOK_PROP,
                "anchors": {"type": "boolean", "description": "Interleave anchor comments."},
                "start_chapter": {"type": "integer", "description": "Chapter index to start at."},
                "end_chapter": {"type": "integer", "description": "Last chapter index to render, inclusive."},
                "max_tokens": {
                    "type": "integer",
                    "description": "Token cap, default 4000. Pass 0 to render the whole range uncapped.",
                },
            },
            "required": ["book"],
        },
    },
    {
        "name": "epub_list",
        "description": "Recursively list .epub files under a directory.",
        "handler": tool_list,
        "schema": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "Directory to scan, default '.'"}
            },
            "required": [],
        },
    },
)

_TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}


def _tool_descriptors():
    return [
        {
            "name": tool["name"],
            "description": tool["description"],
            "inputSchema": tool["schema"],
        }
        for tool in TOOLS
    ]


# --------------------------------------------------------------------------- #
# JSON-RPC
# --------------------------------------------------------------------------- #


class Server:
    def __init__(self, protocol=None):
        self.protocol = protocol or DEFAULT_PROTOCOL
        self.initialised = False

    def handle(self, message):
        """Return a response dict, or ``None`` for notifications."""
        if not isinstance(message, dict):
            return _error(None, -32600, "invalid request")

        method = message.get("method")
        message_id = message.get("id")
        params = message.get("params") or {}

        if message_id is None and method and method.startswith("notifications/"):
            if method == "notifications/initialized":
                self.initialised = True
            return None

        if method == "initialize":
            return self._initialize(message_id, params)
        if method == "ping":
            return _result(message_id, {})
        if method == "tools/list":
            return _result(message_id, {"tools": _tool_descriptors()})
        if method == "tools/call":
            return self._call(message_id, params)

        if message_id is None:
            return None
        return _error(message_id, -32601, "method not found: %s" % method)

    def _initialize(self, message_id, params):
        requested = params.get("protocolVersion")
        if requested in SUPPORTED_PROTOCOLS:
            self.protocol = requested
        return _result(
            message_id,
            {
                "protocolVersion": self.protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
                "instructions": INSTRUCTIONS,
            },
        )

    def _call(self, message_id, params):
        name = params.get("name")
        arguments = params.get("arguments") or {}
        tool = _TOOLS_BY_NAME.get(name)
        if tool is None:
            return _error(message_id, -32602, "unknown tool: %s" % name)
        if not isinstance(arguments, dict):
            return _error(message_id, -32602, "arguments must be an object")

        try:
            payload = tool["handler"](arguments)
        except EpubKitError as exc:
            return _result(message_id, _tool_error("%s: %s" % (type(exc).__name__, exc)))
        except (ValueError, TypeError, KeyError) as exc:
            return _result(message_id, _tool_error("bad arguments: %s" % exc))
        except Exception as exc:  # noqa: BLE001 - a tool must never kill the server
            return _result(
                message_id,
                _tool_error(
                    "internal error: %s\n%s" % (exc, traceback.format_exc(limit=3))
                ),
            )

        return _result(
            message_id,
            {
                "content": [
                    {"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}
                ],
                "isError": False,
            },
        )


def _result(message_id, payload):
    return {"jsonrpc": "2.0", "id": message_id, "result": payload}


def _error(message_id, code, message):
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "error": {"code": code, "message": message},
    }


def _tool_error(message):
    return {"content": [{"type": "text", "text": message}], "isError": True}


# --------------------------------------------------------------------------- #
# stdio transport
# --------------------------------------------------------------------------- #


def serve(stdin=None, stdout=None):
    """Serve MCP over a newline-delimited JSON-RPC 2.0 stream.

    Binary streams are preferred (that is what a real MCP client gives us) but
    text streams are accepted too, so the transport can be driven from a test
    harness or a shell pipe without hand-encoding bytes.
    """
    stdin = stdin if stdin is not None else sys.stdin.buffer
    stdout = stdout if stdout is not None else sys.stdout.buffer
    binary = not isinstance(stdout, io.TextIOBase)
    server = Server()

    while True:
        raw = stdin.readline()
        if not raw:
            break
        if isinstance(raw, (bytes, bytearray)):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                _write(stdout, _error(None, -32700, "parse error"), binary)
                continue
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _write(stdout, _error(None, -32700, "parse error"), binary)
            continue

        response = server.handle(message)
        if response is not None:
            _write(stdout, response, binary)

    return 0


def _write(stdout, payload, binary=True):
    text = json.dumps(payload, ensure_ascii=False)
    if binary:
        stdout.write(text.encode("utf-8"))
        stdout.write(b"\n")
    else:
        stdout.write(text)
        stdout.write("\n")
    stdout.flush()


def main(argv=None):
    """Entry point for the ``epubkit-mcp`` console script."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--version" in argv:
        print("epubkit-mcp %s" % __version__)
        return 0
    if "--help" in argv or "-h" in argv:
        print(
            "epubkit-mcp -- an MCP server for EPUB files.\n\n"
            "Speaks newline-delimited JSON-RPC 2.0 over stdin/stdout.\n"
            "Tools: %s\n\n"
            "Claude Desktop / Cursor configuration:\n"
            '  { "command": "epubkit-mcp", "args": [] }\n'
            % ", ".join(tool["name"] for tool in TOOLS)
        )
        return 0
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
