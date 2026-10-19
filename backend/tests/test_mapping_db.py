"""The served mapping database — and the measurement its order rests on.

The whole mapping wizard is worth nothing if the line it captures loses to the
community entry for the same pad. SDL reads a mapping file top to bottom and
does not stop at the first match for a GUID, so which of the two survives is
decided by ORDER alone — and the wrong order fails silently: the file on disk
contains the capture, the emulator ignores it, and the pad behaves exactly as
it did before the owner ran the wizard.

`test_the_last_line_is_the_one_sdl_keeps` is therefore not a unit test of our
code. It is the measurement the code is built on, re-run against every SDL this
box can load, so that an SDL release changing its mind is reported here rather
than discovered on a controller.
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services.configgen import mapping_db                   # noqa: E402

# A GUID no real pad has, so a probe can never be answered from SDL's built-in
# table instead of from the file under test.
PROBE_GUID = "03000000ffff0000ffff000000010000"
PROBE_BODY = ("a:b0,b:b1,x:b2,y:b3,back:b8,start:b9,leftshoulder:b4,"
              "rightshoulder:b5,leftx:a0,lefty:a1,rightx:a2,righty:a3,"
              "platform:Linux,")

USER_LINE = (f"{PROBE_GUID},Wizard Capture,{PROBE_BODY}")
COMMUNITY_LINE = (f"{PROBE_GUID},Community Guess,{PROBE_BODY}")


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Point every path at a throwaway tree.

    Not decoration: DATA_DIR is `~/.local/share/gamecore` on a real box, which
    on THIS machine is the live install's own data directory. A test that
    forgot this would rewrite the owner's controller database.
    """
    monkeypatch.setattr(mapping_db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mapping_db, "USER_DB", tmp_path / "gamecontrollerdb_user.txt")
    monkeypatch.setattr(mapping_db, "SERVED_DB", tmp_path / "gamecontrollerdb.txt")
    vendored = tmp_path / "vendored.txt"
    vendored.write_text(f"# community\n{COMMUNITY_LINE}\n")
    monkeypatch.setattr(mapping_db, "DB_FILE", vendored)
    return tmp_path


# ── the measurement ──────────────────────────────────────────────────────────

def _sdl_libraries() -> list[tuple[str, str]]:
    """(path, api) for every SDL this box can load — the host's and the ones
    the emulators bundle. The bundled ones are the point: they are older, they
    are what actually reads the file at game time, and the host's answer says
    nothing about them."""
    found: list[tuple[str, str]] = []
    for pattern, api in (("/usr/lib/libSDL3.so.0", "3"),
                         ("/usr/lib/libSDL2-2.0.so.0", "2")):
        if Path(pattern).exists():
            found.append((pattern, api))
    for root in glob.glob(str(Path.home() / ".local/share/flatpak/app/*/*/*/*/files")):
        for lib in glob.glob(f"{root}/**/libSDL3.so.0", recursive=True):
            found.append((lib, "3"))
        for lib in glob.glob(f"{root}/**/libSDL2.so", recursive=True):
            found.append((lib, "2"))
    return found


# Run out of process: SDL keeps its mapping table in globals for the life of
# the process, so a second Init in the same interpreter would answer from the
# first probe's file. It also puts a segfault in a bundled library outside the
# test runner, where it is a failed probe instead of a lost suite.
_PROBE_SRC = r"""
import ctypes, os, sys
guid, path, api, lib = sys.argv[1:5]
os.environ["SDL_GAMECONTROLLERCONFIG_FILE"] = path
os.environ["SDL_NO_SIGNAL_HANDLERS"] = "1"
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_JOYSTICK_HIDAPI"] = "0"
class G(ctypes.Structure):
    _fields_ = [("data", ctypes.c_uint8 * 16)]
g = G()
for i, b in enumerate(bytes.fromhex(guid)):
    g.data[i] = b
s = ctypes.CDLL(lib)
if api == "2":
    s.SDL_GameControllerMappingForGUID.restype = ctypes.c_char_p
    s.SDL_GameControllerMappingForGUID.argtypes = [G]
    if s.SDL_Init(0x2000) != 0:
        sys.exit(2)
    out = s.SDL_GameControllerMappingForGUID(g)
else:
    s.SDL_InitSubSystem.restype = ctypes.c_bool
    s.SDL_InitSubSystem.argtypes = [ctypes.c_uint32]
    s.SDL_GetGamepadMappingForGUID.restype = ctypes.c_char_p
    s.SDL_GetGamepadMappingForGUID.argtypes = [G]
    if not s.SDL_InitSubSystem(0x2000):
        sys.exit(2)
    out = s.SDL_GetGamepadMappingForGUID(g)
print(out.decode() if out else "")
"""


