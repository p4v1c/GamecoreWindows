"""List tracked code files over the GameCore line budget (warn 600, fail 800).

Usage: python check-file-size.py [--diff [base]]   (default base: origin/main)
Exit 1 when any file is over the hard limit. Python rather than bash so it runs
the same way in PowerShell, in Git Bash and on the windows-latest runner.
"""
import re
import subprocess
import sys
from pathlib import Path

WARN = 600
LIMIT = 800
TEST_FACTOR = 1.5
EXEMPT = re.compile(
    r"(^backend/services/gamemedia/(gamemedia|gamescrape)\.py$|^install/generated/"
    r"|catalog_data\.py$|/tests/fixtures/|/seed/|gamecontrollerdb\.txt$"
    r"|package-lock\.json$|^LICENSE$|^CHANGELOG\.md$|^install/(arch|uninstall)\.sh$)"
)
CODE = re.compile(r"\.(py|ts|tsx|js|mjs|sh|ps1|psm1|css)$|^install/bin/|^update/")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          check=True).stdout


def _files(argv: list[str]) -> list[str]:
    if argv[:1] == ["--diff"]:
        base = argv[1] if len(argv) > 1 else "origin/main"
        # R: a file moved AND grown must not escape the budget.
        return _git("diff", "--name-only", "--diff-filter=AMR", f"{base}...HEAD").split()
    return _git("ls-files").splitlines()


def _line_count(path: Path) -> int:
    with path.open("rb") as fh:
        return sum(1 for _ in fh)


def main(argv: list[str]) -> int:
    root = Path(_git("rev-parse", "--show-toplevel").strip())
    report: list[tuple[str, int, str]] = []
    for name in _files(argv):
        path = root / name
        if not path.is_file() or not CODE.search(name) or EXEMPT.search(name):
            continue
        n = _line_count(path)
        factor = TEST_FACTOR if "test" in name else 1
        if n > LIMIT * factor:
            report.append(("FAIL", n, name))
        elif n > WARN * factor:
            report.append(("WARN", n, name))
    for status, n, name in sorted(report, key=lambda r: -r[1]):
        print(f"{status} {n} {name}")
    return 1 if any(r[0] == "FAIL" for r in report) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
