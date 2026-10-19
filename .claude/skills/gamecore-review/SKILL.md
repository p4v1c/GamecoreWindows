---
name: gamecore-review
description: Review a GameCore for Windows diff, branch or the whole repo against the project's own rules (comments, docs, quality, naming, file size, tests, commits) and output a short findings report. Use when asked to review code, audit the repo, check a PR, or before merging.
---

# GameCore review

Output is a **report, straight to the point**: one line per finding.

## Scope

- Diff: `git diff origin/main...HEAD` (or the PR).
- Whole repo: run the automated pass, then sample the biggest / newest files.

## 1. Automated pass

```bash
# Git Bash, or the same commands one by one in PowerShell
ruff check .
.venv/Scripts/python scripts/check-docs.py --all
.venv/Scripts/python scripts/check-catalog.py && .venv/Scripts/python scripts/gen-catalog.py --check
.venv/Scripts/python .claude/skills/gamecore-file-size/scripts/check-file-size.py [--diff]
# French left in code comments / commit subjects
git diff -U0 origin/main | grep -nE '^\+.*(#|//|\*) .*\b(le|la|les|une|pour|est|pas|dans|avec)\b'
git log origin/main..HEAD --format=%s | grep -E '\b(le|la|les|une|pour|est|pas)\b'
# OS calls that escaped the platform layer
git grep -nE "os\.name ?== ?.nt.|sys\.platform|ctypes\.windll|^import win32|^from win32" -- backend ':!backend/services/platform' ':!backend/tests'
```

## 2. Manual checklist (per skill)

| Skill | Question |
|---|---|
| gamecore-code-quality | right layer? one source of truth? no FastAPI in services? no hardcoded roots? OS calls only in `services/platform/`? |
| gamecore-file-size | any file grew past budget? functions > 50 lines? |
| gamecore-naming | names match the tables? events `domain:event`? |
| gamecore-comments | English, why, ≤ 3 lines? no commented-out code? |
| gamecore-docs | every new public name in its doc table? no line counts? |
| gamecore-tests | fix has a failing-first test? network marked? |
| gamecore-commit-pr | English conventional commits? nothing pushed to main? |
| gamecore-human-touch | UI or copy changed: slop-audit counts down? no AI tells added? |
| gamecore-legibility | UI changed: legibility-audit 0 `FAIL` on each touched theme × screen? |
| gamecore-visual-review | UI changed: screenshots of every touched theme × screen, two review passes, findings fixed? |

Also run the generic reviews if available: `/code-review` (bugs),
`/simplify` (reuse), `/ponytail-review` (over-engineering).

## 3. Report format

```
# Review — <scope> — <date>
Verdict: <ship | fix first | rework>

## Blocking
- file:line — problem → fix

## Should fix
- file:line — problem → fix

## Nice to have
- …

## Numbers
- files over budget: N (list)   French comment lines: N   docs check: ok/ko
```

No praise paragraphs, no restating the diff. Every finding has a location and a fix.
