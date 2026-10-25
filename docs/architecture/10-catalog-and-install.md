# 10 — The catalogue, and how a box gets installed

The other nine documents describe a box that is already running. This one
describes where its contents come from.

One rule holds the whole thing together:

> **One directory is one system or one application.** Everything that system
> needs — how to obtain it, how to launch it, its curated config, its logo, its
> controller bindings, its service, its post-install steps — lives in
> `catalog/<id>/`. Adding one is dropping a directory. Removing one is `rm -rf`.

Everything below is a consequence of that rule, including the parts that took a
broken install to get right.

A pack is not an install-time artefact that dies once the box is provisioned.
It is read at **four moments**, by four different readers:

1. **Build time** — `scripts/gen-catalog.py` runs in the repository (and in CI)
   and derives `install/generated/*.dist` from the packs: the tiles a fresh
   install starts from (§4).
2. **Install time** — `arch.sh` and the installer providers read the catalogue
   through `scripts/catalog-query.py` for everything they do: which Flatpaks to
   install, which ROM directories to create, which sandbox flags to grant,
   where each `seed/` lands, which services to enable (§3, §8).
3. **Update time** — the OTA ships the whole `catalog/` tree and
   `merge_file()` uses it to add tiles the box does not have yet — only once
   their emulator is actually installed (`pack_present()`: the Flatpak, the
   pacman packages, the downloaded binary or a `preferIfPresent` native build);
   `gamecore-emu install <id>` adds the tile the moment it is — and to fill in
   fields that did not exist when the box was installed — a box updated to
   v1.2.15 gained `roms.consoles` ratios on its existing mGBA tile this way,
   without its operator touching anything
   ([13-release-and-ota.md](13-release-and-ota.md)).
4. **Runtime** — the backend reads the pack tree on the box on every boot and
   every launch: `configgen` imports each pack's `generator.py`, `bios.py`
   answers from the `bios` block, `pergame` and `local_media` all read their
   blocks live (§2).

So editing a pack is never "too late": the change reaches installed boxes at
the next update, through moment 3.

---

## 1. What a pack looks like on disk

```
catalog/twitch/
├── pack.json                     the declaration — the only required file
├── logo.png                      the tile (logo.svg also works)
├── files/                        what `files` and `services` refer to
│   ├── embertv.service
│   ├── embertv-config.json.tmpl
│   ├── embertv-config.demo.json
│   └── twitch-tv.user.js
└── steps/                        what `postInstall` refers to
    ├── make-cert.sh
    └── trust-cert.sh

catalog/mgba/
├── pack.json
├── logo.png
├── art/                          pictures for themes, by name: console.webp is the
│   ├── console.webp              hardware photo. Served as `art` on GET /systems;
│   └── SOURCE.md                 the operator's assets/art/<id>/ wins. Credits beside it
├── seed/                         curated config, copied to the emulator's config dir
│   ├── config.ini
│   └── qt.ini
├── generator.py                  writes controller bindings for this emulator
└── tests/                        this pack's own tests, run by CI with the rest
```

`seed/`, `logo.*`, `generator.py` and `tests/` are **implicit by presence**: they
are on disk or they are not, and no field in `pack.json` declares them. `files/`
and `steps/` are the opposite — nothing is copied or run unless a block names it.

---

## 2. Every block, and who reads it

`pack.json` is validated against [`catalog/_schema/pack.schema.json`](../../catalog/_schema/pack.schema.json)
by `scripts/check-catalog.py`, which CI runs before anything else. Required:
`id`, `kind`, `label`, `platform`, `color`, `launch`.

