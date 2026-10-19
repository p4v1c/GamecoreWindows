"""The scraper keys reach the backend exactly as the owner typed them.

`install/arch.sh` writes them into a systemd drop-in. systemd reads
`Environment=` with its own rules: a space ends an unquoted value, `\\` and `"`
are escapes, and `%h`, `%u`... are expanded. A ScreenScraper password holding
"%h" reached the backend as the home directory, and one holding a quote lost
its line. `sd_env` quotes and escapes; this runs it in bash and reads its
output back with systemd's rules (checked against systemd itself on Arch).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.linux_until_port(7)

ARCH = Path(__file__).resolve().parents[2] / "install" / "arch.sh"


def _sd_env(name: str, value: str) -> str:
    fn = re.search(r"^sd_env\(\) \{.*?^\}\n", ARCH.read_text(encoding="utf-8"), re.M | re.S).group(0)
    out = subprocess.run(["bash", "-c", fn + 'sd_env "$1" "$2"', "_", name, value],
                         capture_output=True, text=True, check=True)
    return out.stdout.rstrip("\n")


def _systemd_reads(line: str) -> tuple[str, str]:
    """`Environment="NAME=value"` unescaped the way systemd does it."""
    m = re.fullmatch(r'Environment="(.*)"', line)
    assert m, line
    raw, out, i = m.group(1), [], 0
    while i < len(raw):
        c = raw[i]
        if c == "\\":
            out.append(raw[i + 1]); i += 2
        elif c == "%":
            assert raw[i + 1] == "%", f"unescaped specifier in {line}"
            out.append("%"); i += 2
        else:
            assert c != '"', f"bare quote in {line}"
            out.append(c); i += 1
    name, _, value = "".join(out).partition("=")
    return name, value


@pytest.mark.parametrize("value", [
    "plain", "two  spaces", "pa%hss", "back\\slash", 'q"uote', "$HOME `id`", 'mix%%"\\ end',
])
def test_a_secret_survives_the_drop_in(value):
    assert _systemd_reads(_sd_env("SCREENSCRAPER_PASSWORD", value)) == ("SCREENSCRAPER_PASSWORD", value)
