# epubkit

**Give an AI agent an addressable view of an EPUB.** Outline it, search it, read one passage at a time by anchor — instead of dumping the whole book into the context window.

```console
$ epubkit outline 紅樓夢.epub
紅樓夢 -- 111 chapters, ~757863 tokens

  c0     Cover
  c1     The Project Gutenberg eBook of 紅樓夢
  c2     第一回 甄士隱夢幻識通靈 賈雨村風塵怀閨秀
  c3     第二回 賈夫人仙逝揚州城 冷子興演說榮國府
  ...
```

```console
$ epubkit read 紅樓夢.epub c6 --max-tokens 200
--- 紅樓夢 > 第五回 游幻境指迷十二釵　飲仙醪曲演紅樓夢
--- c6p1 .. c6p1  ~200 tokens  (truncated)
--- continue with c6p2

第四回中既將薛家母子在榮府內寄居等事略已表明，此回則暫不能寫矣．如 今且說林黛玉自在榮府以來…
```

Zero dependencies. One `pip install`. Works as a Python library, a CLI, and an MCP server.

---

## Why this exists

Ask an agent about a PDF and it calls `pdfplumber` or `pymupdf`. Ask about an EPUB and it either converts the whole book to one wall of text, or it can't see inside at all.

The conversion tools are good at what they do, but they all answer the same question — *"what is the text of this book?"* — and an agent needs a different one: **"what is in this book, and how do I get to the part I need?"**

That difference shows up as cost. A 300k-token novel converted to Markdown is 300k tokens in your context window whether you wanted chapter 4 or the whole thing. epubkit's answer is that a book is a *tree*, every node has a stable address, and the agent reads only what it asked for:

| step | tool | cost on a 758k-token book |
| --- | --- | ---: |
| see the whole book's shape | `epub_outline` | ~5.7k tokens (0.8%) |
| find one passage | `epub_search` | a few hundred |
| read it | `epub_read` | whatever you budget |

## Install

```console
pip install epubkit
```

Not on PyPI yet — install from source until the first release:

```console
git clone https://github.com/NJStoneJ/epubkit && cd epubkit
python -m venv .venv && .venv/bin/pip install -e .
```

**Python 3.10+.** No runtime dependencies — not `lxml`, not `ebooklib`, not
`html2text`. (3.10 rather than 3.9 because `@dataclass(slots=True)` is used for
the block and chunk types, and a large book holds hundreds of thousands of
them.)

## Use it as an MCP server

epubkit speaks the Model Context Protocol over stdio, so any MCP client can use it.

```json
{
  "mcpServers": {
    "epubkit": {
      "command": "epubkit-mcp",
      "args": []
    }
  }
}
```

Seven tools: `epub_info`, `epub_outline`, `epub_read`, `epub_search`, `epub_chunks`, `epub_markdown`, `epub_list`.

The server ships an `instructions` string that teaches the model the intended loop, so it does not have to guess:

> Read a book like this: `epub_info` → `epub_outline` → `epub_search` for a place → `epub_read` from that anchor, following `next_anchor` to continue. Every chapter has an anchor like `c3` and every block one like `c3p12`. Never render the whole book unless the user asked for the whole book.

`next_anchor` carries across chapter boundaries, so a reader that just follows it walks the entire book one budgeted step at a time.

## Use it as a library

```python
from epubkit import open_book

book = open_book("moby-dick.epub")

book.stats()
# {'title': 'Moby Dick; Or, The Whale', 'authors': ['Herman Melville'],
#  'language': 'en', 'chapters': 142, 'blocks': 2652, 'characters': 1203678,
#  'tokens': 301092, 'footnotes': 0, 'print_pages': 0, 'warnings': [...]}

for node in book.outline(depth=1)[:3]:
    print(node["anchor"], node["title"], node["tokens"])
# c0 The Project Gutenberg eBook of Moby Dick; Or, The Whale 89
# c1 MOBY-DICK; or, THE WHALE. 11
# c2 Original Transcriber's Notes: 94

book.search("Call me Ishmael", limit=1)[0].anchor   # 'c5p1'

result = book.read("c5", max_tokens=120)
result.truncated          # True
result.next_anchor        # 'c5p3'  <- keep going from here
result.breadcrumb         # 'Moby Dick; Or, The Whale > CHAPTER 1. Loomings.'
```

Everything an agent touches is anchored:

```python
book.read("c5")            # a whole chapter
book.read("c5p1")          # one block
book.read("CHAPTER 1.")    # by title, if unambiguous
# breadcrumb: 'Moby Dick; Or, The Whale > CHAPTER 1. Loomings.'
```

Chunking for a vector store, with anchors preserved so a retrieved chunk can be cited back to its source:

