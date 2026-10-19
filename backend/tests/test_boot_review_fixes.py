"""Real shell entry points on staged machines; no host tools are called."""
import json
import os
from pathlib import Path
import subprocess

import pytest

REPO = Path(__file__).resolve().parents[2]


def script(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/bash\n" + text)
    path.chmod(0o755)


def environment(tmp_path):
    return {**os.environ, "HOME": str(tmp_path), "GAMECORE_DATA": str(tmp_path),
            "PATH": str(tmp_path / "bin") + ":" + os.environ["PATH"]}


@pytest.mark.linux_until_port(8)
@pytest.mark.parametrize("mode", ["console", "legacy", "desktop"])
def test_ota_restarts_the_owner_of_the_ui(tmp_path, mode):
    calls = tmp_path / "calls"
    manifest = tmp_path / "manifest.env"
    manifest.write_text("USER_NAME=player\n")
    script(tmp_path / "bin/systemctl", f'''
printf '%s\\n' "$*" >> '{calls}'
case "$*" in
  '--user -M player@ is-active --quiet gamecore-session.target') [[ '{mode}' == console ]];;
  '--user -M player@ is-active --quiet gamecore-ui.service') [[ '{mode}' == console ]];;
  'is-active --quiet gamecore-ui.service') [[ '{mode}' == legacy ]];;
  *) exit 0;;
esac
''')
    result = subprocess.run(["bash", str(REPO / "install/bin/gamecore-restart")],
                            env={**environment(tmp_path), "GAMECORE_MANIFEST": str(manifest)},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    lines = calls.read_text().splitlines()
    assert lines[0] == "restart gamecore-backend.service"
    assert ("--user -M player@ restart gamecore-ui.service" in lines) == (mode == "console")
    assert ("restart gamecore-ui.service" in lines) == (mode == "legacy")


@pytest.mark.linux_until_port(8)
@pytest.mark.parametrize("prepared", [False, True])
def test_ota_requires_the_one_time_preparation(tmp_path, prepared):
    bins, units = tmp_path / "bin", tmp_path / "units"
    units.mkdir()
    script(bins / "sudo", """printf '%s\\n' \\
      'player ALL=(root) NOPASSWD: /usr/bin/systemctl start gamecore-session-migrate.service' \\
      'player ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block gamecore-restart.service'
""")
    if prepared:
        for name in ("gamecore-session-migrate", "gamecore-restart", "gamecore-session-select"):
            script(bins / name, "exit 0\n")
        for name in ("gamecore-session-migrate.service", "gamecore-restart.service"):
            (units / name).touch()
    result = subprocess.run(["bash", str(REPO / "update/check-session-prerequisites.sh")],
                            env={**environment(tmp_path), "GAMECORE_SYSTEM_UNITS": str(units),
                                 "GAMECORE_SYSTEM_BINS": str(bins)}, capture_output=True, text=True)
    assert (result.returncode == 0) == prepared
    if not prepared:
        assert "No running code has been replaced" in result.stdout
        assert "setup-gamecore-session.sh" in result.stdout


def test_preflight_precedes_download_and_deployment():
    text = (REPO / "update/linux.sh").read_text()
    assert text.index('bash "$UPDATE_DIR/check-session-prerequisites.sh"') < text.index('curl -sf')


@pytest.mark.linux_until_port(7)
@pytest.mark.parametrize("current,late", [("1920x1080", False), ("1280x720", False), ("1920x1080", True)])
def test_graphical_start_applies_only_the_confirmed_mode(tmp_path, current, late):
    config = tmp_path / "config"
    config.mkdir()
    (config / "display.json").write_text(json.dumps({"width": 1280, "height": 720, "rate": 60}))
    calls, count = tmp_path / "calls", tmp_path / "count"
    outputs = "HDMI-1 connected primary\n   1920x1080 60.00{}\n   1280x720 60.00{}\n".format(
        "*" if current == "1920x1080" else "", "*" if current == "1280x720" else "")
    script(tmp_path / "bin/xrandr", f'''
printf '%s\\n' "$*" >> '{calls}'
if [[ "$*" == --query ]]; then
  if [[ '{late}' == True && ! -e '{count}' ]]; then
    touch '{count}'
    echo 'HDMI-1 disconnected'
  else
    printf '%s' '{outputs}'
  fi
fi
''')
    command = ["bash", str(REPO / "install/bin/gamecore-xsetup")]
    # The old SDDM hook must not change a mode at all.
    subprocess.run(command, env=environment(tmp_path), check=True)
    assert not calls.exists()
    subprocess.run([*command, "--session"], env=environment(tmp_path), check=True, timeout=10)
    lines = calls.read_text().splitlines()
    changes = [line for line in lines if line.startswith("--output")]
    assert changes == ([] if current == "1280x720" else ["--output HDMI-1 --mode 1280x720 --rate 60.0"])
    assert lines.count("--query") == (2 if late else 1)
