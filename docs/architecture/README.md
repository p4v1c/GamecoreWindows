# GameCore — architecture documentation

Reference for anyone (or anything) that has to change this codebase. It is
written to be read **without** opening the source first: every file is named,
every function that matters is listed with what it does, and the flows are
drawn.

`../../README.md` is the user manual. This is the map of the machine.

## Read in this order

| # | Document | What you get |
|---|---|---|
| 1 | [Runtime topology](01-runtime-topology.md) | The four processes, ports, units, boot sequence, who talks to whom |
| 2 | [Request flows](02-request-flows.md) | Sequence diagrams: launch, kill, covers, OTA, standby, auth |
| 3 | [Backend — routers](03-backend-routers.md) | Every HTTP endpoint, every function, file by file |
| 4 | [Backend — services](04-backend-services.md) | Every service module, every function, file by file |
| 5 | [Frontend](05-frontend.md) | Components, hooks, store, the gamepad event bus |
| 6 | [Electron shell](06-electron-shell.md) | Windows, IPC bridge, boot sequence, HUD toasts |
| 7 | [Config & data](07-config-and-data.md) | Every config file schema, the SQLite schema, the caches |
| 8 | [Controller pipeline](08-controller-pipeline.md) | SDL, GUIDs, per-emulator config writers, snapshots, the mapping wizard |
| 9 | [Gotchas](09-gotchas.md) | The invariants that are easy to break, and why they exist |
| 10 | [Catalogue & install](10-catalog-and-install.md) | `catalog/<id>/` — one directory per system or app, and how a box is built from it |
| 11 | [Install-script seams](11-install-script-seams.md) | How `arch.sh` and `uninstall.sh` would be split, and what must be true first |
| 12 | [The addon contract](12-addon-contract.md) | `api: 1` — the **public** contract third-party addons must satisfy |
| 13 | [Release & OTA](13-release-and-ota.md) | What a push to `main` sets in motion, what reaches a box, and how to go back |

Looking for something specific:

- *"Where does a game actually get launched?"* → [2](02-request-flows.md#1-launching-a-game) then [`process_manager`](04-backend-services.md#process_managerpy)
- *"Why is my controller not mapped in melonDS?"* → [8](08-controller-pipeline.md)
- *"What writes `config/addons.json`?"* → [7](07-config-and-data.md#configaddonsjson)
- *"Why did my UI change disappear after an update?"* → [9](09-gotchas.md#the-ota-rebuild-trap)
- *"What events can the UI listen to?"* → [5](05-frontend.md#the-websocket-event-table)
- *"How do I add an emulator or an app?"* → [10](10-catalog-and-install.md#6-adding-an-emulator)
- *"What installs EmberTV / the Firefox kiosk profiles?"* → [10](10-catalog-and-install.md#3-the-install-pipeline)
- *"Why is `arch.sh` one long script, and can I split it?"* → [11](11-install-script-seams.md)
- *"Which session does the box log into?"* → [1](01-runtime-topology.md#the-session-the-kiosk-runs-in)
- *"I write an addon — where am I allowed to write?"* → [12](12-addon-contract.md)
- *"What happens when I merge to `main`, and how do I undo it?"* → [13](13-release-and-ota.md)
- *"Why is `lib/xenia` classified two different ways?"* → [7](07-config-and-data.md#the-two-roots-drawn--including-what-is-still-ambiguous)
- *"How do I run the tests?"* → [`../TESTING.md`](../TESTING.md)
- *"What is exposed to the LAN, and what protects the rest?"* → [`../SECURITY.md`](../SECURITY.md)

Outside this folder: [`../SECURITY.md`](../SECURITY.md) (the threat model and
what enforces it), [`../TESTING.md`](../TESTING.md) (the suite, the `network`
marker, `conftest.py`), [`../CONTROLLER_MODELS.md`](../CONTROLLER_MODELS.md)
(why each emulator is profiled the way it is).

## The system in one picture

```mermaid
flowchart TB
    subgraph box["The box"]
        subgraph be["gamecore-backend.service — system unit"]
            api["FastAPI :8765 (loopback)<br/>REST + WebSocket + serves frontend/dist"]
            tasks["background tasks<br/>gamepad_monitor · battery · standby · prefetch"]
        end
        subgraph ui["gamecore-ui.service — system unit"]
            main["Electron main<br/>kiosk window · HUD toasts"]
        end
        emu["the emulator<br/>own process group"]
        subgraph addons["addons — systemd user units"]
            a1[":8770 rom-manager"]
            a2[":8771 rpcs3-manager"]
            a3[":8772 save-manager"]
            a4[":8773 bios-manager"]
        end
        caddy["Caddy :8443<br/>TLS + shared-password auth"]
    end

    tv["TV / kiosk display"]
    lan["LAN client (phone, PC)"]

    main -->|"http://localhost:8765"| api
    main --> tv
    api -->|spawn, killpg| emu
    api -.->|WebSocket events| main
    lan -->|https| caddy
    caddy -->|forward_auth| api
    caddy --> a1 & a2 & a3 & a4
    a1 & a2 & a3 -->|"POST /api/addons/notify"| api
```

Everything except Caddy binds `127.0.0.1`. The TV reaches the backend over
loopback with no authentication — physical access is the trust boundary. The
LAN only ever sees Caddy. Details in [1](01-runtime-topology.md) and
[9](09-gotchas.md).

## Conventions in this codebase

- **`routers/` parse and validate, `services/` decide and act.** A router that
  grows logic belongs in a service. No FastAPI import exists below
  `services/`.
- **Every path derives from one of the two roots**, and `backend/services/paths.py`
  owns both: `GAMECORE_ROOT` is the installation (code, aiming at read-only),
  `GAMECORE_DATA` is everything the player owns. `backend/config.py` only
  re-exports them. Nothing hardcodes `/opt/GameCore`, and nothing outside
  `paths.py` joins a writable directory onto a root by hand — `test_paths.py`
  fails the build when something does. See [7](07-config-and-data.md).
- **`config/` is the box's identity** — never in git, never touched by OTA.
- **React components have no CSS files** (themes and `frontend/src/settings/` excepted). Styling is inline objects, colocated with
  the component.
- **Gamepad input is an event bus, not props.** `onGp('gp:confirm', fn)`
  anywhere in the tree.

## Keeping this accurate

The function inventories in [3](03-backend-routers.md) and
[4](04-backend-services.md) were generated from the AST and then annotated by
hand. If you add or rename a function, update the table in the same commit —
a stale map is worse than no map.