```python
for chunk in book.chunks(max_tokens=800):
    index(chunk.text, metadata={"anchor": chunk.anchor, "chapter": chunk.chapter_title})
# chunk.anchor -> 'c5p1', chunk.anchor_end -> 'c5p3', chunk.breadcrumb -> 'Moby Dick; ... > CHAPTER 1. Loomings.'
```

## CLI

```console
epubkit info     book.epub                 # metadata and size, no prose
epubkit outline  book.epub --depth 1       # the cheap map
epubkit read     book.epub c3 --max-tokens 800
epubkit search   book.epub "the whale" --limit 5 --all-terms
epubkit chunks   book.epub --max-tokens 800 --jsonl
epubkit md       book.epub --start 2 --end 5 --anchors
epubkit doctor   book.epub                 # what was found, what was guessed
```

`--json` on any subcommand for machine output. `doctor` is the one to reach for when a book parses oddly — it reports what the parser had to work around:

```console
$ epubkit doctor 紅樓夢.epub
紅樓夢
  package       OEBPS/content.opf
  spine         31 documents
  manifest      35 items
  toc           1 entries
  chapters      111
  tokens        ~757863

  what epubkit had to work around:
    - table of contents is a stub (1 usable entry); used headings instead
    - recovered chapter boundaries from the text itself in 1 oversized chapter(s);
      the book's own markup declared no structure there
    - split 163 oversized block(s) at sentence boundaries; the source had no
      paragraph break there
    - chapter structure came from the EPUB 2 NCX
```

## What it handles

Real EPUBs are messy. These are the cases that broke something at some point, and they are all covered by tests:

- **EPUB 2 and EPUB 3** — both NCX (`navMap`/`navPoint`) and the EPUB 3 nav document.
- **Nested TOCs** — volume → chapter → section becomes `group` + `toc_sections`, so breadcrumbs read `青锋录 > 第一卷 风起 > 第一章 夜行`.
- **Chapters split across files** — `ch2a.xhtml` … `ch2d.xhtml` rejoin into one chapter.
- **Chapters split *inside* a file** — the Gutenberg shape. Pride and Prejudice is 15 XHTML files but 516 NCX navPoints, every one of them a `#pgepubid00123` fragment inside one of those files. Cutting only at file boundaries would give ~15 chapters; epubkit cuts at the fragment and gets **64**.
- **Relative paths** — an OPF at `OEBPS/package/content.opf` with hrefs like `../Text/ch1.xhtml`. Resolved with `posixpath`, not string concatenation.
- **No usable TOC** — falls back to headings, splitting at every top-level heading *per document*.
- **No structure at all** — 紅樓夢 ships as bare `<p>` soup with one NCX entry and titles buried in the prose. epubkit finds the chapter markers in the text and cuts there: **3 chapters → 111**.
- **A paragraph the size of a chapter** — one `<p>` holding 10,934 tokens. Split at sentence boundaries into addressable blocks, so no chunk is ever a context bomb.
- **Footnotes** — both `epub:type="noteref"`/`"footnote"` and the older `class="noteref"` + `div.footnotes` convention. Rendered as `[^1]` markers with the body inlined.
- **Ruby annotations** — `青锋(qīng fēng)`, base text preserved.
- **Print page numbers** — from the EPUB 3 `page-list`, attached to blocks as `page`.
- **Tables, images, figures, blockquotes, lists.**
- **DRM detection** — refuses encrypted books with a clear error, but *not* font obfuscation, which is not DRM and must not block a reader.

## Why not just use an existing tool?

Because they solve a different problem, and the difference is architectural rather than a matter of degree.

| | epubkit | MarkItDown | Docling | ebooklib | Pandoc | PyMuPDF |
| --- | --- | --- | --- | --- | --- | --- |
| Output | **addressable tree** | flat text | document tree | object model | flat text | page objects |
| Read one passage | `read("c3p12")` | — | — | manual | — | by page |
| Stable citation anchors | **yes** | no | no | partial | no | page/rect |
| Token-aware budgets | **yes** | no | no | no | no | no |
| EPUB-aware structure | **yes** | partial | partial | yes | yes | n/a |
| Runtime dependencies | **0** | many | many + models | 1 | external binary | compiled |
| License | MIT | MIT | MIT | **AGPL** | GPL | **AGPL** |

The closest comparisons:

