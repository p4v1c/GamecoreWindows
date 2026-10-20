# Splitting the install scripts — the seams, and why they are not cut yet

`install/arch.sh` is 1 632 lines and `install/uninstall.sh` is 902. Both are far
outside the size the rest of this tree keeps to, and both were looked at as part
of the quality pass that split `gamescrape.py` and `gamemedia.py`.

They were **deliberately left whole**. This file records what the split would
be, so the next person does not re-derive it, and — more importantly — what has
to be true before anyone runs it.

## Why not yet

Three facts, and any one of them is enough:

- **No test covers either file.** The Python splits in the same pass were
  provable: `test_covers.py` and `test_gamemedia.py` counted 73 tests before and
  73 after, to the unit, which is what made "this changed nothing" a statement
  rather than a hope. There is no equivalent here. `shellcheck -S warning` is
  the only automated reader, and it checks for shell mistakes, not for whether
  the install still installs.
- **They run as root.** A sourcing order that is subtly wrong does not raise —
  it writes a unit file, a sudoers rule or an SDDM config to the wrong place,
  as root, on a machine that then reboots into it.
- **A half-refactored install script is worse than a long one.** `arch.sh` warns
  and carries on for a dozen recoverable failures and reports them in its
  closing summary, on purpose: a missing emulator is a degraded box, an aborted
  installer at 66 % is a machine that is neither installed nor clean. That
  property is spread across the whole file and is exactly the kind of thing a
  partial extraction breaks.

**The prerequisite is a disposable VM**, and a fresh one per attempt: the only
honest test of an installer is a machine that has never been installed. Doing it
on a working box proves nothing, because the box already has everything the
script would create. Cutting these files on the machine that *is* the
installation target is the one way to turn a refactor into an outage.

## The seams, if a VM is available

`arch.sh` already announces its own structure — every phase opens with
`msg "<name>"`, and those calls are the cut lines. The three worth taking first
are the three that are self-contained, come late, and can each be re-run on an
already-installed box without doing damage:

| Phase | Lines (approx.) | Why it is a good first cut |
|---|---|---|
| `SDDM auto-login` | 964–1051 | Touches one subsystem and one config tree. Nothing later in the file depends on what it computes. |
| `Caddy reverse-proxy (HTTPS :8443)` | 1052–1103 | Same shape: writes a Caddyfile and enables one unit. `uninstall.sh` already has its own matching Caddy section, so the pair moves together. |
| `Node frontend build` + `Electron shell` | 1176–end | The tail of the script. Long, mechanical, and the only phases that shell out to npm. |

Everything before `msg "System packages"` is preamble — colours, `warn`/`info`,
`progress`, `git_sync`, the manifest recorders, `want_emu`/`want_app` — and it
is what the extracted phases would need. So the split is:

```
install/lib/common.sh      the helpers and the manifest recorders above
install/phases/sddm.sh     one phase per file, sourced in order by arch.sh
install/phases/caddy.sh
install/phases/frontend.sh
install/arch.sh            argument parsing, the phase order, the summary
```

`arch.sh` keeps the phase ORDER and the closing summary, because the order is
the one thing that is genuinely a property of the whole install, and the summary
is what turns a degraded box into a diagnosable one.

## What to check before believing it worked

Not "the script exits 0". On a fresh VM, after a full run:

- the box reaches the grid unaided after a reboot — that is the only end-to-end
  assertion that matters, and it exercises SDDM, the units and Electron at once;
- `install/uninstall.sh` still removes what `arch.sh` created. The two files
  carry a shared, undocumented contract about what was written where, and it is
  the first thing a split of only one of them breaks;
- the closing summary still lists the recoverable failures. Extracting a phase
  into a sourced file makes it easy to lose the `warn`-and-continue behaviour by
  making the phase exit instead — and that failure looks exactly like success.
- **the starting tree reaches a box whose data is outside the install.** The
  first split installs did not: `arch.sh` seeded the player's starting tree
  (the Linux bezels, the bundled themes) into
  GAMECORE_PATH "when absent there", and on a split box `provision_userdata`
  had already created the empty directories under GAMECORE_DATA — so nothing
  was seeded. `seed_data_tree` now targets GAMECORE_DATA and seeds a
  directory that is absent **or empty**; a populated one is the player's.
  `backend/tests/test_installer_seeds_data_tree.py` runs the function out of
  the script (Linux, removed with `arch.sh` at port step 7).

Two smaller things `arch.sh` decides from the same fact (data root ≠ install):
the addons checkout is pre-created in `/opt/gamecore-addons` **only** on the
old layout — on a split box the CLI clones under `$GAMECORE_DATA/addons/_repo`,
mutable code on the data side — and the desktop shortcut's `Icon=` is the
shipped logo (`frontend/src/assets/logo.png`, kept in sync by the OTA), not the
theme's generic gamepad. The graphical installer asks for the data path
(default `/userdata`) and refuses one nested inside the install.

## Also noted, and also left alone

`install/steps/apply-multi-ds4.sh` names two emulators in plain text. It stays
that way. It is a hardware workaround for multiple DualShock 4 pads, those two
are the only ones whose `.ini` files share the SDL-0/Pad1 convention, and a
schema field two packs out of seventeen would use — to serve a patch — costs
more than it earns.

The criterion is not "is it hardcoded", it is **"does it disappear without
saying so"**. That script prints `SKIP — ini not found`. It fails in the open,
which is all that is asked of it.
