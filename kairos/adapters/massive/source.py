"""Compatibility shim for the former Massive source archive module."""

from __future__ import annotations

from .vendor_archive import (
    ArchivedRequest,
    MassiveFlatFileBatchDownloader,
    MassiveFlatFileClient,
    MassiveSourceArchive,
    MassiveVendorArchiveClient,
    OutsideDownloadWindow,
    request_fingerprint,
)

__all__ = [
    "ArchivedRequest",
    "MassiveFlatFileBatchDownloader",
    "MassiveFlatFileClient",
    "MassiveSourceArchive",
    "MassiveVendorArchiveClient",
    "OutsideDownloadWindow",
    "request_fingerprint",
]