| Block | Read by | What it does |
|---|---|---|
| `id` `kind` `label` `platform` `color` | everything | identity. `kind` is `emulator` or `app` |
| `emulatorName` `family` `description` | the grid, the install wizard | display only |
| `windows` | `update/finish.py` (run by `install/steps/create-data-root.ps1` and after each update) | `true` = rewritten for Windows: only these packs get a tile on a Windows grid (`config/apps.json` is created on a fresh install). The Linux packs, still in the tree as test data, have none. First one: `catalog/steam` |
| `order` | `gen-catalog.py` | where the tile sits in the grid and in the wizard's list. A curated running order, not alphabetical. **Absent means last**, never absent — that ordering used to be a list of ids inside the script, and a pack missing from it was silently dropped from both |
| `launch` | the backend, `flatpakify-systems.sh` | the command the tile runs. `preferIfPresent` picks a native binary over the Flatpak when one exists. `fullscreen` and `gamepadTrigger` cover what happens just after — see below |
| `roms` | the backend, `arch.sh` | ROM directory and extensions; `showExtension: false` sends an empty `ext` for entries whose extension means nothing to a player (`.lutris` stubs, `services/pack_library.py`). `roms.consoles` declares the DISTINCT MACHINES one emulator runs (mGBA: Game Boy, Color, Advance) with per-console extensions and an optional `ratio` (what the machine draws, `3:2`) — it feeds the per-console bezel cascade, the drift-correction cache keys and the overlay slots' expected ratio; see [06-electron-and-overlays](06-electron-shell.md). Declared, never derived: `.zip` says nothing and `.rvz` holds two consoles |
| `config` | `install-emu-configs.sh` | where `seed/` is deployed. A seed may carry `@HOME@`, `@GAMECORE_PATH@` and `@GAMECORE_DATA@`, replaced at deploy time (`backend/services/configgen/seed.py`) |
| `controllers` | `backend/services/configgen/` | which binding strategy `generator.py` implements |
| `scraper` `overlay` | covers; `overlay` is read by nothing in the Windows edition (the Linux bezel window left the port) and stays only for the Linux packs | metadata. `scraper.steamStore: true` (PC packs only) adds the minimum spec from the Steam store, see [gamemedia](04-backend-services.md#gamemedia--screenscraper--launchbox) |
| `bios` | `backend/services/bios.py` | which system files the OWNER must supply, so the UI can answer "absent / wrong md5 / conforming" instead of a black screen |
| `perGame` | `backend/services/pergame.py` | whether per-game settings are supported, and the strategy |
| `profileSaves` | `backend/services/profile_saves.py` | how each profile's saves are kept apart; absent = shared by every profile ([below](#profilesaves--saves-per-profile)) |
| `localMedia` | `backend/services/local_media.py` | how covers/titles are read out of the dumps themselves (PARAM.SFO, disc headers) |
| `usb` | the tile, `services/launch.py` | non-gamepad accessories a launch should check for, and what to say when absent |
| `install` | `installer/providers.py` | how the **main artifact** is obtained |
| `sharesEmulator` | `scripts/check-catalog.py`, `catalog.selected()`, `gamecore-emu` | this system runs on another pack's emulator — see [One emulator, several systems](#one-emulator-several-systems) |
| `supersededBy` | `gen-catalog.py`, `merge.py`, `catalog.selected()` | this pack was split into these; kept only so an unmigrated tile still launches |
| `sandbox` | `installer/providers.py` | Flatpak override flags. Absent = the emulator default |
| `packages` | `installer/applier.py` | extra system dependencies, *not* the main artifact |
| `hostAccess` | `installer/host_access.py` | fixed host prerequisites, as root, undone by the uninstaller from a receipt: `uinput`, `ptrace` (melonDS, Azahar layout daemons), `gamemode` (the player joins the `gamemode` group, the only one gamemode's polkit rule lets change the CPU governor) |
| `sources` | `installer/applier.py` | git checkouts the app needs beside it |
| `secrets` | the install wizard, `applier.py` | keys to prompt for, and to expand in templates |
| `files` | `installer/applier.py` | files to write, verbatim or from a template |
| `services` | `installer/applier.py` | systemd **user** units |
| `postInstall` | `installer/applier.py` | ordered scripts, as the user, bounded, never fatal |

### `install` — the four providers

| `provider` | Fields | Used by |
|---|---|---|
| `flatpak` | `appIds` | every Flathub emulator, Steam, Stremio |
| `github-asset` | `repo`, `asset`, `dest`, `magic`, `version?`, `sha256?` | DuckStation (AppImage) |
| `github-archive` | `repo`, `asset`, `dest`, `entrypoint`, `requires` | Xenia (Windows zip under Wine) |
| `pacman` | `packages` | a pack that is just a distribution package |

`appIds` is an ordered list, not a string, because an upstream can vanish
overnight — Ryujinx original left Flathub with no warning. The installer takes
the first candidate the remote still offers; everything else on the box (the
`@FLATPAK_CONFIG@` and `@FLATPAK_DATA@` expansions, `verify`, the launcher)
takes the first one actually **installed**, so a box that fell back keeps its
config, its saves and its BIOS under one app id rather than three.

Anything already installed wins over what the remote prefers. Re-running the
installer on a box that fell back months ago must not drag it forward when the
primary returns: `~/.var/app/<the id it installed>/` holds the memory cards.

A launcher therefore writes `run @APPID@ …` and never an app id — `launch.args`
naming one is refused by `scripts/check-catalog.py`. A tile is written once, by
an installer or an OTA merge, and `config/` is excluded from the OTA rsync; an
id baked into it is the one thing that cannot be corrected later. The token is
resolved at launch, against what is installed. See `catalog/_ota/README.md` for
the channel that corrects a dead id across the fleet without a release.

The download path carries protections that each cost a broken install to learn:
the fixed `/releases/latest/download/` URL **before** the rate-limited API (60
requests/hour/IP, and exhausting it is why fresh installs ended up with no
PlayStation emulator), a `.part` temp file so an aborted transfer is never read
as "already installed", magic-byte checking because a 200 carrying an HTML error
page is still a failed download, and an optional `sha256`.

### One emulator, several systems

A system is a pack even when its emulator is not its own: GameCube and Wii both
run on Dolphin, Game Boy, Color and Advance on mGBA. One pack **owns** the
emulator (`gamecube`, `gba`): `install`, `config`, `seed/`, `controllers`,
`generator.py`. The others declare `"sharesEmulator": "<owner>"` with the same
`install` and `launch` and none of the owner's blocks, because those write the
emulator's own files and two packs writing them overwrite each other.
`scripts/check-catalog.py` allows a shared Flatpak app id only along that link.

| Rule | Where |
|---|---|
| ticking `wii` alone also selects `gamecube` — otherwise Dolphin is installed with no seed | `catalog.selected()`, used by `gamecore-provider.py` and `catalog-query.py` |
| `gamecore-emu install wii` deploys the owner's seed and bindings | `apply_pack` in `install/bin/gamecore-emu` |
| `configgen` profiles the owner once; the others have no `controllers` block | `configgen.profilable_packs()` |
| a superseded pack is profiled only while its tile is on the grid: once its games moved, its emulator may be uninstalled, and asking it failed on every pad (8 s launch wait, "not configured" toast). An unreadable grid changes nothing | `configgen.autoconfigured_packs()` |

The packs that were split stay in the catalogue as `supersededBy` packs
(`dolphin` → `gamecube`, `wii`; `mgba` → `gba`, `gbc`, `gb`). They keep
`sharesEmulator`, so the old tile on a box that has not migrated still launches
exactly as before. `ryujinx` → `switch` is the same: `switch` owns Ryujinx
(its generator, seed and `profileSaves`), and the old tile of a box that never
moved its games launches the same emulator. They are never offered (`gen-catalog.py`, `selected()`), never
added by the merge, and while their tile is on a grid the merge does **not** add
their successors either: the old tile holds the games, and empty twins beside it
would be a lie. The owner moves the games with `scripts/split-systems.py`, by
hand, when they choose ([07](07-config-and-data.md#splitting-a-system-scriptssplit-systemspy)).

### `files` — `src`, `template`, `when`, `ifAbsent`

```json
{ "template": "files/embertv-config.json.tmpl",
  "dest": "/opt/Twitch-TV/config.json",
  "owner": "user", "mode": "600",
  "when": "secrets.TWITCH_CLIENT_ID" }
```

- `src` copies verbatim; `template` expands tokens. Exactly one of the two.
- **Tokens**, in `dest` and inside templates: `@HOME@`, `@USER@`,
  `@GAMECORE_PATH@`, and `@<KEY>@` for every key the pack declares under
  `secrets`.
- `when: secrets.KEY` / `!secrets.KEY` picks between entries. This is what lets
  the twitch pack ship both a real config and a demo one with no branch in the
  installer.
- `ifAbsent: true` writes only when the destination does not exist. Re-running
  the installer is documented as safe, and for a file the owner is invited to
  hand-edit, safe has to mean untouched.

### What a launched process is told: `GAMECORE_LOG_DIR`

Everything `process_manager` starts gets `GAMECORE_LOG_DIR=<data>/logs/packs/<system>`
(`services/logs.py:child_env`). Its stdout and stderr already land in
`logs/launch/<system>/`; a pack script with a log of its own (the melonDS
multiplayer launcher) writes it there, creating the directory, in append mode:
GameCore empties it in place past 4 MB and on a purge. No `pack.json`
field: a pack that needs nothing more does nothing.

### `launch.fullscreen` and `launch.gamepadTrigger`

Two things a tile may need once the app is up, both read by
`backend/services/launch.py` right after the launch succeeds:

```json
"launch": {
  "path": "bash", "args": "/opt/Stremio/stremio-tv.sh",
  "fullscreen": { "wmClass": ["stremio", "Stremio", "com.stremio.Stremio"],
                  "timeoutSec": 60 },
  "gamepadTrigger": true
}
```

- `fullscreen` — for a window no CLI flag makes fullscreen: Stremio has none,
  and Lutris's nested gamescope (WM_CLASS `gamescope-brokey`) opens windowed
  under KWin despite `-f`; without gamescope, Proton names the game window
  `steam_app_default`. On Windows `fullscreen_enforcer.py` waits up to
  `timeoutSec` for the launched job's first window and makes it borderless
  fullscreen; `wmClass` is the Linux match and is not needed there. `merge.py`
  adds the block to a tile already on a box when the tile has none.
- `gamepadTrigger` — on Linux, re-fired `udevadm trigger` after launch so a
  Flatpak app saw pads plugged in afterwards. Ignored on Windows (no udev, no
  Flatpak); the key stays in the schema for the Linux packs.

The pack spells them in camelCase like the rest of the schema;
`gen-catalog.py` writes the `wm_class` / `timeout_s` spelling the enforcer has
always read into the tile entry.

### `launch.formerArgs` — a system that changed emulator

`switch` kept its id, its tile and `emu/switch/` when it went from Eden back
to Ryujinx, but a tile written under Eden carries `run @APPID@ -f -g`, and
Ryujinx reads `-g <rom>` as a graphics backend: the game never opens. A tile
is written once and `config/` is outside the OTA rsync, so the pack names the
args it used to ship and the update merge rewrites a tile still carrying one
(`merge.launcher_is_stale`). Any other args are the operator's and stay.

### Switch: updates and DLC before Ryujinx's first start

The seed points Ryujinx's library at `emu/switch` and `emu/Switch DLC & Updates`
(`game_dirs`, `autoload_dirs`) and skips its profile picker (one profile, and
the picker needs a mouse). Ryujinx then chooses each game's update and DLC in
a library scan, but that scan runs beside a launch: on a first start the game
ran in v1.0.0, and Mario Kart 8 rewrote a v3.0.4 save in the old format. So
`catalog/switch/title_updates.py` writes `games/<title>/updates.json` and
`dlc.json` first, when absent, at install and before each launch. Title ids
come from the ticket names in each NSP (`<rights id>.tik`), no decryption;
a game with two updates, or an NSP without a ticket, is left to Ryujinx.

### Switch: Eden's saves copied into Ryujinx

A box that ran Eden holds its Switch saves there only. They are copied into
Ryujinx, never moved, and Eden's files are only read:

| When | Where | What |
|---|---|---|
| `gamecore-emu install switch` | `postInstall`: `catalog/switch/steps/import-eden.sh` → `catalog/switch/eden_import.py` | `prod.keys`, `title.keys` and the firmware (`nand/system/Contents/registered`, the same `<id>.nca/00` layout Ryujinx reads) where Ryujinx has none, then the primary profile's saves. Before the first launch: the BIOS gate refuses a Switch without `prod.keys` |
| every launch | `prepare_launch` in `catalog/switch/generator.py` → `catalog/switch/eden_saves.py` | the playing profile's saves Ryujinx lacks. Runs after `profileSaves` placed the profile's folders, so it writes through those links into that profile's folder; its Eden saves are `<profile>/switch/save`, the folder the Eden-era pack used |

All of a profile's saves at once, not the launched game's: knowing which
title a dump is needs NCA decryption (`perGame`), and the index is rewritten
whole either way. After the first pass a launch only reads the index.

Each save becomes a container shaped like Ryujinx 1.3.3's own:
`bis/user/save/<id>/0/` (the data), `ExtraData0`/`1` (program id, user, type,
owner) and an entry in `bis/system/save/8000000000000000/0/imkvdb.arc` with
`lastPublishedId` moved past it. Eden's single user (its `profiles.dat`)
becomes Ryujinx's `last_opened` user; the all-zero user folder is device
saves (type 3).

| Guarantee | How |
|---|---|
| never overwrites a Ryujinx save | same program, user and type in the index: left alone |
| each Eden save once per target | `gamecore-eden-import.json` beside the index (it follows a profile's folder), so a save deleted in Ryujinx does not come back |
| never half a save | copied to `<id>.gamecore-tmp`, renamed, then the index; past the hook's deadline the copy is dropped and the rest waits for the next launch, because Ryujinx may already be reading the index |
| refuses what it cannot tell apart | several Eden users (account saves skipped, device saves copied), an unreadable index or `Profiles.json`, Eden or Ryujinx running, Eden's folder a link with no parked primary |

Log: `logs/packs/switch/eden-import.log`; a launch that copied something says so
in a `game:notice`.

### melonDS local multiplayer

`catalog/melonds/generator.py` implements the `launch_command` launch hook
([4](04-backend-services.md#launchpy--everything-before-the-spawn)). One pad,
or no ROM: it returns None and the launch is the solo one, untouched. Two to
four pads:

| Step | Where | What |
|---|---|---|
| hook | `catalog/melonds/multiplayer/setup.py` (`launch_command`) | inside the launch budget, only fast steps: blank saves, a job file (`~/.local/share/gamecore/melonds-multiplayer/job.json`: config, pads, snapshots) and the command. The SDL probes below take ~1 s per pad model; three pads overran the 3 s budget and melonDS started solo, in fullscreen |
| SDL order | `setup.py` (`prepare`, run by `launcher.py --prepare` before melonDS starts) | runs a probe with melonDS's own SDL2 inside its sandbox; joins each SDL device path (`/dev/hidrawN` for a DS4) to a player through sysfs (HID directory or MAC) |
| config | `setup.py` (`prepare`) | for every player: `[Instance{N-1}] JoystickID`, the fullscreen hotkey (F11), two screen windows (`Window0` top screen only, `Window1` bottom screen only, aspect "window" so each fills its half); `[Instance{N-1}.Joystick]` for players 2-4 (the pad's snapshot, else the slot-1 synthesis). Instance 1's solo values are parked and restored at the next solo launch, or solo would open two windows |
| command | `setup.py` | `launcher.py --players N --prepare=<job> -- <melonDS command without -f>`; melonDS gets `QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1` and `GAMECORE_MELONDS_PLAYERS=N` (the L3 daemon ignores L3 when N > 1) |
| instances | `catalog/melonds/multiplayer/launcher.py`, `atspi.py` | presses melonDS's own menus over AT-SPI (`gdbus`): System > Multiplayer > Launch new instance, File > Open recent > 1., then File > Boot firmware in every instance (the cart stays in: DS menu with the game and DS Download Play) |
| menu bar | `launcher.py`, `windows.py` | melonDS hides it only when it toggles fullscreen itself, for all of an instance's windows at once: the first window is activated (Qt drops keys sent to an inactive window under KWin) and gets F11 held across a frame; the AT-SPI menu bar heights confirm it, up to three tries |
| layout | `catalog/melonds/multiplayer/windows.py` | finds `[pN:wM]` windows, drops fullscreen, maximized states and decorations, then one full-height column per player (`columns()`), top screen above touch screen (`screen_rect()`). 2 players keep the native shape (720 px); 3-4 stretch vertically (x1.125, x1.5), the owner's choice. Xlib errors are ignored: the default handler exits, and the launcher exiting would end the session |
| mice | `catalog/melonds/multiplayer/mouse_touch.py`, `inputdev.py`, `arrows.py` | one mouse keeps the plain X pointer. Once a second mouse is really used, mice get players in the order they are used, are grabbed (evdev), move an arrow window wearing the box's cursor theme (`cursor_theme.py`: XCURSOR_THEME, else KDE's kcminputrc, Inherits= followed, ringed in black by `outlined()` so a pale theme shows on white; a drawn arrow without one) clamped to their player's touch screen (`touch_area()`: the bottom window `tile()` places; the top screen takes no touch), moved once per frame and raised once a second, constant gain like X's flat profile, and their left button touches through that player's own uinput touchscreen (`gc-touch-pN`). Not MPX: removing a master crashes kwin_x11, adding one fast crashed Electron's GTK3. One touchscreen per player: melonDS reads only the first touch point and Qt shares a device's fingers between windows. X's own pointer follows every touch (the touchscreens drive the core pointer); after each touch the router warps it to the last pixel and checks it with `XQueryPointer` until it is there. XFixesHideCursor did not hide it on the box. Nothing to clean after a SIGKILL (kernel and X server do it) |
| saves | `setup.py` (`blank_player_saves`, in the hook) | melonDS opens `<rom>.sav.N` for player N and, when it is missing, loads player 1's `<rom>.sav`: player 2 started on a copy of the owner's game. Before the launch, players 2-4 with no save get a blank one (0xFF, the size of player 1's). Player 1's and existing player saves are never touched; archives are skipped. Player 1's save folder follows the profile (`profileSaves`, `place_saves` in `generator.py`); players 2-4 stay beside the ROM |

melonDS 1.x links instances only inside one process (`LocalMP`); between
processes it offers LAN mode, opened from dialogs only. Hence one process and
its own menus, no patched emulator. Saves (`.sav.N`), firmware copies and MAC
addresses are per instance in melonDS itself. The launcher runs in the game's
process group, so suspend and quit reach melonDS; it never exits before
melonDS. Log: `melonds-multiplayer.log` in `$GAMECORE_LOG_DIR`, else `~/.cache/gamecore/`.

### PC games (Lutris)

`catalog/lutris` is the PC system: the Windows games the owner installs in the
Flatpak Lutris (GOG offline installers, itch.io, Epic or GOG through Lutris's
own services, discs). Everything is read from Lutris 0.5.22 (Flathub stable)
and 0.5.23 (beta) and checked against the stable Flatpak; the dev log
(`docs/dev-log/pc-pack.md`) says what was verified and how.

| Piece | Where | What |
|---|---|---|
| install | `pack.json` | `net.lutris.Lutris` from Flathub, its GL32/Compat.i386 extensions pulled with it (32-bit Vulkan/GL for Wine); `gamemode` and `python-yaml` from pacman; `hostAccess.gamemode`; `hostAccess.uinput` (the Xbox pad mirror) |
| GE-Proton + defaults | `files/gamecore-lutris-setup.timer` → `files/lutris_setup.py` | a minute after login, then daily: the latest GE-Proton release (tag from the `/releases/latest` redirect, sha512 checked) into `<runners>/wine/<tag>/`, where Lutris lists Proton builds and runs them through umu; then `runners/wine.yml` `version` (that build), `dxvk`, `vkd3d`, `esync`, `fsync` and `system.yml` `gamemode`, `game_path` (`emu/lutris-games` on the data root) — each only where absent (`files/lutris_defaults.py`). `version` moves to a newer build only while it still names one this pack installed. Two of our builds are kept, plus any a game config names |
| config location | `files/lutris_paths.py` | Lutris uses `~/.var/app/<id>/config/lutris` only if it exists, else everything is under `data/lutris`. The setup never creates that `config` folder |
| library | `generator.py` `sync_library` → `files/lutris_library.py` | before each listing (`services/pack_library.py`): one `<Title>.lutris` stub (JSON, the Lutris game id) per installed game in `emu/lutris/`, Lutris's `coverart/<slug>.jpg` copied as the cover when there is none. A stub keeps its name for its Lutris id (the playtime key survives a rename); a stub is removed only when `pga.db` was read and the game is gone; games in Lutris's `.hidden` category are left out |
| launch | `files/lutris_session.py` | the tile runs `python3 lutris_session.py --app-id @APPID@ <stub>`, which runs `flatpak run <id> lutris:rungameid/<id>`: no Lutris window. It is the session: it closes an idle Lutris first (else the command is forwarded over D-Bus and returns at once), follows the game through its `lutris-wrapper` process, then gives Lutris 10 s to quit before `flatpak kill`. Without a stub it opens Lutris's window |
| media | `scraper.mediaAlias: ["pc windows"]` | ScreenScraper system 138 ("PC Windows", LaunchBox "Windows"), by name: `.lutris` is never hashed (`gamemedia/ss_client.py`); `scraper.steamStore` for the minimum spec on the box back (`gamemedia/steam_store.py`) |

| Xbox pads | `files/lutris_xpad.py`, started by `lutris_session.py` | see below |

#### Xbox pads for PC games

Proton gives a DualShock 4 or a DualSense to the game as raw HID (hidraw), not
as XInput: a game that reads only XInput (Cyberpunk 2077) sees no pad. For the
length of the session, a thread in `lutris_session.py` does what Steam Input
does:

| What | How |
|---|---|
| which pads | `find_pads`: every evdev pad with `BTN_SOUTH`, sticks on X/Y and RX/RY, not Microsoft (`045e`, already XInput) or Valve (`28de`); connection order, 4 players at most |
| the copy | one uinput "Microsoft X-Box 360 pad" `045e:028e` per pad (the xpad layout: square sends `0x133`, triangle `0x134`), sticks scaled to ±32767, triggers 0-255, d-pad as a hat. PS is declared but never sent: the real pad already brings it to GameCore. No rumble |
| no double input | the session's environment, which flatpak and Lutris pass to the game, hides every Sony pad plus the pads present at launch: `SDL_GAMECONTROLLER_IGNORE_DEVICES` (GE-Proton's `winebus.so` honours it) and `PROTON_DISABLE_HIDRAW` (`winebus.sys`, per `0xVVVV/0xPPPP`). A non-Sony pad plugged after launch is not mirrored: it was not hidden |
| GameCore's own pads | the real pad is never grabbed. The copies carry phys `gamecore-virtual-pad/<n>`; `gamepad_devices.VIRTUAL_PAD_PHYS` keeps them out of the roster and `configgen.detect_pads` (one counted took player 2 and rewrote the emulator configs each time it came and went) |
| disconnect | the player's copy stays, released to neutral; the next pad to connect takes the first free player |
| lifetime | the uinput fds belong to the session process: SIGKILL or SIGTERM of the group closes them and the kernel removes the pads. While the session is suspended (SIGSTOP) they stay, idle |
| opt out | `GAMECORE_XBOX_PAD=0` in the game's Lutris environment variables (`system.env` of `games/<configpath>.yml`, found through `pga.db`): the game gets the real pads, as before. For a game whose native PlayStation support (glyphs, touchpad) matters more |
| no uinput | `/dev/uinput` not writable: nothing is hidden or mirrored |

Winetricks ships inside Lutris; nothing to add. `perGame` and `profileSaves`
are `supported: false` (Lutris keeps its own per-game configs; Windows games
save anywhere in their prefix).

### `usb` — the peripherals that are not SDL gamepads

The autoconfig pipeline knows exactly one kind of device: a pad that declares
`BTN_SOUTH` on an evdev node. `gamepad_monitor` enumerates `/dev/input/event*`,
`controller_registry` hands out a player slot, a generator writes a config for
whatever took it. Anything that does not enter through that door was invisible
end to end — no player slot, no udev rule, no line anywhere on screen:

- the **GameCube adapter** Dolphin drives over raw libusb, which has no evdev
  node at all;
- a **DolphinBar** and its Wiimotes, several HID interfaces whose shape depends
  on the mode switch on the bar;
- **arcade sticks** that enumerate as a keyboard — no `BTN_SOUTH`, so
  `pads_by_key()` drops them on purpose;
- **wheels**, whose force-feedback node is separate from their buttons;
- RPCS3's **DS3 passthrough** over hidraw.

`gamepad_monitor` is right to keep dropping these: a player slot is for
something that can be player 2, and a light gun is not. The gap was that there
was no *other* list either.

```json
"usb": [
  {
    "vidPid": "057e:0337",
    "class": "adapter",
    "label": "GameCube controller adapter",
    "udevRule": "SUBSYSTEM==\"usb\", ATTRS{idVendor}==\"057e\", ATTRS{idProduct}==\"0337\", MODE=\"0666\"",
    "note": "Check the switch on the adapter is on Wii U rather than PC."
  }
]
```

`class` is one of `gamepad`, `adapter`, `wheel`, `lightgun`, `arcade`. It is
what the roster was missing — `gamepad` is the case the pipeline already
handled, the other four are the ones it could not express. A class this release
does not know is listed as *unknown* rather than raising: `config/catalog.d/` is
data the operator wrote, and the OTA tier can carry a pack from a newer
catalogue.

Declaring one does four things:

| | where |
|---|---|
| writes `/etc/udev/rules.d/99-gamecore-<pack>.rules` at install | `installer/applier.py:apply_udev` |
| lists the device present-or-absent (no screen draws it since the controller screen lists pads only) | `GET /api/controllers/devices` |
| re-fires `udevadm trigger` after launch, so a device plugged in later reaches the Flatpak sandbox | `services/launch.py` |
| broadcasts `game:notice` with the pack's own note when the device is absent | `usb_devices.launch_notice` |

Two rules worth stating out loud:

- **It never refuses a launch.** A USB accessory is optional by nature — Dolphin
  plays perfectly with a DualShock 4 and no adapter — so blocking would be
  GameCore inventing a fault, the mistake `bios.required: false` exists to
  avoid. The BIOS gate blocks; this one only speaks.
- **The rule is written, never activated.** `apply_udev` runs no `udevadm`.
  Reloading per pack would re-fire the whole device tree a dozen times during
  one install, and the rules matter at the next plug event anyway;
  `install/arch.sh` reloads once, at the end. `udevRule` also never reaches the
  tile — it is install-time text needing root, and the tile is read on every
  launch.

Write the narrowest rule that works. `MODE="0666"` on a device that also
carries a keyboard interface is every keystroke on the box readable by any
local uid — `install/arch.sh` documents that trade at length around
`99-gamecore-input.rules`.

### `profileSaves` — saves per profile

The save follows the person, never the player slot. At launch the active
profile ([04](04-backend-services.md#profile_savespy--saves-that-follow-the-profile))
is player 1. Only the pack that owns the emulator declares it; a pack that
`sharesEmulator` (gb, gbc, mgba → gba; dolphin, wii → gamecube) follows its
owner, with the owner's folder: `<data>/emu/profile-saves/<profile id>/<owner id>/`.
`check-catalog.py` refuses it on a sharing pack. Three forms:

**1. An object — no code, most emulators.** What to point at the profile's
folder (`@SAVES@`), from pack.json alone (`services/profile_save_paths.py`):

```json
"profileSaves": {
  "keys": [{"config": "PCSX2.ini", "section": "Folders", "key": "MemoryCards", "value": "@SAVES@/memcards"}],
  "dirs": [{"config": "bis/user/save", "as": "user-save"}]
}
```

| Field | What |
|---|---|
| `keys[]` | an option in a config file: `config` (relative to the config directory configgen resolves, native or flatpak; `..` allowed) or `path` (tokens), `section` (absent for a flat file such as RetroArch's), `key`, `value` (`@SAVES@` = the profile's folder; a value without it, `true`, is set as is), `quote` |
| `dirs[]` | a folder the emulator has no option for: renamed `<name>.gamecore-primary`, replaced by a symlink to `<profile folder>/<as or name>`, renamed back for the primary. A symlink the owner put there himself (saves on another disk) counts as his folder: renamed too, never deleted. Its parent must exist (the emulator ran once), else the launch is refused; `optional` skips it instead, for an emulator that moved its folder between versions, as long as one entry applies |

The owner's values are remembered in `profile-saves/.primary.json` before the
first write, keyed by file and option (not by pack), and put back for the
primary profile and when the game ends.

**2. `per-instance` / `p1` — a hook in generator.py**, for a layout that
needs code. The core calls `place_saves(dirs, root, opts)`:

| Argument | What |
|---|---|
| `dirs` | 4 entries, player 1 first: the profile's folder (created), or None = the emulator's default location |
| `root` | `<data>/emu/profile-saves`: a value under it left by an earlier launch must be cleared for a None entry; any other value is the owner's and stays |
| `opts` | what a controller generator gets (`target`, `config_dir`, `home`…), whether or not autoconfig is on |

`per-instance` is melonDS (one instance per player; players 2-4 get None
until pads carry profiles). `p1`: one save, player 1's.

**3. `{"supported": false, "why": "…"}`** — the saves cannot follow a profile,
and why. Settings → Profiles names these systems (`shared_saves`).

Save locations checked against the `save-manager` addon's catalogue, which
backs the same folders up.

| Emulator | How |
|---|---|
| RetroArch (17 packs) | `savefile_directory`, `savestate_directory` in `<pack>.cfg`, before its `#include` |
| mGBA (gba, gb, gbc, mgba) | `[ports.qt] savegamePath`, `savestatePath` |
| Snes9x | `[Files] SRAMDirectory`, `SaveStateDirectory` |
| RMG (gopher64) | `[Core] SaveSRAMPath`, `SaveStatePath` |
| DuckStation | `[MemoryCards] Directory`, `[Folders] SaveStates` |
| PCSX2 | `[Folders] MemoryCards`, `Savestates` |
| Dolphin (gamecube, wii, dolphin) | `[Core] MemcardA/BPath`, `GCIFolderA/BPath`, `[General] NANDRootPath` |
| Azahar | `[Data%20Storage]` custom storage, `sdmc_directory` (the NAND stays shared; titles installed as CIA are per profile) |
| melonDS | hook, `per-instance` |
| PPSSPP | folders `PSP/SAVEDATA`, `PSP/PPSSPP_STATE` |
| Ryujinx (switch) | folders `bis/user/save` (`as: user-save`) and the save index `bis/system/save/8000000000000000` (`as: save-index`); Eden's per-profile folder was `save`, read by the Eden import |
| RPCS3 | folders `dev_hdd0/home/00000001/savedata` and `trophy` |
| shadPS4 | folder `home/1/savedata` (≥ 0.16) or `savedata/1` (≤ 0.15), whichever exists (`optional`) |
| Cemu | folder `mlc01/usr/save/00050000` (game saves; accounts stay shared) |
| Xenia | `supported: false`: title updates and DLC share `content/` with the saves, so a profile of its own would lose them |

Rules, each one a way to mix two people's progress:

- **The primary profile keeps today's paths.** It owns every save made
  before profiles; with it active nothing is written but the owner's values back.
- **Rewrite every launch, and put back when the game ends**
  (`profile_saves.release`). Emulators save their config on exit.
- **Never move, copy or delete a save.** Redirection, or a rename in place.
- **Refuse rather than guess.** No parent folder, or both a real folder and
  its `.gamecore-primary`: the launch is refused. A config file the emulator
  has not written yet (RetroArch writes its `.cfg` on exit) is created with
  the save options alone, which is what it would read anyway.

A Flatpak only sees the folder if its sandbox reaches the data root (the
emulators get `/userdata` and the install directory). `GET /profiles` returns
`separate_saves` and `shared_saves` for Settings → Profiles.

### `perGame` — and why it is **required** on every emulator pack

Whether this emulator can be configured one game at a time, and if so where that
file goes. Implemented in `backend/services/pergame.py`.

The cases it exists for are binary, not cosmetic: an RPCS3 title that sits on a
black screen until Write Color Buffers is ticked, a Dolphin game that freezes on
anything but Vulkan, a Wii U dump whose textures are garbage without its graphic
pack. The difference is not 40 fps against 60 — it is *starts* against *does not
start*, and from a sofa the only remedy was to leave GameCore, find the
emulator's own window, and hunt.

**The block is mandatory, and that is the design.** An emulator that cannot do
this must say so explicitly:

```json
"perGame": { "supported": false, "why": "…" }
```

Leaving it out fails validation, because the failure mode of an absent block is
silence: the button simply does not appear, and the player cannot tell "this
emulator has no per-game settings" from "GameCore forgot this emulator". Neither
can anyone reading the catalogue. A declared `false` with a reason is an answer;
an omission is a bug that looks like a feature.

Three properties follow from where the data lives:

- **The emulator's file is derived; `<DATA>/config/per-game/<system>/<id>.json`
  is the original.** Writing straight into `~/.var/app/…` and calling that the
  record loses everything the day somebody runs `flatpak uninstall
  --delete-data` — which people do when an emulator misbehaves, which is exactly
  when they have per-game settings. Under the data root it is also what a backup
  copies and what the OTA rsync already leaves alone.
- **Nothing maps a setting onto thirteen vocabularies.** No table translates
  "internal resolution" per emulator. That layer is what makes Batocera's
  configgen impossible to keep current — every emulator release moves an option
  and the map has to be chased. GameCore writes the section and key it is given,
  verbatim, and the button beside it opens the emulator's own settings window. A
  shipped profile names RPCS3's spelling of RPCS3's option because it *is* an
  RPCS3 profile.
- **`own-keys` merging makes removal honest.** Every write records what it
  displaced — the previous value, or a marker saying the key was absent — and
  removal puts that back key by key, deleting the file only when GameCore
  created it. "Undo" cannot mean "delete the file": the file may hold the
  player's own settings alongside ours. Without that record, "the player can
  remove it" is a button that lies.

And because it is **data**, the day shadPS4 grows per-title configs is a
`pack.json` pushed down the signed catalogue channel — not a release, not a
frontend build, not a box reboot. That is the whole reason the block is here and
not in a table in `backend/services/`.

---

## 3. The install pipeline

```mermaid
flowchart TB
    wiz["installer-gui (PyInstaller binary)<br/>emulators · apps · addons · API keys"]
    conf["/tmp/gamecore-install-*.conf<br/>0600, deleted when the wizard closes"]
    arch["install/arch.sh --unattended<br/>packages · user · services · SDDM · Caddy"]
    prov["scripts/gamecore-provider.py<br/>--kind emulator|app --select …"]
    app["backend/services/installer/applier.py"]
    packs[("catalog/&lt;id&gt;/pack.json")]

    wiz -->|writes| conf --> arch
    arch -->|"one call per kind"| prov --> app
    packs --> app
    packs -.->|"ids, flatpaks, rom-dirs,<br/>sandbox, launchers"| arch
```

`arch.sh` holds **no list of emulators or apps**. It asks the catalogue what
exists, filters by what the operator selected, and hands the rest to the
provider. The two passes are one call each:

```bash
gamecore-provider.py install --kind emulator --select "$EMULATORS" …
gamecore-provider.py install --kind app      --select "$APP_SEL"   …
```

The provider prints one line per event, and `arch.sh` colours them:

| Prefix | Meaning |
|---|---|
| `PACK <id>` | starting this pack — drives the progress bar |
| `OK <msg>` | done |
| `SAME <msg>` | already there, nothing changed |
| `FAIL <msg>` | this tile will be missing; the run continues |
| `UNIT <name>` | a user unit to daemon-reload and restart now |

### The order inside a pack, and why it is fixed

```
packages → install → sources → files → services → postInstall
```

A file written into a checkout needs the checkout. A unit needs its `ExecStart`
to exist. A post-install step needs all three. EmberTV is the case that pins it:
`sources` clones `/opt/Twitch-TV`, `files` writes `config.json` into it and the
Firefox `user.js` (which creates the profile directory), `services` installs
`embertv.service`, and only then can `steps/make-cert.sh` generate a certificate
and `steps/trust-cert.sh` import it into that profile's NSS database.

### What is deliberately *not* in a pack

`gamepad-tv-bridge` is cloned by `arch.sh`, not by a pack. It translates gamepad
buttons into keystrokes for the Firefox kiosks, so it serves both EmberTV and
YouTube and belongs to neither. Same for linger, the user-unit restart, and the
`~/.config/systemd` ownership fix: user-session infrastructure, not app content.

---

## 4. Generated artefacts — run `gen-catalog.py`

Three committed files are **derived** from the packs:

| Generated | Why it exists |
|---|---|
| `install/installer-gui/catalog_data.py` | the wizard's tick-box list. It is a PyInstaller binary that runs **before** the repository is on the machine, so the list is baked in at build time |
| `install/generated/apps.json.dist` | the pristine app-tile catalogue `arch.sh` copies into `config/` |
| `install/generated/systems.json.dist` | same, for systems |

```bash
python3 scripts/gen-catalog.py          # regenerate
python3 scripts/gen-catalog.py --check  # CI: fail if the committed copies are stale
```

Forget it and your pack exists, validates, and appears in **no** tick box —
therefore is never selected, therefore is never installed, and no tile is drawn.
CI's `--check` step is what stops that reaching a release.

---

## 5. Shipped packs and local packs

`backend/services/catalog/loader.py` loads two locations and merges them, local
winning entirely:

| | `catalog/` | `config/catalog.d/` |
|---|---|---|
| origin | shipped with the release | dropped on the box |
| reviewed, in CI | yes | no |
| may carry `generator.py` | yes | ignored |
| `postInstall` `services` `sources` `packages` | honoured | **stripped**, with a warning at every load |
| survives an OTA | replaced by the release | untouched |

The rule is code vs data. A directory dropped into `config/catalog.d/` is data
only, because honouring its privileged blocks would make "drop a directory"
equivalent to arbitrary code execution as root — and the install CLI would make
that reachable from the UI. `GAMECORE_TRUST_LOCAL_PACKS=1` lifts it, and is
logged on every load rather than once.

### Three tiers, and the signed remote one

There are in fact **three** sources, and their precedence is deliberate:

```
catalog/                 shipped   the release
<DATA>/catalog-ota/      remote    signed corrections, override shipped
config/catalog.d/        local     the operator, overrides everything
```

**The operator is last on purpose.** A box whose owner pinned a pack by hand must
not have that undone by an endpoint, or the update channel is also a way to
overrule the person holding the machine.

The middle tier (`backend/services/catalog/ota.py`, key material in
`catalog/_ota/`) exists for one concrete objective: an app id dies on Flathub and
every box is corrected within a day, without cutting a release. Today that
correction *is* a release — `release.yml` fires on every push to `main`, so
fixing one string in one `pack.json` rebuilds the frontend, rebuilds the
PyInstaller wizard, publishes three assets and ships the whole application to
every box.

Three properties are load-bearing:

- **A bundle is Ed25519-signed, and a box with no trust anchor refuses every
  bundle before fetching it.** An unauthenticated remote catalogue is a remote
  code execution primitive with a pleasant API: it names the application the box
  installs and the one it launches, so whoever holds the endpoint, the DNS or the
  TLS terminator holds the fleet. The private key never enters this repository,
  never enters CI and never reaches a box; `.gitignore` and a test refuse the
  obvious filenames, but those are accident nets, not the control. The control is
  that the key lives elsewhere.
- **`CATALOG_VERSION` must be strictly greater than the applied one.** Not
  tidiness: yesterday's bundle stays validly signed for ever, and replaying it is
  how somebody puts back the app id today's bundle fixes. **A signature cannot
  express freshness; only the version can.**
- **Data only, with no opt-in.** Stricter than `config/catalog.d/`, where the
  operator can say "I put that directory there myself". Nobody can say that about
  bytes off the network. `postInstall`, `services`, `sources`, `packages`, `files`
  and `secrets` are dropped on arrival, and a bundle being a single JSON document
  has no way to express a `generator.py`, a symlink or a file mode in the first
  place.

Rotating the key means cutting a release: a box trusts exactly the public key its
installed version shipped. Full procedure in
[`../../catalog/_ota/README.md`](../../catalog/_ota/README.md).

> **The channel is off until `catalog/_ota/catalog-signing.pub` is committed**,
> and `catalog/CATALOG_VERSION` is still `1`. Turning it on is a deliberate act
> by whoever will hold the key.

Two more rules the applier enforces:

- **A pack may only read its own directory.** `src`, `template`, `unit` and
  `run` are resolved against the pack directory and refused if they land outside
  it. Otherwise "drop a directory" becomes "read the rest of the disk".
- **Secrets never touch `argv`.** `sudo -u <user> env KEY=value …` would be
  shorter and puts every value in `/proc/<pid>/cmdline`, which is world-readable;
  one of those values is a Twitch client secret. `--preserve-env` names the
  variables and lets sudo carry them from an environment instead.

---

## 6. Adding an emulator

```bash
mkdir -p catalog/myemu
$EDITOR catalog/myemu/pack.json     # id, kind, label, platform, color, launch
cp …/logo.png catalog/myemu/
python3 scripts/check-catalog.py    # schema
python3 scripts/gen-catalog.py      # the three derived files
git add catalog/myemu install/
```

Minimum viable pack:

```json
{
  "id": "myemu",
  "kind": "emulator",
  "label": "Some Console",
  "emulatorName": "MyEmu",
  "platform": "SOMECONSOLE",
  "family": "Sega",
  "color": "#1e90ff",
  "install": { "provider": "flatpak", "appIds": ["org.example.MyEmu"] },
  "launch": { "path": "flatpak", "args": "run @APPID@ --fullscreen" },
  "roms": { "dir": "emu/myemu", "extensions": ["*.bin", "*.zip"] },
  "perGame": { "supported": false, "why": "Not verified on a real install yet." }
}
```

Optional, and none of it needs a line anywhere else: `seed/` for a curated
config (`config.dest` says where it lands), `generator.py` + `controllers` for
gamepad bindings, `sandbox` if the emulator needs different Flatpak permissions,
`packages` for a system dependency, `scraper` for covers.

Not comfortable writing the JSON by hand? §10 carries ready-to-paste prompts
that let **any** AI chatbot — including free-tier ones — draft it, with
`check-catalog.py` as the safety net.

## 7. Adding an application

Same shape with `"kind": "app"`, plus whatever the app needs beside it:

```json
{
  "id": "myapp", "kind": "app", "label": "My App",
  "platform": "Web", "color": "#8a5fff",
  "packages": { "pacman": ["firefox"] },
  "sources": [{ "git": "https://…/myapp.git", "dest": "/opt/MyApp", "owner": "user" }],
  "files":   [{ "src": "files/myapp.conf", "dest": "@HOME@/.config/myapp.conf",
                "owner": "user", "mode": "644" }],
  "services":[{ "unit": "files/myapp.service", "scope": "user", "enable": true }],
  "postInstall": [{ "run": "steps/setup.sh", "label": "first-run setup",
                    "timeoutSec": 60 }],
  "launch":  { "path": "bash", "args": "/opt/MyApp/start.sh" }
}
```

Every path in `files`, `services` and `postInstall` is **relative to the pack**,
and the pack must actually carry it. `backend/tests/test_installer_applier.py`
fails the build if it does not — which is the check the repository did not have
when a refactor deleted `install/firefox-profiles/` while `arch.sh` still read
it. That install died at 66 %, on a fresh machine, months later.

---

## 8. Querying the catalogue from a script

`scripts/catalog-query.py` is the shell-side reader. It is why `arch.sh`,
`flatpakify-systems.sh` and `install-emu-configs.sh` contain no lists:

```bash
catalog-query.py ids --kind emulator          # one id per line
catalog-query.py flatpaks --kind emulator     # id<TAB>the resolved appId
catalog-query.py app-ids --select steam       # the Flatpak id of one app
catalog-query.py rom-dirs                     # every roms.dir
catalog-query.py config-dest --select mgba    # where seed/ goes
catalog-query.py sandbox --gamecore-path /opt/GameCore
catalog-query.py packages --select duckstation
catalog-query.py launchers
```

## 9. Verifying an install

`scripts/check-install.sh` runs on the box afterwards and changes nothing. It
checks the files, the catalogue, the launchers (a tile naming a Flatpak nobody
installed is a dead tile), the services, the auto-login session — read back from
`/var/lib/gamecore/manifest.env`, because what the kiosk session *is* varies by
box — and that the API answers.

`"the install finished without an error"` and `"the box works"` are different
statements. `arch.sh` warns and carries on for a dozen recoverable failures, and
each one is a tile that is quietly absent.

---

## 10. Drafting a pack with a free AI chatbot

A pack is a single JSON file, and everything that can go wrong in it is caught
**locally** by `scripts/check-catalog.py` — schema, logo, ROM-dir collisions,
app-id collisions, seed hygiene, `@APPID@` discipline (§2). That division of
labour is what makes this workflow safe with *any* model, however small: the
chatbot only has to produce a plausible draft, and the validator — not the
model — is what guarantees correctness. No paid model required.

The loop:

```bash
mkdir -p catalog/<id>            # 1. paste the chatbot's JSON as pack.json
cp …/logo.png catalog/<id>/      # 2. a logo is required (logo.svg also works)
python3 scripts/check-catalog.py <id>   # 3. errors? → paste them into prompt B
python3 scripts/gen-catalog.py   # 4. clean → derive the .dist files
```

Repeat 3 until silent. Two or three rounds is normal with a small model.

### Prompt A — interview, then draft

Paste this into any chatbot, as is. It is self-contained on purpose: a
free-tier model cannot open this repository, so everything it needs is in the
prompt — and it is told to interview the human first, because the human knows
the emulator and the model does not.

````text
You are helping me write a `pack.json` file for GameCore, an emulation box.
A pack describes ONE emulator. The file will be machine-validated, so follow
the rules below exactly.

STEP 1 — Ask me these questions, ONE numbered list, then WAIT for my answers:
1. Emulator name, and the console(s) it emulates?
2. Is it on Flathub? If yes, the exact application id (like org.example.Emu).
3. The command line that launches it fullscreen with a game file, if you
   know it (otherwise I will test later).
4. Which file extensions do the game dumps use? (like .gba, .iso, .zip)
5. Does one emulator run SEVERAL distinct machines (like Game Boy AND
   Game Boy Advance)? If yes: each machine's name, its own extensions, and
   the aspect ratio it draws if known (like 3:2 or 10:9).
6. Does it need BIOS/firmware files the user must supply? Which filenames?
7. A hex color for the tile (or tell me the console's brand color).
8. A short platform code, uppercase (like GBA, PS2, SWITCH).

STEP 2 — After my answers, output ONLY a JSON code block, no prose.
Start from this template and keep ONLY the keys you have answers for.
NEVER invent a key that is not shown here. NEVER guess a value: if I did
not answer something, leave that key out entirely.

{
  "id": "myemu",
  "kind": "emulator",
  "label": "Some Console",
  "emulatorName": "MyEmu",
  "platform": "SOMECONSOLE",
  "color": "#1e90ff",
  "install": { "provider": "flatpak", "appIds": ["org.example.MyEmu"] },
  "launch": { "path": "flatpak", "args": "run @APPID@ --fullscreen" },
  "perGame": { "supported": false,
               "why": "One sentence: what stops per-game settings here." },
  "roms": {
    "dir": "emu/myemu",
    "extensions": ["*.gb", "*.gba", "*.zip"],
    "consoles": [
      { "id": "gb", "label": "Game Boy", "ratio": "10:9",
        "extensions": ["*.gb"] },
      { "id": "gba", "label": "Game Boy Advance", "ratio": "3:2",
        "extensions": ["*.gba"] }
    ]
  },
  "bios": { "dir": "emu/myemu/bios",
            "files": [ { "file": "bios.bin", "required": true,
                         "note": "Where the user gets it, in one sentence." } ] }
}

HARD RULES:
- "id": lowercase letters/digits only; it names the pack's directory.
- "launch.args" for a Flatpak MUST write @APPID@, never the real app id.
- Extensions always look like "*.ext", lowercase.
- "consoles" means DISTINCT MACHINES one emulator runs — it is NOT a list
  of extensions. One machine = one entry, and the block only exists to
  tell several machines apart: it needs AT LEAST TWO entries. If the
  emulator runs a single machine, leave "consoles" out entirely.
- "ratio" is "W:H" with plain integers, like "4:3" or "10:9". If you are
  not certain of a machine's ratio, leave "ratio" out — wrong is worse
  than absent.
- "color" is "#" + 6 hex digits.
- "perGame" is REQUIRED on an emulator. Unless I tell you this emulator
  supports one-game-at-a-time settings, keep `"supported": false` and put
  a real reason in "why" — never the words "not implemented".
- Every console's extensions MUST also appear in "roms.extensions" —
  the outer list is everything the pack scans, the console lists split it.
- Leave out "bios" entirely if the emulator needs no user-supplied files.
  If present, every entry needs all three keys: "file" (exact filename),
  "required" (true/false), "note" (one sentence: where the user gets it).
- Output must be valid JSON: double quotes, no comments, no trailing commas.
````

### Prompt B — the fix loop

When `check-catalog.py` prints errors, paste this — with the errors and the
current JSON — into the same chat:

````text
The validator rejected the pack.json you produced. Here is its output,
then the current file. Fix ONLY what the errors name, change nothing else,
and reply with the complete corrected JSON code block, no prose.

VALIDATOR OUTPUT:
<paste the check-catalog.py lines here>

CURRENT FILE:
<paste pack.json here>
````

### When the pack needs an advanced block

The template above covers the common case: a Flathub emulator with ROMs.
For anything beyond it — `seed/` + `config`, `controllers` + `generator.py`,
`sandbox` flags, `perGame`, `localMedia`, `usb`, or an app-kind pack with
`services` and `postInstall` — do not ask the chatbot to invent the shape.
Open the shipped pack that already does the same thing (§2 names which block
each pack exercises; `catalog/mgba` for multi-console + seed, `catalog/rpcs3`
for bios + perGame, `catalog/twitch` for an app with services) and paste that
whole `pack.json` into the chat as a model, with one line: *"same shape as
this, adapted to <emulator>"*. A small model copies a working example far
more reliably than it follows an abstract description — and whatever it gets
wrong, `check-catalog.py` names it, and prompt B closes the loop.

What no chatbot can do is the part that was always manual: `logo.png` on
disk, dropping real BIOS files, and pressing the buttons to confirm the
launch line actually reaches fullscreen. The pack only *declares*; the box
verifies (§9).
