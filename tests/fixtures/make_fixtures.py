"""Generate deliberately nasty EPUB fixtures.

Every file this produces is a shape that breaks naive converters in the wild.
If epubkit handles these, it handles most of what is actually on people's
disks.

Run directly to (re)generate the ``.epub`` files next to this script::

    python tests/fixtures/make_fixtures.py
"""

from __future__ import annotations

import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

XHTML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    "<!DOCTYPE html>\n"
    '<html xmlns="http://www.w3.org/1999/xhtml"'
    ' xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en">\n'
    "<head><title>%s</title></head>\n"
    "<body>\n%s\n</body>\n</html>\n"
)

CONTAINER = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<container version="1.0"'
    ' xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
    "  <rootfiles>\n"
    '    <rootfile full-path="%s" media-type="application/oebps-package+xml"/>\n'
    "  </rootfiles>\n"
    "</container>\n"
)


def write_epub(path, members):
    """Write an EPUB. ``mimetype`` must be first and uncompressed."""
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, "application/epub+zip")
        for name, data in members.items():
            if isinstance(data, str):
                data = data.encode("utf-8")
            archive.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)


def make_opf(version, metadata, items, spine, toc_id=None):
    manifest = "\n".join(
        '    <item id="%s" href="%s" media-type="%s"%s/>'
        % (
            item["id"],
            item["href"],
            item["type"],
            ' properties="%s"' % item["props"] if item.get("props") else "",
        )
        for item in items
    )
    spine_xml = "\n".join(
        '    <itemref idref="%s"%s/>'
        % (ref["id"], ' linear="no"' if ref.get("linear") == "no" else "")
        for ref in spine
    )
    spine_attrs = ' toc="%s"' % toc_id if toc_id else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="%s"'
        ' unique-identifier="bookid">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        "%s\n"
        "  </metadata>\n"
        "  <manifest>\n%s\n  </manifest>\n"
        "  <spine%s>\n%s\n  </spine>\n"
        "</package>\n"
        % (version, metadata, manifest, spine_attrs, spine_xml)
    )


