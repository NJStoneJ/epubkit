"""Parse every EPUB in examples/ and report what came out.

This is the honest check: fixtures prove the code handles the shapes I thought
of, real books prove it handles the shapes I did not.

    python tests/real_books.py
"""

from __future__ import annotations

import glob
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from epubkit import EpubKitError, open_book  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(HERE), "examples")


def report(path):
    name = os.path.basename(path)
    print("=" * 74)
    print(name)
    print("=" * 74)

    started = time.perf_counter()
    try:
        book = open_book(path)
    except EpubKitError as exc:
        print("  FAILED: %s: %s" % (type(exc).__name__, exc))
        print()
        return None
    elapsed = time.perf_counter() - started

    stats = book.stats()
    print("  %s -- %s (%s)" % (stats["title"] or "?", ", ".join(stats["authors"]) or "?",
                               stats["language"] or "?"))
    print("  parsed in %.2fs   chapters=%d  blocks=%d  tokens=%d  footnotes=%d  pages=%s"
          % (elapsed, stats["chapters"], stats["blocks"], stats["tokens"],
             stats["footnotes"], stats["print_pages"]))
    for warning in stats["warnings"]:
        print("  ! %s" % warning)

    print("  first 8 chapters:")
    for chapter in list(book)[:8]:
        print("    %-5s %-46s %6d tok  %3d blocks  %s"
              % (chapter.anchor, chapter.title[:46], chapter.tokens,
                 len(chapter.blocks),
                 ("vol: " + chapter.group) if chapter.group else ""))

    empty = [c.anchor for c in book if c.tokens == 0]
    if empty:
        print("  ! empty chapters: %s" % empty[:10])

    outline = book.outline()
    import json

    from epubkit import count_tokens

    print("  outline = %d tokens for a %d token book (%.1f%%)"
          % (count_tokens(json.dumps(outline)), book.tokens,
             100.0 * count_tokens(json.dumps(outline)) / max(1, book.tokens)))

    chunks = book.chunks(max_tokens=800)
    over = [c for c in chunks if c.tokens > 820]
    print("  chunks@800 = %d, oversized = %d, all anchors resolve = %s"
          % (len(chunks), len(over),
             all(book.read(c.anchor, max_tokens=1).text for c in chunks)))

    print("  sample markdown (first 300 chars):")
    print("    " + book.to_markdown()[:300].replace("\n", "\n    "))
    print()
    return book


def main():
    paths = sorted(glob.glob(os.path.join(EXAMPLES, "*.epub")))
    if not paths:
        print("No .epub files in %s -- they are not committed." % EXAMPLES)
        print("Fetch them with:  python examples/fetch_examples.py")
        return 1
    for path in paths:
        report(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
