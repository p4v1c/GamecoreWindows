# 12 — The addon contract (`api: 1`)

**This is a public contract.** Unlike everything else in this folder, it is
addressed to people outside this repository: it is what a third-party addon
author has to satisfy, and changing it breaks software this project does not
control and cannot fix.

Implementation: `install/bin/gamecore-addon` (the CLI, Python, with
`install/bin/gamecore-addon.cmd` for a prompt), `backend/routers/addons.py`
(the screen), the registry at `<DATA>/config/addons.json`. The addons and the
shared hook steps live in
[p4v1c/gamecore-addons-windows](https://github.com/p4v1c/gamecore-addons-windows).

## The one rule

> **Write under `GAMECORE_DATA`. Never under `GAMECORE_PATH`.**

Everything below is a consequence of it.

`GAMECORE_PATH` is the installation (`C:\Program Files\GameCore`) — code,
venv, frontend build, catalogue. The console user cannot write there, and the
updater swaps it for a new folder on every release. `GAMECORE_DATA` is
everything the player owns, and it is what a backup copies and what the
updater never touches. See [7](07-config-and-data.md) for the two roots
themselves.

An addon that writes into the install root loses its data at the next update.

## Why there is a version at all

Addons written before the split assumed `$GAMECORE_PATH` was writable. Such an
addon fails **at install time, in somebody's living room, with the Addons
screen showing a script's stderr.**

So the CLI refuses it *before running anything*, by name, and prints the porting
instructions. The audience for that message is a player holding a gamepad in
front of a television, not the addon's author — which is why it says what to do
rather than what went wrong.

```json
{
  "id": "save-manager",
  "name": "Save Manager",
  "version": "1.2.0",
  "api": 1
}
```

`GCA_API_VERSION` in the CLI is the version GameCore speaks. A mismatch in either
direction is refused: an addon declaring `2` against a box speaking `1` is just as
broken as one declaring nothing, and failing loudly beats running half of it.

## What a hook receives

`install.ps1` and `uninstall.ps1` are run by Windows PowerShell 5.1
(`powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File`,
`PSModulePath` dropped so a PowerShell 7 caller cannot break module loading),
elevated like the backend, from the addon's directory, with:

| Variable | Meaning |
|---|---|
| `GAMECORE_DATA` | the data root. **Write here.** |
| `ADDON_DATA_DIR` | `<DATA>/addons/<id>/`, **created before the hook runs**. An addon should not have to know the layout to keep one config file |
| `GAMECORE_PATH` | the install root. **Read-only.** Passed so an addon can find shipped code — never so it can write |
| `GAMECORE_ADDON_API` | the version the CLI speaks, so a hook can branch instead of guessing |
| `ADDON_DIR` | the addon's own directory |
| `USER_NAME` | the console user, `DOMAIN\user`: the addon's task runs as them |
| `GAMECORE_ADDON_PYTHON` | install only: the interpreter to build the addon's venv on, a copy in `<DATA>\addons\_python` (see below) |
| `GAMECORE_BACKEND_PORT` | where the core listens |
| `OFFLINE` | set when the install must not reach the network |
| `PAYLOAD_DIR` | files shipped alongside the addon |

`ADDON_DATA_DIR` existing before the hook runs is deliberate: the most common
thing an addon does is keep one small file, and requiring every author to
create a path they had to derive is how they end up deriving it *wrong* —
under the install root, which is the failure this contract exists to prevent.

## What the CLI does around the hooks

| Command | Steps |
|---|---|
| `install <name>` | download the repo if `<DATA>\addons\_repo` is absent (GitHub's zip of `main`; `OFFLINE=1` copies `PAYLOAD_DIR\gamecore-addons`), check `api`, copy Python to `_python` if absent, run `install.ps1`, register |
| `update [name]` | run `uninstall.ps1` of **every** installed addon (they hold the checkout and `_python`), replace `_repo` (kept until the new one is in), refresh `_python` if the core's Python changed, run every `install.ps1` again. A failed download restarts the addons as they were |
| `remove <name>` | run `uninstall.ps1` if the checkout has one, drop the registry entry |

So `uninstall.ps1` must stop the addon and leave its data: `update` runs it
before every reinstall. The data root comes from the environment, else from
`%ProgramData%\GameCore\session.env` (administrators only: run elevated),
else the code root (a checkout). `addon.json` is read with its BOM stripped.

## The gate is on install and update — never on remove

`check_api` is called from `cmd_install` and `cmd_update`. It is deliberately
**not** called from `cmd_remove`.

A box updated to this release already has addons installed by the old CLI, none
of which declares a version. Refusing to *remove* them would strand the player
with something they cannot uninstall from the very screen that installed it —
the update would have taken away the exit. An addon on its way out does not need
to satisfy a contract about how it writes.

## Where addon code lives — the fourth category

The addon checkout is git-managed code, but the player installs and removes it at
runtime. So it can live on neither a read-only root nor the OTA: it is **mutable
code**, which the code/data split had no name for, and it belongs on the data
side.

| | |
|---|---|
| the repo | `<DATA>\addons\_repo` |
| the interpreter addon venvs are built on | `<DATA>\addons\_python` |
| each addon's venv and state | `<DATA>\addons\<id>` |

Addon ids may not start with `_`, so `_repo` and `_python` cannot collide with
an addon's own directory. `_python` exists because the updater stops every
process whose executable is in the code root: an addon on the core's own
interpreter would die at every release.

## Where an addon may listen

Addons run as **scheduled tasks** `\GameCore\Addons\<id>` (at the console
user's logon, elevated, written by the addon's `install.ps1`) and bind their own
loopback ports (`:8770` to `:8773` today; no firewall rule). They are reachable from the LAN only through Caddy, which
authenticates; the core's `/api/*` is 403 through that path. An addon talks back
to the core with `POST /api/addons/notify`.

That endpoint feeds HUD toast text, and **any addon can call it** — so the text
is untrusted. The UI escapes it (`escHtml()`, `safeColor()`). If you are adding a
surface that renders addon-supplied strings, escape there too; see
[9](09-gotchas.md).

## Porting checklist

1. Add `"api": 1` to `addon.json` — but only after the rest.
2. Replace every write under `$GAMECORE_PATH` with `$ADDON_DATA_DIR`.
3. Do not `mkdir` `$ADDON_DATA_DIR`; it is already there.
4. Keep reads of `$GAMECORE_PATH` if you need shipped code — that is allowed.
5. Check `uninstall.ps1` stops what `install.ps1` started, and deletes no data.
