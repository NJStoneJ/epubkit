"""Manual end-to-end check across every fixture. Prints, does not assert."""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
sys.path.insert(0, os.path.join(HERE, "fixtures"))

import make_fixtures  # noqa: E402  (fixtures dir is on the path)

from epubkit import DrmProtectedError, open_book  # noqa: E402


def show(path):
    name = os.path.basename(path)
    print("=" * 72)
    print(name)
    print("=" * 72)
    try:
        book = open_book(path)
    except DrmProtectedError as exc:
        print("  DRM: %s" % exc)
        return
    stats = book.stats()
    print("  title=%r authors=%s lang=%s" % (stats["title"], stats["authors"], stats["language"]))
    print("  chapters=%d blocks=%d tokens=%d footnotes=%d print_pages=%s"
          % (stats["chapters"], stats["blocks"], stats["tokens"],
             stats["footnotes"], stats["print_pages"]))
    for warning in stats["warnings"]:
        print("  ! %s" % warning)
    for chapter in book:
        print("  %s  %-34s %5d tok  %2d blocks  paths=%d"
              % (chapter.anchor, chapter.title[:34], chapter.tokens,
                 len(chapter.blocks), len(chapter.paths)))
    print("  outline[0] = %s" % (book.outline()[0],))
    print()


def main():
    paths = make_fixtures.build_all()
    for path in paths:
        show(path)

    # a closer look at the two fixtures that matter most
    book = open_book(os.path.join(HERE, "fixtures", "epub3_clean.epub"))
    print("--- epub3_clean: markdown (anchors on) ---")
    print(book.to_markdown(anchors=True)[:1200])
    print()
    print("--- epub3_clean: chunks(max_tokens=40) ---")
    for chunk in book.chunks(max_tokens=40):
        print("  %s..%s  %s | %s  (%d tok)"
              % (chunk.anchor, chunk.anchor_end, chunk.chapter_title,
                 chunk.section, chunk.tokens))
    print()
    print("--- epub3_clean: search('storm') ---")
    for hit in book.search("storm"):
        print("  %s  page=%s  %r" % (hit.anchor, hit.page, hit.text[:70]))
    print()
    print("--- epub3_clean: read('c1', max_tokens=30) ---")
    result = book.read("c1", max_tokens=30)
    print("  anchor=%s end=%s next=%s truncated=%s tokens=%d"
          % (result.anchor, result.anchor_end, result.next_anchor,
             result.truncated, result.tokens))
    print("  %r" % result.text)

    print()
    print("--- cjk_novel ---")
    cjk = open_book(os.path.join(HERE, "fixtures", "cjk_novel.epub"))
    for chapter in cjk:
        print("  %s  group=%-12s %s  (%d paths, %d tok)"
              % (chapter.anchor, chapter.group or "-", chapter.title,
                 len(chapter.paths), chapter.tokens))
    print("  search('青锋') -> %s" % [h.anchor for h in cjk.search("青锋")])
    print("  read('c0') -> %r" % cjk.read("c0").text[:200])
    print("  breadcrumb  -> %s" % cjk.chunks(max_tokens=60)[0].breadcrumb)
    print("  outline[1].sections -> %s" % cjk.outline()[1]["sections"])


if __name__ == "__main__":
    main()
