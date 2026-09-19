"""Command line interface.

Every subcommand is designed to be a tool an agent can shell out to, so the
default output is stable plain text and ``--json`` is available everywhere.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .api import open_book
from .errors import EpubKitError
from .model import parse_anchor
from .tokens import count_tokens, use_tiktoken


def _configure_streams():
    """Windows consoles default to a legacy codepage; CJK output needs UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _emit(payload, as_json, render):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        render(payload)


def _book(args):
    book = open_book(args.book)
    if getattr(args, "tiktoken", False) and not use_tiktoken():
        print("warning: tiktoken is not installed; using the built-in estimate",
              file=sys.stderr)
    return book


# --------------------------------------------------------------------------- #
# Subcommands
# --------------------------------------------------------------------------- #


def cmd_info(args):
    book = _book(args)
    payload = book.stats()
    payload["meta"] = book.meta.to_dict()
    payload["source"] = book.source

    def render(data):
        print("%s" % (data["title"] or "(untitled)"))
        if data["authors"]:
            print("by %s" % ", ".join(data["authors"]))
        print()
        print("  language      %s" % (data["language"] or "?"))
        print("  chapters      %d" % data["chapters"])
        print("  blocks        %d" % data["blocks"])
        print("  characters    %d" % data["characters"])
        print("  tokens        ~%d" % data["tokens"])
        print("  footnotes     %d" % data["footnotes"])
        print("  print pages   %s" % (data["print_pages"] or "n/a"))
        if data["meta"]["series"]:
            print("  series        %s #%s" % (data["meta"]["series"],
                                             data["meta"]["series_index"]))
        if data["warnings"]:
            print()
            print("  notes:")
            for warning in data["warnings"]:
                print("    - %s" % warning)

    _emit(payload, args.json, render)


def cmd_outline(args):
    book = _book(args)
    payload = book.outline(depth=args.depth)

    def render(nodes):
        print("%s -- %d chapters, ~%d tokens"
              % (book.meta.title or "(untitled)", len(nodes), book.tokens))
        print()
        for node in nodes:
            group = "%s / " % node["group"] if node["group"] else ""
            print("  %-6s %s%s" % (node["anchor"], group, node["title"]))
            for section in node["sections"]:
                print("         %-6s   %s" % (section["anchor"], section["title"]))

    _emit(payload, args.json, render)


def cmd_read(args):
    book = _book(args)
    result = book.read(args.anchor, max_tokens=args.max_tokens)

    def render(data):
        print("--- %s" % data["breadcrumb"])
        print("--- %s .. %s  ~%d tokens%s"
              % (data["anchor"], data["anchor_end"], data["tokens"],
                 "" if not data["truncated"] else "  (truncated)"))
        if data["next_anchor"]:
            print("--- continue with %s" % data["next_anchor"])
        print()
        print(data["text"])

    _emit(result.to_dict(), args.json, render)


def cmd_search(args):
    book = _book(args)
    hits = book.search(
        args.query,
        limit=args.limit,
        all_terms=args.all_terms,
        regex=args.regex,
        max_tokens=args.max_tokens,
        chapter=args.chapter,
    )
    payload = [hit.to_dict() for hit in hits]

    def render(rows):
        if not rows:
            print("no matches")
            return
        for row in rows:
            page = " p.%s" % row["page"] if row["page"] else ""
            print("%s  %s%s" % (row["anchor"], row["chapter"], page))
            print("    %s" % row["text"].replace("\n", "\n    "))
            print()

    _emit(payload, args.json, render)


def cmd_chunks(args):
    book = _book(args)
    chunks = book.chunks(max_tokens=args.max_tokens, overlap_blocks=args.overlap)
    selected = chunks[args.offset : args.offset + args.limit] if args.limit else chunks[args.offset :]

    if args.jsonl:
        for chunk in selected:
            print(json.dumps(chunk.to_dict(), ensure_ascii=False))
        return

    payload = [chunk.to_dict() for chunk in selected]

    def render(rows):
        print("%d chunks (showing %d from offset %d), max_tokens=%d"
              % (len(chunks), len(rows), args.offset, args.max_tokens))
        print()
        for row in rows:
            print("%s .. %s  ~%d tokens" % (row["anchor"], row["anchor_end"], row["tokens"]))
            print("    %s" % row["breadcrumb"])
            print()

    _emit(payload, args.json, render)


def cmd_markdown(args):
    book = _book(args)
    result = book.render_markdown(
        anchors=args.anchors,
        start=args.start,
        end=args.end,
        max_tokens=args.max_tokens,
    )
    text = result.text
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text)
        print("wrote %s (%d characters, ~%d tokens)"
              % (args.output, len(text), count_tokens(text)))
    else:
        print(text)
    if result.truncated:
        print("\n[truncated at chapter %s -- continue with --start %s]"
              % (result.next_chapter, result.next_chapter), file=sys.stderr)