def meta_block(title, author, language="en", modified=True):
    lines = [
        '    <dc:identifier id="bookid">urn:uuid:epubkit-fixture</dc:identifier>',
        "    <dc:title>%s</dc:title>" % title,
        "    <dc:creator>%s</dc:creator>" % author,
        "    <dc:language>%s</dc:language>" % language,
    ]
    if modified:
        lines.append(
            '    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>'
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 1. A well-formed EPUB 3: nested nav, footnote aside, page-list
# --------------------------------------------------------------------------- #


def fixture_epub3_clean():
    nav = XHTML % (
        "Contents",
        """<nav epub:type="toc" id="toc">
  <h1>Contents</h1>
  <ol>
    <li><a href="Text/ch1.xhtml">Chapter 1: The Beginning</a>
      <ol><li><a href="Text/ch1.xhtml#sec1">A first section</a></li></ol>
    </li>
    <li><a href="Text/ch2.xhtml">Chapter 2: The Middle</a></li>
    <li><a href="Text/ch3.xhtml">Chapter 3: The End</a></li>
  </ol>
</nav>
<nav epub:type="page-list" hidden="hidden">
  <ol>
    <li><a href="Text/ch1.xhtml#pg1">1</a></li>
    <li><a href="Text/ch1.xhtml#pg2">2</a></li>
    <li><a href="Text/ch2.xhtml#pg3">3</a></li>
  </ol>
</nav>""",
    )

    ch1 = XHTML % (
        "Chapter 1",
        """<h1 id="ch1">Chapter 1: The Beginning</h1>
<p id="pg1">It was a dark and stormy night<a epub:type="noteref" href="#fn1">1</a> and the ship rolled.</p>
<span epub:type="pagebreak" id="pg2" title="2"></span>
<p id="sec1">The second paragraph mentions ALPHA-ONE explicitly.</p>
<aside epub:type="footnote" id="fn1"><p>A note about the storm, recorded by the mate.</p></aside>""",
    )

    ch2 = XHTML % (
        "Chapter 2",
        """<h1>Chapter 2: The Middle</h1>
<p id="pg3">The middle of the book contains BETA-TWO and little else.</p>
<blockquote>A quoted line from somewhere.</blockquote>
<ul><li>first item</li><li>second item</li></ul>""",
    )

    ch3 = XHTML % (
        "Chapter 3",
        """<h1>Chapter 3: The End</h1>
<p>Everything concludes with GAMMA-THREE.</p>
<table><tr><th>Name</th><th>Value</th></tr><tr><td>alpha</td><td>1</td></tr></table>""",
    )

    items = [
        {"id": "nav", "href": "nav.xhtml", "type": "application/xhtml+xml", "props": "nav"},
        {"id": "ch1", "href": "Text/ch1.xhtml", "type": "application/xhtml+xml"},
        {"id": "ch2", "href": "Text/ch2.xhtml", "type": "application/xhtml+xml"},
        {"id": "ch3", "href": "Text/ch3.xhtml", "type": "application/xhtml+xml"},
    ]
    spine = [{"id": "ch1"}, {"id": "ch2"}, {"id": "ch3"}]

    return {
        "META-INF/container.xml": CONTAINER % "OEBPS/content.opf",
        "OEBPS/content.opf": make_opf(
            "3.0", meta_block("The Storm", "A. Author"), items, spine
        ),
        "OEBPS/nav.xhtml": nav,
        "OEBPS/Text/ch1.xhtml": ch1,
        "OEBPS/Text/ch2.xhtml": ch2,
        "OEBPS/Text/ch3.xhtml": ch3,
    }


# --------------------------------------------------------------------------- #
# 2. EPUB 2: NCX only, class-based footnotes
# --------------------------------------------------------------------------- #


def fixture_epub2_ncx():
    ncx = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        '  <head><meta name="dtb:uid" content="urn:uuid:epubkit-fixture"/></head>\n'
        "  <docTitle><text>Old Book</text></docTitle>\n"
        "  <navMap>\n"
        '    <navPoint id="n1" playOrder="1">\n'
        "      <navLabel><text>One: First</text></navLabel>\n"
        '      <content src="Text/c1.xhtml"/>\n'
        "    </navPoint>\n"
        '    <navPoint id="n2" playOrder="2">\n'
        "      <navLabel><text>Two: Second</text></navLabel>\n"
        '      <content src="Text/c2.xhtml"/>\n'
        "    </navPoint>\n"
        '    <navPoint id="n3" playOrder="3">\n'
        "      <navLabel><text>Three: Third</text></navLabel>\n"
        '      <content src="Text/c3.xhtml"/>\n'
        "    </navPoint>\n"
        "  </navMap>\n"
        "</ncx>\n"
    )

    c1 = XHTML % (
        "One",
        """<h2>One: First</h2>
<p>An old-style paragraph<a class="noteref" href="#fn1"><sup>1</sup></a> with NCX-ONE in it.</p>
<div class="footnotes">
  <p id="fn1">Footnote body for the old book.</p>
</div>""",
    )
    c2 = XHTML % ("Two", "<h2>Two: Second</h2>\n<p>Content NCX-TWO.</p>")
    c3 = XHTML % ("Three", "<h2>Three: Third</h2>\n<p>Content NCX-THREE.</p>")

    items = [
        {"id": "ncx", "href": "toc.ncx", "type": "application/x-dtbncx+xml"},
        {"id": "c1", "href": "Text/c1.xhtml", "type": "application/xhtml+xml"},
        {"id": "c2", "href": "Text/c2.xhtml", "type": "application/xhtml+xml"},
        {"id": "c3", "href": "Text/c3.xhtml", "type": "application/xhtml+xml"},
    ]
    spine = [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}]

    return {
        "META-INF/container.xml": CONTAINER % "OEBPS/content.opf",
        "OEBPS/content.opf": make_opf(
            "2.0", meta_block("Old Book", "B. Writer", modified=False),
            items, spine, toc_id="ncx",
        ),
        "OEBPS/toc.ncx": ncx,
        "OEBPS/Text/c1.xhtml": c1,
        "OEBPS/Text/c2.xhtml": c2,
        "OEBPS/Text/c3.xhtml": c3,
    }


