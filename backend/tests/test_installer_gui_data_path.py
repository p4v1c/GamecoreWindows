"""The Linux graphical installer's data-path field (`install/installer-gui`).

The wizard is the Linux one, kept until a Windows graphical installer exists
(dev log, open questions); `install/install.ps1` is the Windows installer and
seeds the data root itself (`test_install_session_steps.py`).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GUI = REPO / "install" / "installer-gui" / "gamecore_installer.py"


def _data_path_problem():
    """`data_path_problem` out of the wizard, without importing Qt."""
    tree = ast.parse(GUI.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "data_path_problem")
    ns: dict = {"re": re}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(GUI), "exec"), ns)
    return ns["data_path_problem"]


@pytest.mark.parametrize("install,data,ok", [
    ("/opt/GameCore", "/userdata", True),
    ("/opt/GameCore", "/opt/GameCore", True),          # the old layout, still allowed
    ("/opt/GameCore", "/mnt/big/gamecore-data", True),
    ("/opt/GameCore", "/opt/GameCore/data", False),    # nested: an update replaces it
    ("/opt/GameCore", "/opt/GameCoreData", True),      # a sibling, not nested
    ("/opt/GameCore", "userdata", False),              # relative
    ("/opt/GameCore", "/user data", False),            # a space in a systemd unit
    ("/opt/GameCore", "/", False),
    ("/opt/GameCore", "/userdata/", False),            # arch.sh's _conf_path refuses it too
])
def test_the_wizard_refuses_a_data_path_arch_sh_could_not_use(install, data, ok):
    problem = _data_path_problem()(install, data)
    assert (problem == "") is ok, problem


def test_the_wizard_is_the_only_writer_of_the_data_root():
    """The wizard is the sole writer of GAMECORE_DATA.

    It used to be conditional: on the live ISO `gamecore-disk-install.sh` wrote
    the key itself, from the partition it had just mounted, and the wizard had
    to stay out of the way so the two could not disagree. With the ISO gone
    there is one installer and one writer, and the key is unconditional — a
    conf that reaches arch.sh without it silently falls back to the install
    directory, which is the old layout and not what the field says.
    """
    text = GUI.read_text(encoding="utf-8")
    assert re.search(r'f"GAMECORE_DATA=\{shlex\.quote\(c\[\'data\'\]\)\}",', text), \
        "the wizard no longer writes GAMECORE_DATA into the conf"
    assert "w.iso_src" not in text, \
        "an ISO branch is back in the wizard — there is only one installer now"
    assert 'self.data = QLineEdit("/userdata")' in text
    assert '"data": sysp.data.text().strip()' in text