def cmd_doctor(args):
    """Report what the parser found, and what it had to guess."""
    from .api import inspect

    info = inspect(args.book)
    book = open_book(args.book)

    payload = {
        "source": info["source"],
        "opf_path": info["opf_path"],
        "spine_documents": len(info["spine"]),
        "manifest_items": len(info["manifest"]),
        "meta": info["meta"],
        "stats": book.stats(),
        "chapters": [
            {
                "anchor": chapter.anchor,
                "title": chapter.title,
                "tokens": chapter.tokens,
                "blocks": len(chapter.blocks),
                "documents": chapter.paths,
                "title_from_toc": chapter.title_from_toc,
                "group": chapter.group,
            }
            for chapter in book
        ],
        "toc_entries": len(book.toc),
    }

    def render(data):
        stats = data["stats"]
        print("%s" % (stats["title"] or "(untitled)"))
        print("  package       %s" % data["opf_path"])
        print("  spine         %d documents" % data["spine_documents"])
        print("  manifest      %d items" % data["manifest_items"])
        print("  toc           %d entries" % data["toc_entries"])
        print("  chapters      %d" % stats["chapters"])
        print("  tokens        ~%d" % stats["tokens"])
        print()
        if stats["warnings"]:
            print("  what epubkit had to work around:")
            for warning in stats["warnings"]:
                print("    - %s" % warning)
        else:
            print("  nothing unusual: the book declared its own structure")
        print()
        guessed = [c for c in data["chapters"] if not c["title_from_toc"]]
        if guessed:
            print("  %d chapter(s) titled from headings rather than the TOC:"
                  % len(guessed))
            for chapter in guessed[:10]:
                print("    %s  %s" % (chapter["anchor"], chapter["title"]))

    _emit(payload, args.json, render)


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #


def build_parser():
    parser = argparse.ArgumentParser(
        prog="epubkit",
        description="The EPUB toolkit for AI agents. Zero dependencies.",
    )
    parser.add_argument("--version", action="version", version="epubkit %s" % __version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add(name, help_text):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("book", help="path to a .epub file")
        sub.add_argument("--json", action="store_true", help="machine-readable output")
        sub.add_argument("--tiktoken", action="store_true",
                         help="use tiktoken for exact token counts")
        return sub

    sub = add("info", "metadata and size, without extracting prose")
    sub.set_defaults(func=cmd_info)

    sub = add("outline", "the chapter map: anchors, titles, token costs")
    sub.add_argument("--depth", type=int, default=2,
                     help="1 = chapters only, 2 = include sections (default 2)")
    sub.set_defaults(func=cmd_outline)

    sub = add("read", "read from an anchor or a chapter title")
    sub.add_argument("anchor", help="'c3', 'c3p12', or a chapter title")
    sub.add_argument("--max-tokens", type=int, default=None,
                     help="stop after roughly this many tokens")
    sub.set_defaults(func=cmd_read)

    sub = add("search", "find text and get anchors back")
    sub.add_argument("query")
    sub.add_argument("--limit", type=int, default=20)
    sub.add_argument("--all-terms", action="store_true",
                     help="require every whitespace-separated term to match")
    sub.add_argument("--regex", action="store_true")
    sub.add_argument("--max-tokens", type=int, default=400,
                     help="snippet budget per hit (default 400)")
    sub.add_argument("--chapter", type=int, default=None,
                     help="restrict to one chapter index")
    sub.set_defaults(func=cmd_search)

    sub = add("chunks", "token-bounded chunks that keep their anchors")
    sub.add_argument("--max-tokens", type=int, default=800)
    sub.add_argument("--overlap", type=int, default=0,
                     help="carry this many blocks of overlap into the next chunk")
    sub.add_argument("--offset", type=int, default=0)
    sub.add_argument("--limit", type=int, default=0, help="0 = all")
    sub.add_argument("--jsonl", action="store_true", help="one JSON object per line")
    sub.set_defaults(func=cmd_chunks)

    sub = add("md", "render a chapter range as Markdown")
    sub.add_argument("-o", "--output", help="write to a file instead of stdout")
    sub.add_argument("--anchors", action="store_true",
                     help="interleave <!-- c3p12 --> comments")
    sub.add_argument("--start", type=int, default=None, help="start at this chapter index")
    sub.add_argument("--end", type=int, default=None,
                     help="stop after this chapter index, inclusive")
    sub.add_argument("--max-tokens", type=int, default=None)
    sub.set_defaults(func=cmd_markdown)

    sub = add("doctor", "explain what the parser found and what it guessed")
    sub.set_defaults(func=cmd_doctor)

    return parser


def main(argv=None):
    _configure_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args) or 0
    except EpubKitError as exc:
        print("epubkit: %s" % exc, file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
