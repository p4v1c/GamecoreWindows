"""GE-Proton: found by tag, verified, unpacked where Lutris lists it."""
import hashlib
import importlib.util
import io
import sys
import tarfile
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

FILES = Path(__file__).resolve().parents[1] / "files"
sys.path.insert(0, str(FILES))
spec = importlib.util.spec_from_file_location("test_lutris_proton", FILES / "lutris_proton.py")
proton = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proton)

TAG = "GE-Proton11-7"


def build(tag=TAG, top=None, with_script=True) -> bytes:
    """A gzip tarball shaped like GE's `make redist` output."""
    top = top or tag
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(name, data=b"", kind=tarfile.REGTYPE, link=""):
            info = tarfile.TarInfo(name)
            info.type, info.size, info.linkname = kind, len(data), link
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data) if data else None)
        add(top, kind=tarfile.DIRTYPE)
        if with_script:
            add(f"{top}/proton", b"#!/usr/bin/env python3\n")
        add(f"{top}/files/bin", kind=tarfile.DIRTYPE)
        add(f"{top}/files/bin/wine", b"ELF")
        add(f"{top}/files/bin/wine64", kind=tarfile.SYMTYPE, link="wine")
    return buf.getvalue()


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@pytest.fixture
def served(monkeypatch):
    """urlopen answering from a dict of url → bytes."""
    table = {}

    def urlopen(url, timeout=None):
        if url not in table:
            raise urllib.error.URLError(f"offline: {url}")
        return Response(table[url])
    monkeypatch.setattr(proton.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(proton.shutil, "disk_usage",
                        lambda p: type("U", (), {"free": proton.MIN_FREE_BYTES * 2})())
    return table


def publish(table, archive: bytes, digest=None):
    digest = digest or hashlib.sha512(archive).hexdigest()
    table[proton.ASSET_URL.format(tag=TAG, ext="tar.gz")] = archive
    table[proton.ASSET_URL.format(tag=TAG, ext="sha512sum")] = \
        f"{digest}  {TAG}.tar.gz\n".encode()


@pytest.mark.parametrize("location, tag", [
    ("https://github.com/GloriousEggroll/proton-ge-custom/releases/tag/GE-Proton11-7", TAG),
    ("/GloriousEggroll/proton-ge-custom/releases/tag/GE-Proton9-27/", "GE-Proton9-27"),
    ("https://github.com/GloriousEggroll/proton-ge-custom/releases", None),
    ("https://evil.example/releases/tag/../../x", None),
])
def test_the_tag_is_read_from_the_latest_redirect(location, tag):
    assert proton.tag_from_location(location) == tag


def test_latest_tag_uses_the_redirect_and_not_the_rate_limited_api(monkeypatch):
    headers = Message()
    headers["Location"] = f"https://github.com/x/releases/tag/{TAG}"

    class Opener:
        def open(self, url, timeout=None):
            raise urllib.error.HTTPError(url, 302, "Found", headers, None)
    monkeypatch.setattr(proton.urllib.request, "build_opener", lambda *h: Opener())
    monkeypatch.setattr(proton.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("the API must not be asked"))
    assert proton.latest_tag(print) == TAG


def test_latest_tag_is_none_offline(monkeypatch):
    class Opener:
        def open(self, url, timeout=None):
            raise urllib.error.URLError("offline")
    monkeypatch.setattr(proton.urllib.request, "build_opener", lambda *h: Opener())
    monkeypatch.setattr(proton.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("x")))
    assert proton.latest_tag(lambda m: None) is None


@pytest.mark.linux_until_port(11)
def test_a_verified_build_lands_in_runners_wine(tmp_path, served):
    publish(served, build())
    wine = tmp_path / "runners" / "wine"
    assert proton.fetch(wine, TAG, print) is True
    assert proton.is_installed(wine, TAG)
    assert (wine / TAG / "files/bin/wine64").is_symlink()
    assert sorted(p.name for p in wine.iterdir()) == [TAG]       # no staging left


def test_a_checksum_mismatch_installs_nothing(tmp_path, served):
    publish(served, build(), digest="0" * 128)
    wine = tmp_path / "wine"
    assert proton.fetch(wine, TAG, print) is False
    assert list(wine.iterdir()) == []


@pytest.mark.parametrize("archive", [build(top="Something-Else"), build(with_script=False)])
def test_an_archive_that_is_not_one_build_is_refused(tmp_path, served, archive):
    publish(served, archive)
    wine = tmp_path / "wine"
    assert proton.fetch(wine, TAG, print) is False
    assert list(wine.iterdir()) == []


def test_no_download_without_room(tmp_path, served, monkeypatch):
    publish(served, build())
    monkeypatch.setattr(proton.shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
    assert proton.fetch(tmp_path / "wine", TAG, print) is False


def test_offline_fetch_fails_cleanly(tmp_path, served):
    assert proton.fetch(tmp_path / "wine", TAG, print) is False


def test_sha512sum_lines_are_matched_by_file_name():
    digest = "a" * 128
    assert proton.parse_sha512(f"{digest}  {TAG}.tar.gz\n", f"{TAG}.tar.gz") == digest
    assert proton.parse_sha512(f"{digest} *{TAG}.tar.gz", f"{TAG}.tar.gz") == digest
    assert proton.parse_sha512(f"{digest}  other.tar.gz", f"{TAG}.tar.gz") is None
    assert proton.parse_sha512("junk", f"{TAG}.tar.gz") is None


def test_prune_keeps_the_newest_and_any_build_a_game_is_pinned_to(tmp_path):
    wine, config = tmp_path / "wine", tmp_path / "config"
    tags = ["GE-Proton9-27", "GE-Proton10-3", "GE-Proton10-25", "GE-Proton11-7"]
    for tag in tags:
        (wine / tag).mkdir(parents=True)
    (config / "games").mkdir(parents=True)
    (config / "games" / "celeste-1.yml").write_text("wine:\n  version: GE-Proton9-27\n")
    removed = proton.prune(wine, tags, 2, config)
    assert removed == ["GE-Proton10-3"]
    assert sorted(p.name for p in wine.iterdir()) == ["GE-Proton10-25", "GE-Proton11-7",
                                                       "GE-Proton9-27"]
