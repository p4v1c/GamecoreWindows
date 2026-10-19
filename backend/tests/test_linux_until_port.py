"""The `linux_until_port` marker: a step, a skip on Windows, and a list in docs.

Every test of Linux code the port has not replaced yet carries the step that
replaces it, and docs/dev-log/windows-port-linux-tests.md lists them all. The
list is checked against the source here, so a mark cannot be added, moved or
forgotten without the dev log saying so.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from backend.tests import linux_until_port as lup

REPO = Path(__file__).resolve().parents[2]
LIST = REPO / "docs" / "dev-log" / "windows-port-linux-tests.md"
ROW = re.compile(r"^\| (\d+) \| `([^`]+)` \| (.+?) \| .+ \|$")


def _step_of(node: ast.AST) -> int | None:
    if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "linux_until_port":
        return node.args[0].value
    return None


def _module_step(tree: ast.Module) -> int | None:
    for node in tree.body:
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "id", "") == "linux_until_port_module"):
            return node.value.args[0].value
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "pytestmark"
                                                for t in node.targets):
            marks = node.value.elts if isinstance(node.value, ast.List) else [node.value]
            for mark in marks:
                if _step_of(mark):
                    return _step_of(mark)
    return None


def _marked_in_source() -> set[tuple[int, str, str]]:
    """(step, file, test name or "*" for the whole file)."""
    found = set()
    files = list((REPO / "backend" / "tests").glob("test_*.py"))
    files += list((REPO / "catalog").glob("*/tests/test_*.py"))
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(REPO).as_posix()
        step = _module_step(tree)
        if step:
            found.add((step, rel, "*"))
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for deco in node.decorator_list:
                    if _step_of(deco):
                        found.add((_step_of(deco), rel, node.name))
    return found


def _listed_in_docs() -> set[tuple[int, str, str]]:
    listed = set()
    for line in LIST.read_text(encoding="utf-8").splitlines():
        m = ROW.match(line)
        if not m:
            continue
        step, rel, tests = int(m.group(1)), m.group(2), m.group(3)
        if tests.startswith("all ("):
            listed.add((step, rel, "*"))
        else:
            listed.update((step, rel, name) for name in re.findall(r"`(test_\w+)`", tests))
    return listed


def test_the_dev_log_lists_exactly_the_marked_tests():
    source, docs = _marked_in_source(), _listed_in_docs()
    assert source, "no test is marked: the parser above reads nothing"
    assert source - docs == set(), f"marked but not in {LIST.name}: {sorted(source - docs)}"
    assert docs - source == set(), f"in {LIST.name} but not marked: {sorted(docs - source)}"


@pytest.mark.parametrize("step", [None, 1, 2, 11])
def test_a_step_that_removes_no_linux_code_is_refused(step):
    with pytest.raises(pytest.UsageError):
        lup.reason(step)


def test_the_reason_names_the_step():
    assert lup.reason(3) == "Linux code, replaced at Windows port step 3"


def test_a_module_that_cannot_import_on_windows_is_skipped_with_its_step():
    with pytest.raises(pytest.skip.Exception, match="step 7"):
        lup.linux_until_port_module(7)
