"""Measure epubkit on the real books in examples/ and print a Markdown table.

Run:  python tests/benchmark.py
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from epubkit import count_tokens, open_book  # noqa: E402

EXAMPLES = os.path.join(ROOT, "examples")


def main():
    names = sorted(n for n in os.listdir(EXAMPLES) if n.endswith(".epub"))
    if not names:
        print("No books in examples/ -- they are not committed.", file=sys.stderr)
        print("Fetch them with:  python examples/fetch_examples.py", file=sys.stderr)
        return 1

    rows = []
    print("| book | size | chapters | blocks | tokens | parse | outline | outline % |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")

    for name in names:
        path = os.path.join(EXAMPLES, name)
        started = time.perf_counter()
        book = open_book(path)
        parse = time.perf_counter() - started

        started = time.perf_counter()
        outline = book.outline(depth=2)
        outline_time = time.perf_counter() - started
        outline_tokens = count_tokens(_render(outline))

        chunks = book.chunks(max_tokens=800)
        oversized = [c for c in chunks if c.tokens > 800]
        unresolved = []
        for chunk in chunks:
            try:
                book.resolve(chunk.anchor)
            except Exception:  # noqa: BLE001
                unresolved.append(chunk.anchor)

        rows.append({
            "name": name,
            "title": book.meta.title,
            "bytes": os.path.getsize(path),
            "chapters": len(book.chapters),
            "blocks": sum(len(c.blocks) for c in book.chapters),
            "tokens": book.tokens,
            "parse": parse,
            "outline": outline_time,
            "outline_tokens": outline_tokens,
            "chunks": len(chunks),
            "oversized": len(oversized),
            "unresolved": unresolved,
            "footnotes": book.footnote_count,
            "pages": book.pages,
            "from_toc": sum(1 for c in book.chapters if c.title_from_toc),
            "sources": sorted({s["source"] for n in outline for s in n["sections"]}
                              | {"toc" if any(c.title_from_toc for c in book.chapters)
                                 else "heading"}),
        })

        print("| %s | %.0f KB | %d | %d | %s | %.2fs | %.3fs | %.1f%% |" % (
            book.meta.title or name,
            os.path.getsize(path) / 1024.0,
            len(book.chapters),
            sum(len(c.blocks) for c in book.chapters),
            "{:,}".format(book.tokens),
            parse,
            outline_time,
            100.0 * outline_tokens / max(1, book.tokens),
        ))

    print()
    for row in rows:
        print("%s: %d chapters, chunks@800=%d, oversized=%d, unresolved=%s, "
              "footnotes=%d, print pages=%d, structure from %s" % (
                  row["name"], row["chapters"], row["chunks"], row["oversized"],
                  row["unresolved"] or "none", row["footnotes"], row["pages"],
                  "/".join(row["sources"])))
    return 0


def _render(value):
    import json
    return json.dumps(value, ensure_ascii=False)


if __name__ == "__main__":
    raise SystemExit(main())
