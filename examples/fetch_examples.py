#!/usr/bin/env python3
"""Download the Project Gutenberg books used by the benchmarks.

The sample books are deliberately **not** committed. They are 3.4 MB of
third-party binaries, and a library repository should not carry them; this
script reproduces them byte for byte from the canonical source instead.

    python examples/fetch_examples.py

`tests/benchmark.py` and `tests/real_books.py` need these files. The unit test
suite does not -- it builds its own fixtures -- so a checkout without them is
still fully testable.

All five books are in the public domain in the United States. Project
Gutenberg's licence applies to its own header and branding, which these files
retain; see https://www.gutenberg.org/policy/license.html.
"""

import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

# (gutenberg id, local filename, what this book is here to prove)
BOOKS = (
    (11, "pg11.epub", "small, clean EPUB 3 with a normal nav document"),
    (1342, "pg1342.epub", "516 NCX navPoints all pointing into 15 files via fragments"),
    (2701, "pg2701.epub", "many chapters, footnotes, a large but well-formed book"),
    (23950, "pg23950.epub", "Chinese, no headings at all -- needs the text rescue"),
    (24264, "pg24264.epub", "Chinese, giant paragraphs -- needs the block splitter"),
)

URL = "https://www.gutenberg.org/cache/epub/%d/pg%d.epub"
USER_AGENT = "epubkit-examples/0.1 (+https://github.com/NJStoneJ/epubkit)"


def fetch(book_id, target):
    request = urllib.request.Request(
        URL % (book_id, book_id), headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read()
    if not payload.startswith(b"PK"):
        raise ValueError("not a zip file -- Gutenberg may have changed the URL")
    with open(target, "wb") as handle:
        handle.write(payload)
    return len(payload)


def main():
    os.makedirs(HERE, exist_ok=True)
    failures = 0
    for book_id, name, why in BOOKS:
        target = os.path.join(HERE, name)
        if os.path.exists(target) and os.path.getsize(target) > 0:
            print("  skip   %-14s %8d bytes  -- already present"
                  % (name, os.path.getsize(target)))
            continue
        try:
            size = fetch(book_id, target)
        except (urllib.error.URLError, ValueError, OSError) as exc:
            print("  FAIL   %-14s %s" % (name, exc))
            failures += 1
            continue
        print("  got    %-14s %8d bytes  -- %s" % (name, size, why))

    print()
    if failures:
        print("%d book(s) could not be downloaded." % failures)
        return 1
    print("Examples ready. Run `python tests/benchmark.py`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
