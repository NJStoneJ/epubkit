"""ZIP container, ``container.xml`` and OPF package parsing."""

from __future__ import annotations

import posixpath
import zipfile
from urllib.parse import unquote

from .errors import DrmProtectedError, NotAnEpubError
from .model import BookMeta
from .nodes import collapse, parse_markup

CONTAINER_PATH = "META-INF/container.xml"
ENCRYPTION_PATH = "META-INF/encryption.xml"

_FONT_EXT = (".otf", ".ttf", ".ttc", ".woff", ".woff2", ".eot")
_FONT_ALGORITHMS = (
    "http://www.idpf.org/2008/embedding",
    "http://ns.adobe.com/pdf/enc#RC",
)

_META_TAGS = {
    "title": "title",
    "creator": "authors",
    "language": "language",
    "identifier": "identifier",
    "publisher": "publisher",
    "date": "date",
    "description": "description",
    "subject": "subjects",
    "rights": "rights",
}

_LIST_FIELDS = {"authors", "subjects"}


class ManifestItem:
    """One ``<item>`` from the OPF manifest."""

    __slots__ = ("id", "href", "path", "media_type", "properties")

    def __init__(self, item_id, href, path, media_type, properties):
        self.id = item_id
        self.href = href
        self.path = path
        self.media_type = media_type
        self.properties = properties

    def has(self, prop):
        return prop in self.properties

    def __repr__(self):
        return "ManifestItem(%r, %r)" % (self.id, self.path)


class EpubArchive:
    """Everything you can learn about an EPUB without reading its prose."""

    def __init__(self, source):
        try:
            self.zip = zipfile.ZipFile(source)
        except (zipfile.BadZipFile, OSError) as exc:
            raise NotAnEpubError("not a readable EPUB container: %s" % exc) from exc

        self.source = source
        self.names = set(self.zip.namelist())
        if CONTAINER_PATH not in self.names:
            raise NotAnEpubError(
                "missing META-INF/container.xml -- this is a zip, but not an EPUB"
            )

        self._check_drm()

        self.opf_path = self._find_opf_path()
        self.opf_dir = posixpath.dirname(self.opf_path)
        self.package = parse_markup(self.read(self.opf_path))
        self.meta = self._read_metadata()
        self.manifest = self._read_manifest()
        self.spine, self.spine_toc_id = self._read_spine()

    # -- raw access -------------------------------------------------------- #

    def read(self, path):
        """Read a member by name, falling back to a case-insensitive match.

        Manifest hrefs and actual zip entry names disagree about case often
        enough in the wild that this fallback earns its keep.
        """
        try:
            return self.zip.read(path)
        except KeyError:
            lowered = path.lower()
            for name in self.names:
                if name.lower() == lowered:
                    return self.zip.read(name)
            raise

    def resolve(self, href):
        """Resolve a manifest href against the OPF directory.

        This is the failure MarkItDown issue #1724 documents. Naive
        ``f"{base}/{href}"`` joining silently drops every chapter whose href
        uses a relative segment such as ``../Text/ch1.xhtml`` -- which is
        exactly what a manifest at ``OEBPS/package/content.opf`` produces.
        """
        href = unquote((href or "").split("#", 1)[0])
        if not href:
            return ""
        if href.startswith("/"):
            return posixpath.normpath(href).lstrip("/")
        return posixpath.normpath(posixpath.join(self.opf_dir, href))

    # -- internals --------------------------------------------------------- #

    def _find_opf_path(self):
        root = parse_markup(self.read(CONTAINER_PATH))
        for element in root.iter():
            if element.tag == "rootfile":
                full_path = element.get("full-path")
                if full_path:
                    return posixpath.normpath(unquote(full_path)).lstrip("/")
        raise NotAnEpubError("container.xml declares no rootfile")

    def _check_drm(self):
        if ENCRYPTION_PATH not in self.names:
            return
        root = parse_markup(self.read(ENCRYPTION_PATH))
        blocked = []
        for data in root.iter():
            if data.tag != "encrypteddata":
                continue
            algorithm = ""
            uri = ""
            for child in data.iter():
                if child.tag == "encryptionmethod" and not algorithm:
                    algorithm = child.get("Algorithm") or ""
                elif child.tag == "cipherreference" and not uri:
                    uri = child.get("URI") or ""
            if _is_font_obfuscation(algorithm, uri):
                continue
            blocked.append(uri or algorithm or "unnamed resource")
        if blocked:
            raise DrmProtectedError(
                "this EPUB is DRM protected (%d encrypted resource(s), e.g. %r). "
                "epubkit does not bypass DRM -- font obfuscation is handled, "
                "content encryption is not." % (len(blocked), blocked[0])
            )

    def _read_metadata(self):
        meta = BookMeta()
        for element in self.package.iter():
            tag = element.tag
            if tag == "meta":
                self._read_meta_element(element, meta)
                continue
            field_name = _META_TAGS.get(tag)
            if field_name is None:
                continue
            value = collapse(element.text_content())
            if not value:
                continue
            if field_name in _LIST_FIELDS:
                values = getattr(meta, field_name)
                if value not in values:
                    values.append(value)
            elif not getattr(meta, field_name):
                setattr(meta, field_name, value)

        for element in self.package.iter():
            if element.tag == "package" and not meta.version:
                meta.version = element.get("version") or ""
        return meta

    @staticmethod
    def _read_meta_element(element, meta):
        prop = element.get("property") or ""
        name = (element.get("name") or "").strip().lower()
        content = collapse(element.text_content()) or (element.get("content") or "")

        if prop == "dcterms:modified" and content and not meta.modified:
            meta.modified = content
            return
        if not name or not content:
            return
        if name == "calibre:series" and not meta.series:
            meta.series = content
        elif name == "calibre:series_index" and not meta.series_index:
            meta.series_index = content

    def _read_manifest(self):
        items = {}
        for element in self.package.iter():
            if element.tag != "item":
                continue
            item_id = element.get("id")
            href = element.get("href")
            if not item_id or not href:
                continue
            items[item_id] = ManifestItem(
                item_id,
                href,
                self.resolve(href),
                element.get("media-type") or "",
                (element.get("properties") or "").split(),
            )
        return items

    def _read_spine(self):
        spine = []
        toc_id = ""
        for element in self.package.iter():
            if element.tag == "spine":
                toc_id = element.get("toc") or toc_id
                continue
            if element.tag != "itemref":
                continue
            idref = element.get("idref")
            if not idref:
                continue
            item = self.manifest.get(idref)
            if item is None:
                continue
            linear = (element.get("linear") or "yes").strip().lower() != "no"
            spine.append((item, linear))
        return spine, toc_id

    def close(self):
        self.zip.close()


def _is_font_obfuscation(algorithm, uri):
    """Font obfuscation is not DRM -- it is a fixed XOR every tool reverses."""
    path = (uri or "").lower().split("?")[0]
    if path.endswith(_FONT_EXT):
        return True
    return algorithm in _FONT_ALGORITHMS
