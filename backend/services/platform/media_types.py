"""The content types GameCore serves, independent of the Windows registry.

Python's `mimetypes` reads `HKEY_CLASSES_ROOT` on Windows. What it finds there
depends on what the PC has installed: `.webp` is absent on a bare Windows
(covers and pack pictures then go out as `application/octet-stream`), and
some PCs map `.js` to `text/plain`, which a browser refuses for a module
script, leaving the interface blank.
"""
from __future__ import annotations

import mimetypes

# Everything the backend hands the interface: the bundle, fonts, pictures.
SERVED = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".html": "text/html",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}


def pin_media_types() -> None:
    """Register SERVED over whatever the registry said. Idempotent."""
    for suffix, media_type in SERVED.items():
        mimetypes.add_type(media_type, suffix)