# --------------------------------------------------------------------------- #
# 3. One chapter split across four files (routine in Calibre output)
# --------------------------------------------------------------------------- #


def fixture_epub3_split():
    nav = XHTML % (
        "Contents",
        """<nav epub:type="toc" id="toc"><ol>
  <li><a href="Text/ch1.xhtml">Chapter One</a></li>
  <li><a href="Text/ch2a.xhtml">Chapter Two</a></li>
  <li><a href="Text/ch3.xhtml">Chapter Three</a></li>
</ol></nav>""",
    )

    ch1 = XHTML % ("One", "<h1>Chapter One</h1>\n<p>SPLIT-CHAPTER-ONE.</p>")
    parts = []
    for index, letter in enumerate("abcd", start=1):
        body = "<p>SPLIT-PART-%d.</p>" % index
        if index == 1:
            body = "<h1>Chapter Two</h1>\n" + body
        parts.append(XHTML % ("Two part %d" % index, body))
    ch3 = XHTML % ("Three", "<h1>Chapter Three</h1>\n<p>SPLIT-CHAPTER-THREE.</p>")

    items = [
        {"id": "nav", "href": "nav.xhtml", "type": "application/xhtml+xml", "props": "nav"},
        {"id": "ch1", "href": "Text/ch1.xhtml", "type": "application/xhtml+xml"},
        {"id": "ch3", "href": "Text/ch3.xhtml", "type": "application/xhtml+xml"},
    ]
    spine = [{"id": "ch1"}]
    for index, letter in enumerate("abcd", start=1):
        items.append(
            {"id": "ch2%s" % letter, "href": "Text/ch2%s.xhtml" % letter,
             "type": "application/xhtml+xml"}
        )
        spine.append({"id": "ch2%s" % letter})
    spine.append({"id": "ch3"})

    members = {
        "META-INF/container.xml": CONTAINER % "OEBPS/content.opf",
        "OEBPS/content.opf": make_opf(
            "3.0", meta_block("Split Book", "C. Author"), items, spine
        ),
        "OEBPS/nav.xhtml": nav,
        "OEBPS/Text/ch1.xhtml": ch1,
        "OEBPS/Text/ch3.xhtml": ch3,
    }
    for index, letter in enumerate("abcd", start=1):
        members["OEBPS/Text/ch2%s.xhtml" % letter] = parts[index - 1]
    return members


# --------------------------------------------------------------------------- #
# 4. OPF in a subdirectory, hrefs using ../Text/ -- MarkItDown issue #1724
# --------------------------------------------------------------------------- #


