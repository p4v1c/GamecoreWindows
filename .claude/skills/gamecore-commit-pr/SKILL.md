---
name: gamecore-commit-pr
description: Commit, branch and pull-request rules for GameCore for Windows (p4v1c/GamecoreWindows), including the release hazards (every push to main publishes an OTA release; never workflow_dispatch release.yml from a branch; never push to the Linux originals). Use whenever committing, pushing, opening or merging a PR in this repo.
---

# GameCore commits and PRs

## Hazards — read first

- **Every push to `main` publishes a release** that boxes install over the air.
  Never push to `main` directly. Branch.
- **Never run `workflow_dispatch` on `release.yml` from a branch**: the
  publish step has no guard and releases a tag named after the branch — the
  fleet installs it.
- PRs run `ci.yml` on `windows-latest`. Run the checks locally anyway
  (`gamecore-tests`): a red CI is a late answer.
- **Never push to `p4v1c/GamecoreRenew` or `p4v1c/gamecore-addons`.** They
  are the Linux originals, read-only here; the clone's `upstream` remote has
  its push URL disabled on purpose.
- No Claude attribution in commits or PRs (no `Co-Authored-By`, no
  "Generated with"): p4v1c is the only author.

## Branch

`feat/<topic>` · `fix/<topic>` · `refactor/<topic>` · `docs/<topic>` — English, kebab-case.

## Commit message

```
<type>(<area>): <what changed for the player or the dev, present tense>

<why: what broke, how, what the reader would otherwise re-derive.
2-6 lines. English.>
```

- Types: `feat` `fix` `refactor` `docs` `test` `chore` `ci` `revert`.
- Area = the touched part: `orbit`, `session`, `catalog`, `platform`, `ota`…
- **English**, subject ≤ 72 chars, no trailing period.
- One logical change per commit; generated files with their source.
- Good: `fix(bluetooth): named devices first, in the paired list and the scan`
- Bad: `fix(orbit): les réglages d'Orbit reviennent` (French), `fix: stuff`, `wip`.

## PR

Title = the main commit subject. Body:

```
## What
- bullets
## Why
- the failure or the need, one line each
## Checked
- commands run + result (tests, check-docs, gen-catalog --check, CI run, check on the real Windows PC if any)
## Not done
- what is left, if anything
```

Short. No narrative. Link the doc updated.
