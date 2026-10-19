"""What the backend serves is typed by GameCore, not by the PC's registry.

On a Windows runner `.webp` came out as `application/octet-stream`: Python's
`mimetypes` reads the registry, and a bare Windows has no entry for it.
"""
from __future__ import annotations

import mimetypes

import pytest

from backend.services.platform import media_types


@pytest.fixture
def broken_registry():
    """A PC whose registry says what some real ones do."""
    mimetypes.add_type("text/plain", ".js")
    mimetypes.add_type("application/octet-stream", ".webp")
    yield
    mimetypes.init()
    media_types.pin_media_types()


def test_a_wrong_registry_entry_is_overridden(broken_registry):
    media_types.pin_media_types()
    assert mimetypes.guess_type("index-abc.js")[0] == "text/javascript"
    assert mimetypes.guess_type("console.webp")[0] == "image/webp"


@pytest.mark.parametrize("suffix,media_type", sorted(media_types.SERVED.items()))
def test_every_served_suffix_has_its_type_once_the_app_is_imported(suffix, media_type):
    import backend.main  # noqa: F401  (pins at import)
    assert mimetypes.guess_type(f"file{suffix}")[0] == media_type


def test_a_pack_picture_goes_out_as_webp(tmp_path):
    from fastapi.responses import FileResponse

    picture = tmp_path / "console.webp"
    picture.write_bytes(b"RIFF\x00\x00\x00\x00WEBP")
    media_types.pin_media_types()
    assert FileResponse(picture).media_type == "image/webp"