def fixture_epub3_relative():
    nav = XHTML % (
        "Contents",
        """<nav epub:type="toc" id="toc"><ol>
  <li><a href="../Text/ch1.xhtml">Relative One</a></li>
  <li><a href="../Text/ch2.xhtml">Relative Two</a></li>
  <li><a href="../Text/ch3.xhtml">Relative Three</a></li>
</ol></nav>""",
    )

    docs = {
        "ch1": XHTML % ("One", "<h1>Relative One</h1>\n<p>RELATIVE-ONE body.</p>"),
        "ch2": XHTML % ("Two", "<h1>Relative Two</h1>\n<p>RELATIVE-TWO body.</p>"),
        "ch3": XHTML % ("Three", "<h1>Relative Three</h1>\n<p>RELATIVE-THREE body.</p>"),
    }

    items = [
        {"id": "nav", "href": "nav.xhtml", "type": "application/xhtml+xml", "props": "nav"},
        {"id": "ch1", "href": "../Text/ch1.xhtml", "type": "application/xhtml+xml"},
        {"id": "ch2", "href": "../Text/ch2.xhtml", "type": "application/xhtml+xml"},
        {"id": "ch3", "href": "../Text/ch3.xhtml", "type": "application/xhtml+xml"},
    ]
    spine = [{"id": "ch1"}, {"id": "ch2"}, {"id": "ch3"}]

    members = {
        "META-INF/container.xml": CONTAINER % "OEBPS/package/content.opf",
        "OEBPS/package/content.opf": make_opf(
            "3.0", meta_block("Relative Book", "D. Author"), items, spine
        ),
        "OEBPS/package/nav.xhtml": nav,
    }
    for name, body in docs.items():
        members["OEBPS/Text/%s.xhtml" % name] = body
    return members


# --------------------------------------------------------------------------- #
# 5. Stub TOC: structure exists only in the headings
# --------------------------------------------------------------------------- #


def fixture_epub3_flat_toc():
    nav = XHTML % (
        "Contents",
        """<nav epub:type="toc" id="toc"><ol>
  <li><a href="Text/d1.xhtml">Contents</a></li>
</ol></nav>""",
    )

    docs = []
    for index in range(1, 4):
        body = "<h1>Flat Chapter %d</h1>\n<p>FLAT-%d body text.</p>" % (index, index)
        body += "\n<h2>A subsection of %d</h2>\n<p>More FLAT-%d detail.</p>" % (index, index)
        docs.append(XHTML % ("Flat %d" % index, body))

    items = [
        {"id": "nav", "href": "nav.xhtml", "type": "application/xhtml+xml", "props": "nav"},
    ]
    spine = []
    for index in range(1, 4):
        items.append(
            {"id": "d%d" % index, "href": "Text/d%d.xhtml" % index,
             "type": "application/xhtml+xml"}
        )
        spine.append({"id": "d%d" % index})

    members = {
        "META-INF/container.xml": CONTAINER % "OEBPS/content.opf",
        "OEBPS/content.opf": make_opf(
            "3.0", meta_block("Flat Book", "E. Author"), items, spine
        ),
        "OEBPS/nav.xhtml": nav,
    }
    for index, doc in enumerate(docs, start=1):
        members["OEBPS/Text/d%d.xhtml" % index] = doc
    return members


# --------------------------------------------------------------------------- #
# 6. Chinese novel: volume headers without hrefs, chapters split, ruby
# --------------------------------------------------------------------------- #


