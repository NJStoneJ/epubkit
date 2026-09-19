"""Exception types raised by epubkit."""

from __future__ import annotations


class EpubKitError(Exception):
    """Base class for every error epubkit raises."""


class NotAnEpubError(EpubKitError):
    """The file is not a readable EPUB container."""


class DrmProtectedError(EpubKitError):
    """The EPUB is encrypted and epubkit will not bypass that.

    epubkit deliberately stops here. Font obfuscation (which every EPUB with
    embedded fonts uses, and which is trivially reversible) is *not* treated as
    DRM; only genuine content encryption is.
    """


class AnchorError(EpubKitError):
    """The supplied anchor does not point at anything in this book."""


class TocError(EpubKitError):
    """The book has no usable table of contents and none could be inferred."""