def _ask_sdl(lib: str, api: str, path: Path) -> str | None:
    """The name SDL resolves PROBE_GUID to, or None when it could not be
    asked."""
    try:
        r = subprocess.run([sys.executable, "-c", _PROBE_SRC,
                            PROBE_GUID, str(path), api, lib],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    fields = r.stdout.strip().split(",")
    return fields[1] if len(fields) > 1 else None


def test_the_last_line_is_the_one_sdl_keeps(db, tmp_path):
    """The premise the whole file order rests on, re-measured.

    A file naming one GUID twice is handed to every SDL that will load, and
    each must answer with the SECOND name. If any answers "Community Guess",
    the concatenation in mapping_db is upside down for that emulator and the
    owner's capture is dead in it.

    No skip when nothing can be probed: the assertion below this loop tests our
    own ordering and always runs. CI has no SDL and must stay green there — but
    a box that HAS one is the box this measurement is about.
    """
    both = tmp_path / "both.txt"
    both.write_text(f"{COMMUNITY_LINE}\n{USER_LINE}\n")

    libraries = _sdl_libraries()
    verdicts = {}
    for lib, api in libraries:
        answer = _ask_sdl(lib, api, both)
        if answer is not None:
            verdicts[lib] = answer

    disagreed = {lib: name for lib, name in verdicts.items()
                 if name != "Wizard Capture"}
    assert not disagreed, (
        f"an SDL kept the FIRST line for a duplicated GUID: {disagreed}. "
        f"mapping_db appends the owner's captures after the community ones "
        f"because every SDL measured kept the last — this one does not, so a "
        f"captured mapping loses to the community guess in whatever links it.")

    # Guard rail, and the reason this test is not a skip. On CI there is no
    # SDL, `libraries` is empty and the assertion above is vacuous — which is
    # acceptable there and NOT acceptable on a box that has one. A probe that
    # silently answers None for every library would otherwise look identical to
    # a unanimous agreement.
    assert not libraries or verdicts, (
        f"{len(libraries)} SDL librar(ies) were found and not one could be "
        f"asked, so the measurement above proved nothing. The probe itself is "
        f"broken: {[lib for lib, _ in libraries]}")


def test_the_owner_line_is_written_after_the_community_one(db):
    """Our half of the same fact, and the one that can regress by editing this
    repo. It must fail if `rebuild()` ever puts the user block first."""
    mapping_db.upsert(USER_LINE)

    served = mapping_db.SERVED_DB.read_text()
    assert COMMUNITY_LINE in served and USER_LINE in served
    assert served.index(USER_LINE) > served.index(COMMUNITY_LINE), (
        "the owner's capture is written BEFORE the community entry for the "
        "same GUID. Every SDL measured keeps the last line, so this order "
        "hands the pad back to the community guess the wizard was run to "
        "replace.")


# ── the file itself ──────────────────────────────────────────────────────────

def test_a_second_capture_replaces_the_first(db):
    """Re-running the wizard on one pad must not stack a second line. Both
    would resolve the same way today, but a file that grows an entry per
    attempt cannot be read — and remove() would only unbury the older one."""
    mapping_db.upsert(USER_LINE)
    mapping_db.upsert(f"{PROBE_GUID},Second Try,{PROBE_BODY}")

    user = mapping_db.read_user()
    assert len(user) == 1, user
    assert "Second Try" in user[0]


def test_two_platforms_for_one_guid_coexist(db):
    """SDL keys on GUID *and* platform. Replacing by GUID alone would delete a
    Windows entry to store a Linux one — a mapping lost, not updated."""
    mapping_db.upsert(USER_LINE)
    mapping_db.upsert(f"{PROBE_GUID},Windows Pad,"
                      + PROBE_BODY.replace("platform:Linux", "platform:Windows"))

    assert len(mapping_db.read_user()) == 2


def test_the_user_file_survives_a_new_community_database(db):
    """The reason there are two files at all. update/linux.sh rsyncs the
    vendored database over the installed one on every OTA; a capture written
    into it would be gone with the next release."""
    mapping_db.upsert(USER_LINE)
    mapping_db.DB_FILE.write_text("# a newer community database\n")

    mapping_db.rebuild()
    assert USER_LINE in mapping_db.SERVED_DB.read_text()
    assert mapping_db.read_user() == [USER_LINE]


def test_the_served_file_is_rebuilt_when_a_source_moves(db):
    """An OTA replaces the community file underneath a served file that is
    already there. Without the staleness check the box keeps handing SDL a
    concatenation of the PREVIOUS release for ever."""
    mapping_db.upsert(USER_LINE)
    os.utime(mapping_db.SERVED_DB, (1, 1))          # pretend it is old
    mapping_db.DB_FILE.write_text("# newer community\n")

    assert "# newer community" in mapping_db.served().read_text()


def test_a_community_database_that_arrives_OLDER_is_still_noticed(db):
    """The OTA case a timestamp comparison gets wrong.

    `update/linux.sh` installs with `rsync -a`, and `-a` implies `-t`: the new
    vendored database arrives carrying the mtime it had in the release archive,
    which can be older than the merge already on the box. A "newer than me?"
    test answers no to a database that has genuinely just changed, and the box
    serves the previous release's merge for ever — silently, because the file
    is present and looks right.
    """
    mapping_db.upsert(USER_LINE)
    mapping_db.DB_FILE.write_text("# a DIFFERENT community database\n")
    # Backdated a decade, exactly as rsync -a would leave it.
    os.utime(mapping_db.DB_FILE, (1_000_000, 1_000_000))

    served = mapping_db.served().read_text()

    assert "# a DIFFERENT community database" in served, (
        "the replacement was ignored because it is older than the merge — "
        "which is what every OTA looks like, rsync -a preserving mtimes")
    assert USER_LINE in served, "and the owner's capture survived the rebuild"


def test_an_unchanged_box_does_not_rewrite_600kb_on_every_launch(db, monkeypatch):
    """The other half. `served()` runs on every game launch, and a merge that
    rebuilt unconditionally would write the whole community database each
    time — the reason this is a fingerprint and not just "always rebuild"."""
    mapping_db.upsert(USER_LINE)
    rebuilds = []
    real = mapping_db.rebuild
    monkeypatch.setattr(mapping_db, "rebuild",
                        lambda: rebuilds.append(1) or real())

    mapping_db.served()
    mapping_db.served()

    assert rebuilds == [], "nothing changed and it rebuilt anyway"


def test_a_capture_can_be_dropped(db):
    """A wrong capture must be undoable from the couch: the wizard's own
    "forget" (`/controllers/mapping/forget`) is the only way back."""
    mapping_db.upsert(USER_LINE)

    assert mapping_db.remove(PROBE_GUID) is True
    assert mapping_db.read_user() == []
    assert USER_LINE not in mapping_db.SERVED_DB.read_text()
    assert mapping_db.remove(PROBE_GUID) is False, "removing twice is not an error"


def test_a_box_with_no_capture_still_serves_the_community_database(db):
    """The wizard is optional. A box that never runs it must be exactly as well
    off as it was before this module existed."""
    served = mapping_db.served()

    assert served is not None
    assert COMMUNITY_LINE in served.read_text()


def test_junk_is_never_stored_as_a_mapping(db):
    """A truncated or hand-edited line silently shadowing a good entry is the
    failure the parser exists to prevent."""
    for junk in ("", "# a comment", "not-a-guid,Name,a:b0,",
                 f"{PROBE_GUID},,a:b0,", f"{PROBE_GUID},Name,"):
        with pytest.raises(ValueError):
            mapping_db.upsert(junk)

    mapping_db.USER_DB.parent.mkdir(parents=True, exist_ok=True)
    mapping_db.USER_DB.write_text(f"# comment\n\nrubbish\n{USER_LINE}\n")
    assert mapping_db.read_user() == [USER_LINE]


def test_nothing_is_served_when_there_is_nothing_to_serve(db, monkeypatch):
    """An empty file is an ANSWER to SDL — "this database says nothing about
    your pad" — and it would shadow whatever the caller would otherwise have
    fallen back to."""
    monkeypatch.setattr(mapping_db, "DB_FILE", db / "absent.txt")

    assert mapping_db.rebuild() is None
    assert not mapping_db.SERVED_DB.exists()
    assert mapping_db.served() is None


@pytest.mark.linux_until_port(3)
def test_the_launcher_hands_emulators_the_served_file(db, monkeypatch):
    """The end of the chain, and the step that makes the rest matter: a capture
    that never reaches SDL_GAMECONTROLLERCONFIG_FILE is a file on disk nothing
    reads. This used to name the vendored database directly."""
    from backend.services import session

    mapping_db.upsert(USER_LINE)
    monkeypatch.delenv("SDL_GAMECONTROLLERCONFIG_FILE", raising=False)

    env = session._display_env()

    named = env.get("SDL_GAMECONTROLLERCONFIG_FILE")
    assert named == str(mapping_db.SERVED_DB), named
    assert USER_LINE in Path(named).read_text()


def test_the_probe_can_tell_the_two_orders_apart(db, tmp_path):
    """The measurement's own guard rail.

    `_ask_sdl` returning the second name proves nothing unless it would have
    returned the FIRST had the file been written the other way round. Feed it
    the reversed file: an SDL that answers "Wizard Capture" to both is not
    reading the file at all, and the measurement above is theatre.
    """
    reversed_file = tmp_path / "reversed.txt"
    reversed_file.write_text(f"{USER_LINE}\n{COMMUNITY_LINE}\n")

    for lib, api in _sdl_libraries():
        answer = _ask_sdl(lib, api, reversed_file)
        if answer is None:
            continue
        assert answer == "Community Guess", (
            f"{lib} answered {answer!r} for a file whose LAST line is the "
            f"community one. It is not reading the file under test, so the "
            f"precedence measurement means nothing.")


# ── what a capture is allowed to override ────────────────────────────────────
#
# The measured cost of getting this wrong is larger than the wizard's whole
# benefit. `SDL_GAMECONTROLLERCONFIG_FILE` is exported to EVERY game the box
# launches, and the last line for a GUID wins — so a captured line does not sit
# beside SDL's built-in mapping for that pad, it replaces it, in RPCS3, Dolphin,
# Ryujinx, PCSX2 and DuckStation at once.
#
# A capture is read through /dev/input. That is the numbering of the linux
# joystick driver, and it is the pad's real numbering only when SDL reads it the
# same way. For the Sony, Microsoft and Nintendo families SDL uses a HIDAPI
# driver instead, and stamps it into byte 14 of the GUID. Measured, one
# DualShock 4, both probes an instant apart:
#
#     SDL_JOYSTICK_HIDAPI=1   05008fe54c050000cc09000000006800
#     SDL_JOYSTICK_HIDAPI=0   05009b514c050000cc09000000810000

HIDAPI_GUID = "05008fe54c050000cc09000000006800"
HIDAPI_LINE = f"{HIDAPI_GUID},PS4 Controller,{PROBE_BODY}"


def test_a_capture_for_a_hidapi_pad_is_not_served(db):
    """It would replace SDL's own mapping with another driver's button order.

    Measured on the reference box after the wizard ran: SDL answered `start:b6,
    back:b4, leftshoulder:b9` for a DualShock 4 on its own, and `start:b9,
    back:b8, leftshoulder:b4` once the served file reached it. Every emulator
    that asks SDL what the pad is was being told the second.
    """
    mapping_db.upsert(HIDAPI_LINE)
    served = mapping_db.served().read_text()

    assert HIDAPI_LINE not in served
    assert COMMUNITY_LINE in served, "the community database is untouched"


def test_the_owners_file_still_holds_what_they_captured(db):
    """Not served is not deleted. `USER_DB` is the owner's work, it survives
    updates by design, and `remove()` is the one thing that drops a capture."""
    mapping_db.upsert(HIDAPI_LINE)

    assert mapping_db.read_user() == [HIDAPI_LINE]
    assert HIDAPI_LINE in mapping_db.USER_DB.read_text()


def test_a_capture_for_a_pad_sdl_does_not_drive_is_still_served(db):
    """The case the wizard exists for, and the one this must not touch.

    A pad SDL has no HIDAPI driver for is a pad SDL reads through /dev/input —
    the same source the capture came from — so the numbers agree and SDL has no
    mapping of its own to overrule.
    """
    mapping_db.upsert(USER_LINE)
    served = mapping_db.served().read_text()

    assert served.index(USER_LINE) > served.index(COMMUNITY_LINE), (
        "the capture must still be the last word for its GUID")


def test_the_driver_byte_is_read_and_not_guessed():
    """Byte 14 is SDL's driver stamp. A GUID that cannot be read is a no: the
    wrong answer here publishes one driver's button order under another's."""
    from backend.services.configgen import controllers

    assert controllers.guid_read_through_evdev(PROBE_GUID)
    assert not controllers.guid_read_through_evdev(HIDAPI_GUID)
    for junk in ("", "not-a-guid", PROBE_GUID[:-1], "zz" + PROBE_GUID[2:]):
        assert not controllers.guid_read_through_evdev(junk), junk