def fixture_cjk_novel():
    nav = XHTML % (
        "目录",
        """<nav epub:type="toc" id="toc"><ol>
  <li><span>第一卷 风起</span>
    <ol>
      <li><a href="Text/w1c1.xhtml">第一章 夜行</a></li>
      <li><a href="Text/w1c2a.xhtml">第二章 山雨</a></li>
    </ol>
  </li>
  <li><span>第二卷 云涌</span>
    <ol><li><a href="Text/w2c1.xhtml">第一章 归途</a></li></ol>
  </li>
</ol></nav>""",
    )

    w1c1 = XHTML % (
        "第一章",
        """<h2>第一章 夜行</h2>
<p>夜里的风很冷，他背着刀走了很久。这段正文里出现关键字「青锋」。</p>
<p>注：<ruby>青锋<rt>qīng fēng</rt></ruby>是那把刀的名字<a epub:type="noteref" href="#n1">1</a>。</p>
<aside epub:type="footnote" id="n1"><p>青锋：古剑名，出自旧籍。</p></aside>""",
    )
    w1c2_parts = []
    for index, letter in enumerate("abc", start=1):
        body = "<p>山雨欲来，第%d段。</p>" % index
        if index == 1:
            body = "<h2>第二章 山雨</h2>\n" + body
        w1c2_parts.append(XHTML % ("第二章 第%d节" % index, body))
    w2c1 = XHTML % ("归途", "<h2>第一章 归途</h2>\n<p>归途漫漫，此卷结束。</p>")

    items = [
        {"id": "nav", "href": "nav.xhtml", "type": "application/xhtml+xml", "props": "nav"},
        {"id": "w1c1", "href": "Text/w1c1.xhtml", "type": "application/xhtml+xml"},
        {"id": "w2c1", "href": "Text/w2c1.xhtml", "type": "application/xhtml+xml"},
    ]
    spine = [{"id": "w1c1"}]
    for index, letter in enumerate("abc", start=1):
        items.append(
            {"id": "w1c2%s" % letter, "href": "Text/w1c2%s.xhtml" % letter,
             "type": "application/xhtml+xml"}
        )
        spine.append({"id": "w1c2%s" % letter})
    spine.append({"id": "w2c1"})

    members = {
        "META-INF/container.xml": CONTAINER % "OEBPS/content.opf",
        "OEBPS/content.opf": make_opf(
            "3.0", meta_block("青锋录", "石某", language="zh-CN"), items, spine
        ),
        "OEBPS/nav.xhtml": nav,
        "OEBPS/Text/w1c1.xhtml": w1c1,
        "OEBPS/Text/w2c1.xhtml": w2c1,
    }
    for index, letter in enumerate("abc", start=1):
        members["OEBPS/Text/w1c2%s.xhtml" % letter] = w1c2_parts[index - 1]
    return members


# --------------------------------------------------------------------------- #
# 7 & 8. Encryption: real DRM versus font obfuscation
# --------------------------------------------------------------------------- #

_ENCRYPTION = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"'
    ' xmlns:enc="http://www.w3.org/2001/04/xmlenc#">\n'
    "%s\n"
    "</encryption>\n"
)


def fixture_drm():
    members = fixture_epub3_clean()
    members["META-INF/encryption.xml"] = _ENCRYPTION % (
        "  <enc:EncryptedData>\n"
        "    <enc:EncryptionMethod"
        ' Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/>\n'
        "    <enc:CipherData>\n"
        '      <enc:CipherReference URI="OEBPS/Text/ch1.xhtml"/>\n'
        "    </enc:CipherData>\n"
        "  </enc:EncryptedData>"
    )
    return members


def fixture_font_obfuscation():
    members = fixture_epub3_clean()
    members["META-INF/encryption.xml"] = _ENCRYPTION % (
        "  <enc:EncryptedData>\n"
        "    <enc:EncryptionMethod"
        ' Algorithm="http://www.idpf.org/2008/embedding"/>\n'
        "    <enc:CipherData>\n"
        '      <enc:CipherReference URI="OEBPS/Fonts/serif.otf"/>\n'
        "    </enc:CipherData>\n"
        "  </enc:EncryptedData>"
    )
    return members


# --------------------------------------------------------------------------- #
# 7. One giant chapter, titles split across elements, no heading markup at all
# --------------------------------------------------------------------------- #

_RULE = "————————————————————————————————-"


def _cjk_body(seed, repeats=260, decoy=False):
    """Hard-wrapped Chinese prose, optionally opening with a false-positive line.

    The decoy is the whole point: ``第四回中既將薛家母子…`` is an ordinary
    sentence that begins with the characters of a chapter marker, and in a
    hard-wrapped source it is exactly as short as a real title line.
    """
    line = "　　話說這一日，城中人來人往，街市熱鬧非常，誰也不知後事如何。"
    lines = []
    if decoy:
        lines.append("第四回中既將薛家母子在榮府內寄居等事略已表明，此回則暫不能寫矣．如今且說")
    for index in range(repeats):
        lines.append("%s%s%04d" % (line, seed, index))
    return "\n".join(lines)