- **MarkItDown** converts a document to Markdown. Great generalist, but the output is a string, so there is no way to ask for part of it, and its EPUB path had a [relative-path resolution bug](https://github.com/microsoft/markitdown/issues/1724) that epubkit's `EpubArchive.resolve()` is written to avoid.
- **Docling** produces a rich document tree, but it pulls in ML models and its tree is aimed at layout fidelity, not at being addressed cheaply by a model on a token budget.
- **ebooklib** is a good EPUB object model, but it is AGPL-licensed, has a leaky abstraction over the format, and gives you no opinion about how an agent should read.
- **Pandoc** is the best converter in existence and a poor fit for this: it is an external binary, and it produces text.

None of them are wrong. They are converters. epubkit is a reader.

## Design decisions

**No runtime dependencies.** The obvious implementation uses `lxml`, `ebooklib` and `html2text`. That would also make `uvx epubkit-mcp` fail on a machine without a compiler and hand an AGPL licence to anyone who bundles it. Standard library only means it installs anywhere Python runs, starts instantly, and can be vendored without a licence question. Markup is parsed in three tiers: `ElementTree` → a sanitised retry for DOCTYPEs and named entities → a tolerant `HTMLParser` with implied-end-tag rules. Real books need all three.

**Anchors, not offsets.** `c3p12` means chapter 3, block 12. Offsets break the moment you change the parser; indices into a rebuilt tree are stable across releases and survive a re-parse. A chunk retrieved from a vector store can cite `c3p12` and a human can open the same book and find it.

**Bounded by default.** Every default argument is chosen so that *no single call can dump a book*. `epub_markdown` caps at 4,000 tokens and reports `next_chapter`; `epub_chunks` returns 10 chunks and reports `next_offset`. This is not politeness — the defaults are what a model actually gets, and an unbounded default is a real bug. There are tests that assert these ceilings.

**Token counts are estimated, and say so.** One token per CJK character, one per four characters otherwise — close to BPE for both scripts, and honest about being an estimate. `use_tiktoken()` swaps in exact counts if you have `tiktoken` installed, and the splitter's guarantees hold either way.

**Guarantees, not best effort.** `split_long_text` promises that every piece fits the budget and that `"".join(pieces) == text`. Both are enforced by measuring the assembled result rather than trusting a running sum, and both are tested against adversarial input.

## Benchmarks

Measured on the five Project Gutenberg books in `examples/`, CPython 3.13,
single core (identical figures on 3.11):

| book | size | chapters | blocks | tokens | parse | outline | outline % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Alice's Adventures in Wonderland | 133 KB | 17 | 805 | 35,947 | 0.09s | 0.000s | 1.9% |
| Pride and Prejudice | 545 KB | 64 | 2,223 | 178,898 | 0.18s | 0.000s | 1.4% |
| Moby Dick; Or, The Whale | 710 KB | 142 | 2,652 | 301,092 | 0.18s | 0.000s | 1.8% |
| 三國志演義 | 942 KB | 110 | 3,713 | 597,308 | 0.27s | 0.000s | 0.9% |
| 紅樓夢 | 1,123 KB | 111 | 1,418 | 757,863 | 0.74s | 0.000s | 0.8% |

Reproduce with `python tests/benchmark.py`. On every one of these books: every chunk is within its 800-token budget, every anchor resolves, and no anchor is duplicated.

## Tests

```console
python tests/test_epubkit.py        # 89 tests, standard library only
python tests/benchmark.py           # the table above
python tests/mcp_probe.py           # drives the MCP server over a pipe
python tests/packaging_check.py     # entry points and metadata
python tests/smoke.py               # walkthrough of every fixture
python tests/real_books.py          # parses everything in examples/
```

The packaging check is a fast, network-free pre-flight. It cannot substitute
for a real install, so do that too before a release:

```console
python -m venv _build_venv
_build_venv/Scripts/python -m pip install -e .
_build_venv/Scripts/epubkit.exe --version
_build_venv/Scripts/epubkit-mcp.exe --help
```

Both console scripts, the installed metadata, and the MCP handshake have been
verified this way against the built wheel — `epubkit-mcp` driven over a real
subprocess pipe passes every protocol invariant.

The fixtures are generated, not committed as binaries, and each one exists because it broke something:

`epub3_clean` (nav + `page-list` + `aside` footnotes) · `epub2_ncx` (class-based noterefs) · `epub3_split` (chapter across four files) · `epub3_relative` (OPF in a subdirectory) · `epub3_flat_toc` (one-entry stub TOC) · `cjk_novel` (volume hierarchy + ruby) · `epub_split_title` (titles split by rule lines, **plus a decoy prose line that begins like a chapter marker**) · `giant_paragraph` (a 47k-token single `<p>`) · `drm` · `font_obfuscation`.

## What it does not do

- **No DRM removal.** Encrypted books raise `DrmProtectedError`. Font obfuscation is not treated as DRM.
- **No rendering.** It does not care what a page looks like; it cares where the text is.
- **No images extracted.** Image references are reported as blocks, not written to disk.
- **No audio, no video, no fixed-layout rendering.**
- **Not a converter.** `epubkit md` exists, but if converting is your goal, use Pandoc.

## License

MIT.
