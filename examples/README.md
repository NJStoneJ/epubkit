# Sample books

Not committed to the repository. Run this to fetch them:

```console
python examples/fetch_examples.py
```

They are Project Gutenberg EPUBs, in the public domain in the United States.
Gutenberg's own licence covers its header and branding, which the files retain
— see <https://www.gutenberg.org/policy/license.html>.

Five books, each chosen because it breaks something:

| file | source | what it proves |
| --- | --- | --- |
| `pg11.epub` | [Alice's Adventures in Wonderland](https://www.gutenberg.org/ebooks/11) | small, clean EPUB 3 with a normal nav document |
| `pg1342.epub` | [Pride and Prejudice](https://www.gutenberg.org/ebooks/1342) | 516 NCX navPoints all pointing into 15 files via `#pgepubid` fragments — file-level chaptering would find ~15 chapters instead of 64 |
| `pg2701.epub` | [Moby-Dick](https://www.gutenberg.org/ebooks/2701) | 142 chapters, footnotes, a large but well-formed book |
| `pg23950.epub` | [三國志演義](https://www.gutenberg.org/ebooks/23950) | Chinese, **no headings at all** — exercises the text-based chapter rescue |
| `pg24264.epub` | [紅樓夢](https://www.gutenberg.org/ebooks/24264) | Chinese, single paragraphs of 10,000+ tokens — exercises the block splitter |

`pg23950` and `pg24264` are the interesting ones. Both ship as bare `<p>` soup
with a one-entry NCX: by markup each is a single chapter, and epubkit recovers
110 and 111 chapters respectively by reading the chapter markers out of the
prose.

## Not needed for the test suite

`python -m unittest discover -s tests` builds its own fixtures in
`tests/fixtures/` and passes without any of these files. They are only required
by `tests/benchmark.py` and `tests/real_books.py`.
