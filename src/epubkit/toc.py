"""Table of contents extraction: EPUB 3 ``nav``, EPUB 2 NCX, and headings.

EPUB 3 books carry a navigation document (``properties="nav"``) whose
``<nav epub:type="toc">`` holds a nested ``<ol>``. EPUB 2 books carry an NCX
with a ``navMap`` of nested ``navPoint``. Plenty of books ship both, and some
ship neither and rely on the headings inside the prose -- so epubkit reads all
three and picks the best one it can find.
"""

from __future__ import annotations

import posixpath
from urllib.parse import unquote

from .model import TocEntry
from .nodes import collapse, epub_type, parse_markup


def split_href(href):
    """``"Text/ch1.xhtml#sec2"`` -> ``("Text/ch1.xhtml", "sec2")``."""
    href = unquote(href or "")
    path, _, fragment = href.partition("#")
    return path, fragment


def resolve_href(base_dir, href):
    path, fragment = split_href(href)
    if not path:
        return "", fragment
    if path.startswith("/"):
        path = posixpath.normpath(path).lstrip("/")
    else:
        path = posixpath.normpath(posixpath.join(base_dir, path))
    return path, fragment


# --------------------------------------------------------------------------- #
# EPUB 3 navigation document
# --------------------------------------------------------------------------- #


def parse_nav(data, base_dir):
    """Return ``(toc_entries, page_list)`` from an EPUB 3 nav document."""
    root = parse_markup(data)
    toc = []
    fallback = []
    pages = []

    for element in root.iter():
        if element.tag != "nav":
            continue
        kind = epub_type(element).lower()
        if "page-list" in kind:
            if not pages:
                pages = _read_flat_nav(element, base_dir)
        elif "landmarks" in kind:
            continue
        elif "toc" in kind:
            if not toc:
                toc = _read_nested_nav(element, base_dir)
        elif not fallback:
            fallback = _read_nested_nav(element, base_dir)

    return (toc or fallback), pages


def _read_nested_nav(container, base_dir):
    list_element = None
    for element in container.iter():
        if element.tag in ("ol", "ul"):
            list_element = element
            break

    out = []
    if list_element is None:
        for element in container.iter():
            if element.tag == "a":
                _append(out, collapse(element.text_content()),
                        element.get("href"), base_dir, 0)
        return out

    _read_list(list_element, base_dir, 0, out)
    return out


def _read_list(list_element, base_dir, depth, out):
    for item in list_element.elements():
        if item.tag != "li":
            continue

        link = None
        nested = None
        heading = None
        for child in item.elements():
            if child.tag == "a" and link is None:
                link = child
            elif child.tag in ("ol", "ul") and nested is None:
                nested = child
            elif heading is None and child.tag not in ("ol", "ul"):
                heading = child

        source = link if link is not None else heading
        if source is not None:
            title = collapse(source.text_content())
            href = source.get("href") or ""
            if title:
                _append(out, title, href, base_dir, depth)

        if nested is not None:
            _read_list(nested, base_dir, depth + 1, out)


def _read_flat_nav(container, base_dir):
    pages = []
    for element in container.iter():
        if element.tag != "a":
            continue
        href = element.get("href") or ""
        if not href:
            continue
        label = collapse(element.text_content())
        if not label:
            continue
        path, fragment = resolve_href(base_dir, href)
        pages.append((path, fragment, label))
    return pages


# --------------------------------------------------------------------------- #
# EPUB 2 NCX
# --------------------------------------------------------------------------- #


def parse_ncx(data, base_dir):
    """Return the navMap of an NCX as a flat, depth-annotated entry list."""
    root = parse_markup(data)
    nav_map = None
    for element in root.iter():
        if element.tag == "navmap":
            nav_map = element
            break
    if nav_map is None:
        return []

    out = []
    for child in nav_map.elements():
        if child.tag == "navpoint":
            _walk_navpoint(child, base_dir, 0, out)
    return out


def _walk_navpoint(nav_point, base_dir, depth, out):
    label = ""
    href = ""
    for child in nav_point.elements():
        if child.tag == "navlabel" and not label:
            label = collapse(child.text_content())
        elif child.tag == "content" and not href:
            href = child.get("src") or ""

    if label:
        _append(out, label, href, base_dir, depth)

    for child in nav_point.elements():
        if child.tag == "navpoint":
            _walk_navpoint(child, base_dir, depth + 1, out)


def _append(out, title, href, base_dir, depth):
    if not title:
        return
    path, fragment = resolve_href(base_dir, href)
    out.append(TocEntry(title, path, fragment, depth, len(out)))


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #


def annotate_ancestors(entries):
    """Fill in each entry's ancestor titles and parent location.

    The flat, depth-annotated entry list is all the hierarchy we kept, and it is
    enough. Chinese books lean on this: ``第一卷 风起`` is a ``<span>`` with no
    href, so it never becomes a chapter, but it is exactly the context a reader
    (or a model) needs to know which volume a chapter belongs to.
    """
    stack = []
    for entry in entries:
        while stack and stack[-1][0] >= entry.depth:
            stack.pop()
        entry.ancestors = [title for _, title, _ in stack]
        entry.parent_location = stack[-1][2] if stack else ("", "")
        stack.append((entry.depth, entry.title, entry.location))
    return entries


def choose_toc(nav_entries, ncx_entries):
    """Prefer whichever source describes more of the book; nav wins ties."""
    if not nav_entries:
        return list(ncx_entries), "ncx"
    if not ncx_entries:
        return list(nav_entries), "nav"
    if len(ncx_entries) > len(nav_entries):
        return list(ncx_entries), "ncx"
    return list(nav_entries), "nav"


def chapter_starts(entries, spine_paths):
    """Pick the TOC entries that begin a chapter.

    Three rules, in order:

    1. Only entries pointing into a spine document are candidates.
    2. Entries at the shallowest depth that carries an href are chapters;
       deeper entries are sections inside them.
    3. Except when a deeper entry points at a *different document* than its
       parent. A separate file is always its own chapter, however the
       navigation document nests it.

    Rule 2 alone would merge every chapter of a volume into the volume; rule 3
    alone would turn every ``ch1.xhtml#sec2`` sub-section into a chapter.
    Together they do the right thing on both.
    """
    spine_set = set(spine_paths)
    candidates = [entry for entry in entries if entry.path and entry.path in spine_set]
    if not candidates:
        return []

    chapter_depth = min(entry.depth for entry in candidates)

    out = []
    seen = set()
    for entry in candidates:
        if entry.depth > chapter_depth:
            parent_path = entry.parent_location[0]
            if parent_path == entry.path:
                continue
        location = entry.location
        if location in seen:
            continue
        seen.add(location)
        out.append(entry)
    return out
