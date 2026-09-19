"""Public entry points."""

from __future__ import annotations

import os

from .chapterize import build_book
from .container import EpubArchive
from .errors import NotAnEpubError

__all__ = ["open_book", "inspect"]


def open_book(path):
    """Parse an EPUB into an addressable :class:`~epubkit.model.Book`.

    Raises :class:`~epubkit.errors.NotAnEpubError` for anything that is not a
    readable EPUB and :class:`~epubkit.errors.DrmProtectedError` for encrypted
    content.
    """
    if not os.path.isfile(path):
        raise NotAnEpubError("no such file: %s" % path)
    return build_book(path)


def inspect(path):
    """Container-level facts, without extracting any prose.

    Useful as a cheap first look: how many spine documents, what the manifest
    says, whether the packaging itself is sound.
    """
    archive = EpubArchive(path)
    try:
        return {
            "source": str(path),
            "opf_path": archive.opf_path,
            "opf_dir": archive.opf_dir,
            "meta": archive.meta.to_dict(),
            "spine": [
                {"id": item.id, "path": item.path, "linear": linear}
                for item, linear in archive.spine
            ],
            "manifest": [
                {
                    "id": item.id,
                    "path": item.path,
                    "media_type": item.media_type,
                    "properties": list(item.properties),
                }
                for item in archive.manifest.values()
            ],
            "member_count": len(archive.names),
        }
    finally:
        archive.close()