def fixture_epub_split_title():
    """The ``紅樓夢`` shape: no headings, titles split by a rule, one huge file."""
    parts = []
    for number, title, decoy in (
        ("一", "甄士隱夢幻識通靈　賈雨村風塵怀閨秀", False),
        ("二", "賈夫人仙逝揚州城　冷子興演說榮國府", False),
        ("三", "賈雨村夤緣復舊職　林黛玉拋父進京都", True),
    ):
        parts.append("<p>第%s回</p>" % number)
        parts.append("<p>%s</p>" % _RULE)
        parts.append("<p>　　　　　　　　　　%s</p>" % title)
        parts.append("<p>%s</p>" % _cjk_body(number, decoy=decoy))

    body = "\n".join(parts)
    opf = make_opf(
        "3.0",
        meta_block("無標題古本", "佚名", language="zh-CN"),
        [{"id": "txt", "href": "Text/all.xhtml", "type": "application/xhtml+xml"}],
        [{"id": "txt"}],
    )
    return {
        "META-INF/container.xml": CONTAINER % "OEBPS/content.opf",
        "OEBPS/content.opf": opf,
        "OEBPS/Text/all.xhtml": XHTML % ("無標題古本", body),
    }


# --------------------------------------------------------------------------- #
# 9. One paragraph the size of a chapter (the 紅樓夢 13,616-token block)
# --------------------------------------------------------------------------- #


def fixture_giant_paragraph():
    """A single ``<p>`` with no internal breaks at all.

    Text-derived EPUBs really do this: the producer emitted one paragraph per
    *file*, not per paragraph. A reader that treats a block as indivisible then
    hands out a 13k-token "chunk", which is the context bomb epubkit exists to
    prevent -- so this fixture makes the case reproducible in a unit test.
    """
    sentences = [
        "　　卻說這日天色將晚，眾人各自散去，惟有寶玉獨自坐在階下出神，"
        "心中不知想著何事，只聽得院內笑語之聲隱隱傳來。"
    ] * 900
    giant = "".join(sentences)

    body = "\n".join([
        "<h1>第一回 巨段無斷句</h1>",
        "<p>%s</p>" % giant,
        "<h1>第二回 正常段落</h1>",
        "<p>這是一段正常的文字，用來確認切分沒有影響其他段落。</p>",
    ])
    opf = make_opf(
        "3.0",
        meta_block("巨段測試", "佚名", language="zh-CN"),
        [{"id": "txt", "href": "Text/all.xhtml", "type": "application/xhtml+xml"}],
        [{"id": "txt"}],
    )
    return {
        "META-INF/container.xml": CONTAINER % "OEBPS/content.opf",
        "OEBPS/content.opf": opf,
        "OEBPS/Text/all.xhtml": XHTML % ("巨段測試", body),
    }


# --------------------------------------------------------------------------- #

FIXTURES = {
    "epub3_clean.epub": fixture_epub3_clean,
    "epub2_ncx.epub": fixture_epub2_ncx,
    "epub3_split.epub": fixture_epub3_split,
    "epub3_relative.epub": fixture_epub3_relative,
    "epub3_flat_toc.epub": fixture_epub3_flat_toc,
    "cjk_novel.epub": fixture_cjk_novel,
    "epub_split_title.epub": fixture_epub_split_title,
    "giant_paragraph.epub": fixture_giant_paragraph,
    "drm.epub": fixture_drm,
    "font_obfuscation.epub": fixture_font_obfuscation,
}


def build_all(out_dir=HERE):
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for name, factory in FIXTURES.items():
        path = os.path.join(out_dir, name)
        write_epub(path, factory())
        paths.append(path)
    return paths


if __name__ == "__main__":
    for built in build_all():
        print("wrote %s (%d bytes)" % (built, os.path.getsize(built)))
