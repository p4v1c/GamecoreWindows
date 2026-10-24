"""hostAccess.gamemode: the player joins the group gamemode's polkit rule trusts."""
from types import SimpleNamespace

import pytest

from backend.services.installer import applier as ap
from backend.services.installer import host_access as host

pytestmark = pytest.mark.linux_until_port(11)


@pytest.fixture
def groups(tmp_path, monkeypatch):
    """A fake group database, and every command recorded instead of run."""
    members = {"gamemode": [], "input": []}
    calls = []
    monkeypatch.setattr(host, "ETC", tmp_path / "etc")
    monkeypatch.setattr(host, "RECEIPT", tmp_path / "state" / "receipt.json")
    monkeypatch.setattr(host.os, "geteuid", lambda: 0)
    monkeypatch.setattr(host.os, "getgrouplist", lambda user, gid: [gid])

    def getgrnam(name):
        if name not in members:
            raise KeyError(name)
        return SimpleNamespace(gr_mem=list(members[name]), gr_gid=900 + len(name))

    def run(*args):
        calls.append(args)
        if args[:2] == ("usermod", "-aG"):
            members[args[2]].append(args[3])
        if args[0] == "gpasswd":
            members[args[3]].remove(args[2])

    import pwd
    monkeypatch.setattr(pwd, "getpwnam", lambda user: SimpleNamespace(pw_gid=1000))
    monkeypatch.setattr(host.grp, "getgrnam", getgrnam)
    monkeypatch.setattr(host, "_run", run)
    return members, calls


def pack():
    return SimpleNamespace(id="lutris", data={"hostAccess": {"gamemode": True}})


def test_the_player_joins_gamemode_once_and_leaves_it_at_uninstall(tmp_path, groups):
    members, calls = groups
    ctx = ap.AppContext(gamecore_path=tmp_path, user="player")
    assert all(r.ok for r in host.apply_host_access(pack(), ctx))
    assert all(r.ok for r in host.apply_host_access(pack(), ctx))
    assert members["gamemode"] == ["player"]
    assert calls.count(("usermod", "-aG", "gamemode", "player")) == 1
    host.restore()
    assert members["gamemode"] == []


def test_a_player_already_in_the_group_is_left_in_it(tmp_path, groups):
    members, calls = groups
    members["gamemode"].append("player")
    host.apply_host_access(pack(), ap.AppContext(gamecore_path=tmp_path, user="player"))
    host.restore()
    assert members["gamemode"] == ["player"]
    assert not any(c[0] in ("usermod", "gpasswd") for c in calls)


def test_a_missing_gamemode_package_is_reported_not_raised(tmp_path, groups):
    members, _calls = groups
    del members["gamemode"]
    results = host.apply_host_access(pack(), ap.AppContext(gamecore_path=tmp_path, user="p"))
    assert len(results) == 1 and not results[0].ok
