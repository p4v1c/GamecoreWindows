# GameCore

A retro gaming frontend built for kiosk / living-room use.  
React + Electron shell + FastAPI backend — plug in a controller and play.

![GameCore in the Orbit theme: a Nintendo 3DS picked in a row of consoles](docs/img/themes/orbit-home.webp)

Website: [p4v1c.github.io/GamecoreRenew](https://p4v1c.github.io/GamecoreRenew/)

---

> **Licence** — GPL-3.0-or-later, see [`LICENSE`](LICENSE).
> **Contributing** — [`CONTRIBUTING.md`](CONTRIBUTING.md).
> **Upgrading a box** — [`CHANGELOG.md`](CHANGELOG.md) lists what needs your hand.

## Table of contents

1. [What it does](#what-it-does)
2. [Requirements](#requirements)
3. [Installation (device)](#installation-device)
4. [Uninstallation](#uninstallation)
5. [Development setup](#development-setup)
5. [First launch](#first-launch)
6. [Adding ROMs](#adding-roms)
7. [Controller navigation](#controller-navigation)
8. [Settings & Wi-Fi](#settings--wi-fi)
9. [Themes](#themes)
10. [OTA updates](#ota-updates)
11. [Adding an emulator or an app](#adding-an-emulator-or-an-app)
12. [Living-room box setup](#living-room-box-setup)
13. [Project structure](#project-structure)

---

## What it does

GameCore is a full-screen game launcher designed to run on a dedicated machine connected to a TV.  
You navigate with a gamepad, launch emulators, and never touch a keyboard.

**Supported emulators (out of the box):**

| ID | Emulator | System |
|----|----------|--------|
| `gamecube` | Dolphin | Nintendo GameCube |
| `wii` | Dolphin | Nintendo Wii |
| `duckstation` | DuckStation | PlayStation 1 |
| `pcsx2` | PCSX2 | PlayStation 2 |
| `rpcs3` | RPCS3 | PlayStation 3 |
| `ppsspp` | PPSSPP | PSP |
| `cemu` | Cemu | Wii U |
| `switch` | Ryujinx | Nintendo Switch |
| `azahar` | Azahar | Nintendo 3DS |
| `gb` | mGBA | Game Boy |
| `gbc` | mGBA | Game Boy Color |
| `gba` | mGBA | Game Boy Advance |
| `melonds` | melonDS | Nintendo DS |
| `gopher64` | Rosalie's Mupen GUI (Mupen64Plus core) | Nintendo 64 |
| `xenia` | Xenia Canary | Xbox 360 |
| `shadps4` | shadPS4 | PlayStation 4 |

> - **Nintendo 64**: the pack **id** is `gopher64` but the emulator installed is **Rosalie's Mupen GUI** (`com.github.Rosalie241.RMG`), a front end over the Mupen64Plus core — which is why its config file is `mupen64plus.cfg`. The id is a key: it names the catalogue directory, `emu/gopher64/` on every installed box, and the controller snapshots. Renaming it without a migration would move a player's N64 library under a path the scanner no longer reads, so it stays.
> - **Nintendo Switch** runs Ryujinx (Ryubing, `io.github.ryubing.Ryujinx`). Put `prod.keys` (and `title.keys` if you have one) in `~/.var/app/io.github.ryubing.Ryujinx/config/Ryujinx/system/`, updates and DLC as `.nsp` in `emu/Switch DLC & Updates/`. A box that ran Eden gets Eden's keys, firmware and saves copied into Ryujinx when Ryujinx is installed, and each profile's at its first launch; Eden's files are only read (`CHANGELOG.md`). The Eden variant lives on the `switch-eden` branch.
> - **PlayStation 1** uses the official DuckStation **AppImage** — the Flatpak was discontinued upstream in 2025.
> - **Xbox 360** runs Xenia Canary **through Wine** (`lib/xenia/xenia_canary.exe`, downloaded by the full installer).
> - **PlayStation 4** uses the shadPS4 Flatpak; games are folders (`emu/shadps4/<Game>/eboot.bin`, `scanDirs`).
> - Everything else installs from Flathub. Full-mode installs grant each emulator `--filesystem` (ROMs) and `--device=all` (controller) overrides automatically.

---

## Requirements

| | Minimum |
|--|---------|
| OS | Arch Linux / Manjaro, already installed |
| Firmware | UEFI |
| Display | 1920×1080 (Full HD) |
| GPU | Any — hardware acceleration recommended |
| RAM | 4 GB |
| Storage | 80 GB + (60 GB system + your ROM library) |
| Controller | Any XInput / evdev gamepad |

All emulators are installed via **Flatpak**. Make sure Flatpak is available on your system before running the installer.

---

## Installation (device)

> Run this on the machine that will act as the kiosk.  
> The installer sets up auto-login, auto-start, and all dependencies.

**Graphical installer** — a native step-by-step wizard, like any
desktop installer. Download `gamecore-installer` from the
[latest release](https://github.com/p4v1c/GamecoreRenew/releases/latest), then:

```bash
chmod +x gamecore-installer
./gamecore-installer
```

Pick your emulators and addons, paste your API keys (optional), hit Install —
it asks for the administrator password (polkit), downloads the latest GameCore
release and does everything. Re-running it is safe.

**Command line** (SSH / no graphical session):

```bash
git clone https://github.com/p4v1c/GamecoreRenew.git
cd GamecoreRenew
sudo bash install/arch.sh                      # interactive prompts
sudo bash install/arch.sh --unattended my.conf # scripted (see install/install.conf.example)
```

What the installer does:
- Installs Node.js, Python, Flatpak, Plasma and the drivers your GPU needs
- Installs the emulators and apps you ticked — each one from its own
  [catalogue pack](#adding-an-emulator-or-an-app), nothing is hardcoded
- Creates a Python virtual environment and installs backend dependencies
- Builds the React frontend, installs Node modules for Electron
- Creates the Linux user you named (or reuses it if it exists)
- Configures SDDM auto-login into that user's **KDE Plasma X11 session**, with
  the GameCore kiosk drawn over it
- Registers two systemd services: `gamecore-backend` and `gamecore-ui`
- The machine boots straight into GameCore after a reboot

Closing GameCore from Settings → Desktop drops you on that Plasma desktop. To
keep the desktop across reboots — and put the kiosk back later:

```bash
sudo gamecore-session-select desktop     # kiosk off, it stops starting at boot
sudo gamecore-session-select gamecore    # kiosk back on
gamecore-session-select status           # which mode, and the sessions available
```

There is one session, so this changes no login configuration: it enables or
disables `gamecore-ui.service`, nothing else.

Verify an install from the outside — it changes nothing:

```bash
bash /opt/GameCore/scripts/check-install.sh
```

After installation:
```bash
sudo reboot
```

---

## Uninstallation

```bash
sudo bash /opt/GameCore/install/uninstall.sh --dry-run   # show everything it would do
sudo bash /opt/GameCore/install/uninstall.sh             # do it
```

The installer records what it changed in `/var/lib/gamecore/`, and the uninstaller
reads that manifest — so it only undoes what **this** box's install actually did.
A package that was already present is never removed, a user account that predated
GameCore is never deleted, and an emulator config that existed before is *restored*
from its `.bak-preinstall` backup rather than deleted.

By default it removes the services, the auto-login, the sudoers and udev rules, the
Caddy configuration and its root CA, the companion checkouts in `/opt`, the stored
web password, and the application files — and **keeps your ROMs** (`emu/`) and
`config/`.

| Flag | Effect |
|---|---|
| `--dry-run` | Print every action, change nothing. Run this first. |
| `--purge` | Also delete `emu/` (ROMs) and `config/`. |
| `--remove-flatpaks` | Uninstall the Flatpak emulators **this install** added. Their save data in `~/.var/app/` is **kept** — the uninstaller never passes `--delete-data`. Remove it yourself with `flatpak uninstall --delete-data <app-id>`. |
| `--remove-packages` | `pacman -Rns` the packages this install added and that nothing else requires. |
| `--remove-user` | Delete the GameCore user — only if the manifest proves the installer created it. |
| `--yes` | No confirmation prompts. |
| `--user` / `--path` | Override the auto-detected user and install directory. |

Two things it cannot undo, and says so at the end: the `pacman -Syu` the installer
ran, and the in-place rewrite of `config/systems.json` / `config/apps.json`.

`sshd`, `bluetooth` and `sddm` are left enabled — they are system services that
almost certainly predate GameCore. The installer did enable SSH, so close it
yourself if you want it closed: `sudo systemctl disable --now sshd`.

---

## Development setup

> For development on your own machine — no auto-start, no kiosk mode.

```bash
git clone https://github.com/p4v1c/GamecoreRenew.git
cd GamecoreRenew

# 1. Backend
pip install -r backend/requirements.txt
# Loopback only — the core has no authentication of its own, and
# docs/SECURITY.md phase 1 is "only :8443 listens on the LAN".
uvicorn backend.main:app --host 127.0.0.1 --port 8765 --reload

# 2. Frontend (separate terminal)
cd frontend && npm install && npm run dev
# → http://localhost:5173

# 3. Electron (separate terminal)
cd electron && npm install
ELECTRON_DEV=1 npx electron .

# Tests — see docs/TESTING.md
pip install pytest
pytest backend/tests -q -m "not network"    # what CI runs
```

`DEBUG = True` in `backend/config.py` and `const DEBUG = true` in `electron/main.js` enable:
- DevTools window (detached)
- Dev URLs (localhost:5173 for frontend, localhost:8765 for backend)
- Verbose logging
- No kiosk / fullscreen lock

Set both to `false` before deploying to a device.

---

## First launch

On first boot the splash screen appears, then the **Home** screen.

- **Home** — shows your recently played games and quick-launch apps
- **Library** — full list of all systems and their ROMs, navigate with the D-pad

If no ROMs are found, the library will be empty until you add some (see below).

---

## Adding ROMs

ROMs are stored in `emu/<system_id>/` folders (e.g. `emu/melonds/`, `emu/gamecube/`).

**Option 1 — ROM Manager addon (recommended)**  
The ROM Manager ships as an addon (installed by default by the installer —
or run `gamecore-addon install rom-manager`). From any device on the same
network, open a browser and go to:
```
https://<device-ip>:8443/roms/
```
The addon itself listens only on `127.0.0.1:8770` — every LAN request goes
through Caddy on `:8443`, which asks for the shared password first. `:8770` is
not reachable from another machine.
Select a system in the left sidebar, then drag & drop your ROM files.  
They are uploaded directly to the correct folder on the device.

**Option 2 — Copy directly**  
Copy ROM files into the matching `emu/<system_id>/` folder via USB or SSH.

**Supported formats per system:**

The scanner filters strictly on this list — a format that is not declared is not
shown, with no message. `config/systems.json` is the source of truth and is
regenerated from `install/generated/systems.json.dist` on every install;
`backend/tests/test_systems_extensions.py` pins this table to it.

| System | Folder | Extensions |
|--------|--------|-----------|
| Nintendo GameCube | `emu/gamecube/` | `.iso` `.gcm` `.rvz` `.zip` |
| Nintendo Wii | `emu/wii/` | `.iso` `.rvz` `.wbfs` `.wad` `.zip` |
| PlayStation | `emu/duckstation/` | `.bin` `.iso` `.img` `.cue` `.chd` `.pbp` `.zip` |
| PlayStation 2 | `emu/pcsx2/` | `.iso` `.bin` `.chd` `.zip` |
| PlayStation 3 | `emu/rpcs3/` | disc-game **folders** (scanned as directories). Updates/DLC are `.pkg`, installed via the RPCS3 manager addon |
| PlayStation 4 | `emu/shadps4/` | game **folders** (scanned as directories) |
| PlayStation Portable | `emu/ppsspp/` | `.iso` `.cso` `.pbp` `.zip` |
| Wii U | `emu/cemu/` | `.wux` `.rpx` `.iso` `.zip` |
| Nintendo Switch | `emu/switch/` | `.xci` `.nsp` |
| Nintendo 3DS | `emu/azahar/` | `.3ds` `.cia` `.zip` |
| Nintendo DS | `emu/melonds/` | `.nds` `.zip` |
| Game Boy | `emu/gb/` | `.gb` `.zip` |
| Game Boy Color | `emu/gbc/` | `.gbc` `.zip` |
| Game Boy Advance | `emu/gba/` | `.gba` `.zip` |
| Nintendo 64 | `emu/gopher64/` | `.n64` `.z64` `.v64` `.zip` |
| Xbox 360 | `emu/xenia/` | `.iso` `.xex` |
| Super Nintendo | `emu/snes9x/` | `.sfc` `.smc` `.fig` `.swc` `.jma` `.zip` `.gd3` `.gz` `.bs` |
| Nintendo Entertainment System | `emu/nes/` | `.nes` `.unf` `.unif` |
| Family Computer Disk System | `emu/fds/` | `.fds` |
| Sega Master System | `emu/mastersystem/` | `.sms` `.bms` |
| Sega Game Gear | `emu/gamegear/` | `.gg` |
| Sega SG-1000 | `emu/sg1000/` | `.sg` `.sgd` |
| Sega Mega Drive / Genesis | `emu/megadrive/` | `.md` `.mdx` `.smd` `.gen` `.68k` `.bin` |
| Sega Mega-CD / Sega CD | `emu/megacd/` | `.cue` `.iso` `.chd` `.m3u` |
| Sega Mega Drive 32X | `emu/sega32x/` | `.32x` |
| Sega Saturn | `emu/saturn/` | `.ccd` `.chd` `.cue` `.iso` `.mds` `.m3u` |
| Sega Dreamcast | `emu/dreamcast/` | `.chd` `.cdi` `.cue` `.gdi` `.m3u` |
| Sega Naomi | `emu/naomi/` | `.zip` `.7z` |
| Sega Naomi GD-ROM | `emu/naomigd/` | `.zip` `.7z` |
| Sammy Atomiswave | `emu/atomiswave/` | `.zip` `.7z` |
| NEC PC Engine / TurboGrafx-16 | `emu/pcengine/` | `.pce` |
| NEC PC Engine CD / TurboGrafx-CD | `emu/pcenginecd/` | `.cue` `.ccd` `.chd` `.toc` `.m3u` |
| NEC PC Engine SuperGrafx | `emu/supergrafx/` | `.sgx` |
| Arcade (MAME) | `emu/mame/` | `.zip` `.7z` `.cmd` |
| PC (Lutris) | `emu/lutris/` | `.lutris` — written by GameCore, one per game installed in Lutris; nothing to copy here |

> A multi-track PS1 dump is `Game.cue` plus its `Game (Track NN).bin` files.
> Launch the **`.cue`** — it is the one that knows about the other tracks.
> Starting `Track 01.bin` on its own boots the game without its CD audio.

### PC games (Lutris)

Tick **PC** in the installer (or install it from Settings → Catalog). That brings
Lutris (Flatpak), gamemode, and within a minute of the next login the latest
GE-Proton, set as Lutris's default with DXVK, VKD3D, esync, fsync and gamemode on.

Games are installed in Lutris itself, which needs a mouse and keyboard: Settings →
Desktop, open **Lutris** from the menu, install the game (a GOG offline installer,
itch.io, Epic or GOG through Lutris's sources, a disc), then come back to GameCore.
The first time Lutris opens it downloads its own runtime (umu, DXVK); let it
finish. New games install to `emu/lutris-games/` unless you choose another folder.

Every game installed in Lutris appears under **PC** the next time you open it,
and disappears when you remove it in Lutris; games you hide in Lutris stay
hidden. A game starts with A, without the Lutris window, and GameCore gets the
screen back when you quit it. The files in `emu/lutris/` are written by
GameCore; there is nothing to copy there. On the Shelf theme, the back of a PC
game's box shows its minimum system requirements, read from the Steam store.

During a PC game, each PlayStation pad (and any other pad that is not an Xbox
one) is shown to the game as an Xbox 360 pad, so games that only accept Xbox
pads work; the game shows Xbox buttons. A game that supports PlayStation pads
itself can get them back: in Lutris, the game's Configure → System options →
Environment variables, add `GAMECORE_XBOX_PAD` = `0`.

---

## Controller navigation

GameCore is designed for full gamepad control — no mouse or keyboard needed.

| Button | Action |
|--------|--------|
| D-pad / Left stick | Navigate menus |
| A / Cross | Confirm / Launch game |
| B / Circle | Back |
| Start / Options | Open Settings; on a game in the library, open that game's options (per-game emulator settings) |
| Guide / PS, twice | In a game: suspend it and return to GameCore. In the menu: manage suspended games |
| Select / Share | Open Power menu, which also lists suspended games |

L2 and R2 belong to the theme (Shelf turns and restacks its boxes with them);
the buttons above do the same thing on every theme.

A pad whose Guide button never reaches the box can hold **Start + Select** for
a second to leave a game, and reach suspended games from the Power menu.

### Nintendo DS with friends

Start a DS game with two to four controllers connected and each player gets
their own DS, side by side on the TV. Every DS starts on its menu: pick the
game on each DS for games where everyone has a cart, or **DS Download Play**
on the others when the game shares from one cart (Mario Party DS). Each
controller drives its own DS. The touch screen is the mouse. With one mouse,
one cursor touches any DS; with two or more, each mouse gets its own
arrow and touches only its player's DS (the first mouse used is player 1's).
With one controller nothing changes.

---

## Settings & Wi-Fi

Open Settings from the top-right icon or press **Start** on the controller.

- **Wi-Fi** — turn Wi-Fi on or off, scan and connect to networks. Turning it
  off with no cable plugged in takes the box offline, so it asks for a second press
- **Audio** — volume control
- **Bluetooth** — pair controllers
- **Themes** — change the look of the whole UI
- **Update** — check for and apply OTA updates
- **System** — reboot / shutdown
- **Profiles** — who plays on this box: add, rename, recolour, delete, switch

**Profiles.** A box starts with no profiles and behaves exactly as before:
controllers are P1 to P4. **Settings → Profiles → Set up profiles** names the
first one, which keeps the saves the box already had, beside the ROMs; from
then on a controller added to a profile (its page → Controllers) is shown by
that profile's name (status bar, controller screen, notifications), whoever is
playing; the others stay P1 to P4. Others can be added. With two or more,
the console asks "Who's using this controller?" each time the interface
starts, as a PS5 does; **○** keeps the last one, and **Log in automatically**
(Settings → Profiles) skips the question and starts as the last profile. A profile is a name, a colour and a picture, shown at the top
right of every theme beside Settings and Power (press it to open Profiles), with its own saves, playtime,
recently played and favourites. Saves follow the profile playing (player 1)
on every system but the Xbox 360: the first profile keeps the saves the box
already had, every other one starts its own. GameCore points each emulator
at the profile's folder before the game and back afterwards; nothing is ever
moved or copied. The profile cannot change while a game is open or
suspended: close it first. Deleting a profile keeps its saves on disk, but no
profile opens them again.

**Logs.** Each game launch keeps the emulator's output, and the backend keeps
its own log, under `logs/` in the data folder (`/userdata/logs` on a box set up
that way): `logs/launch/<system>/` holds the last 10 launches per system,
`logs/controllers/`, `logs/media/`, `logs/session/`, `logs/network/`, `logs/ota/`,
`logs/addons/` and `logs/ui/` (interface errors) one area each, `logs/packs/` what
a pack script writes, and `logs/backend/backend.log` every warning and error. Read them when a game will not start or
crashes. **Settings → System → Logs** shows their size and purges them, after a
second press to confirm.

---

## Themes

Five themes ship with the box. Same screens, same pad controls, different looks.

| Default: a quiet dark console | Orbit: night blue, one light on the focus |
|---|---|
| ![Default theme home](docs/img/themes/default-home.webp) | ![Orbit theme home](docs/img/themes/orbit-home.webp) |
| ![Default theme controller screen](docs/img/themes/default-controller.webp) | ![Orbit theme controller screen](docs/img/themes/orbit-controller.webp) |

| Shelf: boxed games on a papered wall | Summer: a live beach at the real hour |
|---|---|
| ![Shelf theme home](docs/img/themes/shelf-home.webp) | ![Summer theme home](docs/img/themes/summer-home.webp) |
| ![Shelf theme controller screen](docs/img/themes/shelf-controller.webp) | ![Summer theme controller screen](docs/img/themes/summer-controller.webp) |

| Jelly: a cyan playground in jelly tiles |
|---|
| ![Jelly theme consoles](docs/img/themes/jelly-home.webp) |
| ![Jelly theme controller screen](docs/img/themes/jelly-controller.webp) |

In Jelly, focus a game in Collection and press **Options** to open its game options. On Play and Consoles, Options opens Settings. The game options menu is also available in a console's library.

Picking a theme swaps the frontend. Drop a folder in `config/themes/`, select it
in **Settings → Themes**, and the launcher is redrawn — boot animation,
dashboard, library, menus, the lot.

```
config/themes/my-theme/
  theme.json        manifest: id, version, api, provides
  index.js          entry — a native ES module, no build step
  theme.css         your own styles
  views/  lib/      one feature per file
```

A theme is **all or nothing**: it must provide both surfaces, `splash` and
`shell`. Anything less does not load — the picker says so and the default UI
runs whole. There is no half-themed state to debug.

It changes the UI, never the behaviour. Paging, focus, sorting, search,
launching, the shutdown confirmation and the live controller diagram stay with
the launcher and are handed to a theme as data. A themed dashboard cannot
navigate differently from the default one, because it has no code that could.

If a theme crashes, the default frontend takes over and the crash is recorded;
after the second crash the theme is refused at boot, with the reason in Settings
(`CRASH_LIMIT` in `frontend/src/lib/themeSafety.ts`).
**Holding L1 + R1 for 2s in the menu** forces the default theme back — the way out
of a theme that makes the UI unusable.

An update installs themes it finds missing and **never touches a theme already
on the box** — edit a bundled one and your edits survive. The flip side: to pick
up a new version of a bundled theme, delete its folder and update again. Your
selection survives updates and reboots either way.

Start from `config/themes/_skeleton` — copy it, drop the leading underscore
(that prefix marks a template and keeps it out of the picker), and it loads.

**Full contract:** [`docs/themes/README.md`](docs/themes/README.md) —
surfaces, the SDK, the safety model, performance budget.
**Writing one with an AI:** [`docs/themes/PROMPTS.md`](docs/themes/PROMPTS.md).

---

## Addons

Optional modules live in [p4v1c/gamecore-addons](https://github.com/p4v1c/gamecore-addons)
and are managed with one command:

```
gamecore-addon install <name>     # e.g. rom-manager
gamecore-addon list
gamecore-addon update
gamecore-addon remove <name>
```

Each addon runs as its own service on its own port (8770-8799) and shows a
shared nav bar linking every installed web addon — it all feels like one site.
The core only exposes the registry (`GET /api/addons`) and a WebSocket relay
(`POST /api/addons/notify`); it knows nothing about addon internals.

---

## Cover art and game media

Covers are resolved per game through a tiered pipeline — each tier is only
tried if the previous one found nothing:

1. **Local cache / manual override** — `emu/covers/<system>/<name>.png|.jpg`.
   Drop an image there to force a cover for a game.
2. **Icon embedded in the game itself** (offline, always exact):
   PS3 folders (`PS3_GAME/ICON0.PNG`), PS4 folders (`sce_sys/icon0.png`),
   PSP ISOs (`PSP_GAME/ICON0.PNG` read straight out of the ISO).
   Titles shown in the library also come from the game's `PARAM.SFO`
   for PS3/PS4, so serial-named folders display their real name.
3. **ScreenScraper / LaunchBox** — the ROM's **file hash** (CRC32+MD5+SHA1)
   when it is a real file, so a renamed or mistagged cartridge still lands on
   the right game; the `PARAM.SFO` title for PS3/PS4/PSP folders; the filename
   otherwise. This is also where the other 53 media types come from — see
   below. Needs credentials (or the offline LaunchBox index); skipped
   entirely when neither is configured.
4. **Exact disc-ID lookup** — the ID is read from the image header
   (GameCube/Wii `.iso/.gcm/.rvz`) or from `SYSTEM.CNF` inside the disc
   (PS1/PS2), then fetched from **GameTDB** (GC/Wii/PS3) or the
   **xlenore psx/ps2 cover repos**. No filename guessing involved.
5. **Name-based scraping** — the **libretro thumbnails CDN**, then
   **TheGamesDB** (needs an API key, see below).

Failed lookups are cached for a week (`.miss` files) so browsing the
library stays fast offline. Append `?refresh=1` to a
`/api/covers/<system>/<file>` request to force a re-resolve (e.g. after
renaming a ROM or getting internet back).

### Beyond the jacket — `/api/media`

A game has up to **54 media types**: 3D box, clear logo, gameplay screenshot,
title screen, ready-made mixes, trailer, manual, cartridge art, bezels.

```bash
curl 'http://127.0.0.1:8765/api/media/rpcs3/BCES00509' | jq .media
# → { "box-3d": {"category":"box","kind":"image","region":"eu","cached":false}, … }

# and the file itself
curl -o box3d.png 'http://127.0.0.1:8765/api/media/rpcs3/BCES00509/media/box-3d'
```

**The default themes are unaffected**: they draw the jacket, from `/api/covers`,
exactly as before. This exists so a theme *can* be built on 3D boxes or
screenshots — see [`docs/themes/README.md` §7.1](docs/themes/README.md).

Nothing is downloaded before it is asked for. A scrape fetches the cover and
records the rest; the first request for a 3D box is one HTTP call and costs no
ScreenScraper quota.

### Enable ScreenScraper (best identification)

ScreenScraper needs **two accounts**, and they are not the same thing:

| | |
|---|---|
| **developer** | asked for on the [ScreenScraper forum](https://www.screenscraper.fr), granted per piece of software. The dev id is the **pseudonym**, not the number in the `devinfos.php` URL. Without it: `403` |
| **member** | your own account on screenscraper.fr. It carries the daily quota and the number of threads you may use |

The installer asks for both. To add them afterwards:

```bash
sudo systemctl edit gamecore-backend.service
```

```
[Service]
Environment="SCREENSCRAPER_DEV_ID=your_dev_pseudonym"
Environment="SCREENSCRAPER_DEV_PASSWORD=your_dev_password"
Environment="SCREENSCRAPER_USER=your_member_login"
Environment="SCREENSCRAPER_PASSWORD=your_member_password"
```

```bash
sudo systemctl restart gamecore-backend.service
```

> The developer credential is **shared by every box running GameCore**. That is
> why requests are spaced 1.2 s apart and never committed to the repo: exceeding
> the rate gets the id blacklisted for everyone, not just you.

**Or no account at all**: LaunchBox publishes its metadata dump, 185 000 games
with 172 000 synopses, and it works offline once indexed.

```bash
cd /opt/GameCore && .venv/bin/python backend/services/gamemedia/gamescrape.py --refresh
```

That downloads 106 MB, indexes for about 25 s and leaves a 234 MB SQLite file in
`emu/gamescrape/`. The backend never builds it on its own — 106 MB of download
from inside an HTTP handler would block the request for minutes.

### Enable TheGamesDB (recommended)

1. Register for a free API key at **https://thegamesdb.net**
2. Add the key to the backend service:

```bash
sudo systemctl edit gamecore-backend.service
```

In the editor, add:

```
[Service]
Environment=THEGAMESDB_API_KEY=your_key_here
```

Then restart:

```bash
sudo systemctl restart gamecore-backend.service
```

TheGamesDB covers PS3, Switch, Nintendo 64, DS, GBA, PSP, PS1, PS2, GameCube, Wii U, and 3DS.
If no key is set, the scraper silently falls back to libretro only.

---

## Standby

After a configurable idle time (Settings → Standby), GameCore shows a
screensaver (Orbit and Shelf: a room at night where a CRT plays short videos
of the games you play most; Jelly: your covers floating in jellies on the cyan
floor, one of them growing now and then to play its game's video; other
themes: a cover-art slideshow), then turns
the screen off via DPMS and drops the
CPU governor to powersave. The box itself stays up: backend, SSH and OTA
updates keep working. **Any controller button wakes it** (evdev-based, so
it works even with the UI asleep); mouse/keyboard input works too. A
running game always blocks standby.

The standby videos come from ScreenScraper, so they need its account (the same
one that brings the 3D boxes). The backend downloads them in the background,
the most played games first, never while a game is running, and keeps at most
5 GB; `GAMECORE_STANDBY_VIDEO_CAP_GB=2` in the backend's environment lowers
that, `0` turns the videos off and deletes them.

Governor switching is optional and needs a sudoers rule (the screen is
the real power sink — skip this if you don't care):

```
# /etc/sudoers.d/gamecore-standby
your_user ALL=(root) NOPASSWD: /usr/bin/cpupower frequency-set -g powersave, /usr/bin/cpupower frequency-set -g performance
```

### Rest mode

The Power menu opens on **Rest mode**: it closes every game and app (the
suspended ones too), then puts the box to sleep. Press the PS / Guide button on
a paired Bluetooth pad, or a button on a USB pad, to wake it: the theme's boot
animation plays with its sound and you land on Home. **Turn off**, **Restart**
and **Return to desktop** are unchanged.

While the box sleeps and wakes the screen stays black, so the TV never shows
the old picture; the animation starts once the TV has the signal back. TVs
differ: if yours still shows "no signal" at the start of the animation, raise
Settings → System → **Wait for the TV after waking**.

A short press of the box's power button does the same (a long press still
forces it off). Games are closed before *any* sleep, whatever started it.

Whether a pad can wake the box depends on the hardware (the Bluetooth adapter
and the board's USB wake support). GameCore arms the adapter, USB pads and
paired Bluetooth pads by itself, at every boot and after an update.

---

## OTA updates

Via the UI: **Settings → Update → Check for update → Install**

Or manually on the device:
```bash
bash update/linux.sh
```

The update pulls the latest release from GitHub, replaces app files in place (preserving ROMs, `config/`, and emulators), rebuilds the frontend, then restarts the services through a detached `gamecore-restart.service` unit. That last step needs a one-time root setup:

```bash
sudo install/steps/setup-update-permissions.sh
```

This installs the restart unit and a sudoers rule allowing the GameCore user to start **only** that unit — the update itself runs unprivileged, from the UI, with progress streamed to the settings screen.

---

## Adding an emulator or an app

**One directory is one system or one application.** Everything it needs lives in
`catalog/<id>/`, and nothing about it is written anywhere else — not in
`install/arch.sh`, not in the installer wizard, not in the tile catalogues.
Adding one is dropping a directory; removing one is `rm -rf`.

```
catalog/myemu/
├── pack.json        the declaration — the only required file
├── logo.png         the tile
├── seed/            curated config, deployed to the emulator's config dir
├── generator.py     controller bindings (optional)
└── tests/           this pack's own tests, run by CI with the rest
```

A minimal emulator:

```json
{
  "id": "myemu",
  "kind": "emulator",
  "label": "Some Console",
  "emulatorName": "MyEmu",
  "platform": "SOMECONSOLE",
  "family": "Sega",
  "color": "#1e90ff",
  "order": 13,
  "install": { "provider": "flatpak", "appIds": ["org.example.MyEmu"] },
  "launch": { "path": "flatpak", "args": "run @APPID@ --fullscreen" },
  "roms": { "dir": "emu/myemu", "extensions": ["*.bin", "*.zip"] }
}
```

Then, from the repository root:

```bash
python3 scripts/check-catalog.py    # validate against the schema
python3 scripts/gen-catalog.py      # regenerate the three derived files
git add catalog/myemu install/
```

**`gen-catalog.py` is not optional.** Three committed files are generated from
the packs: `install/generated/systems.json.dist` and `install/generated/apps.json.dist` (the tile
catalogues the installer copies into `config/`) and
`install/installer-gui/catalog_data.py` (the wizard's tick-box list — the wizard
is a standalone binary that runs *before* the repository is on the machine, so
its list is baked in at build time). Skip it and your pack validates, appears in
no tick box, is never selected, and never installs. CI runs
`gen-catalog.py --check` and fails the build if the committed copies are stale.

An **app** is the same file with `"kind": "app"`, plus whatever it needs beside
it — `sources` for git checkouts, `files` for configs (with `@HOME@` and secret
tokens), `services` for a systemd user unit, `postInstall` for the steps that do
not reduce to data. `catalog/twitch/` is the worked example: it installs EmberTV
end to end, including generating a TLS certificate and trusting it in a Firefox
profile.

Emulators that are not on Flathub are just a different `install` provider —
`github-asset` for an AppImage (DuckStation), `github-archive` for a zip
(Xenia). Both carry checksum, magic-byte and retry protections for free.

Full reference: [`docs/architecture/10-catalog-and-install.md`](docs/architecture/10-catalog-and-install.md).

---

## Living-room box setup

How the reference box is wired together. GameCore runs from `/opt/GameCore` with two **system** units:

| Unit | Role |
|---|---|
| `gamecore-backend.service` | FastAPI backend (uvicorn, port **8765**). `Environment=GAMECORE_PATH=/opt/GameCore`. Scraper credentials — the TheGamesDB key and the four ScreenScraper values — live in a local drop-in (`systemctl edit gamecore-backend`, mode 600), never in the repo. |
| `gamecore-ui.service` | Electron shell (`electron/start-ui.sh`), started after the display manager. |

Two companion projects handle TV input and Twitch. **A `--full` install sets both up
automatically** — it clones them, installs their user services, and prompts for the
Twitch Client ID/Secret, the TheGamesDB API key and the ScreenScraper credentials
(secrets are written to local files/systemd drop-ins only, never to git; leave empty
for demo mode and name-based covers). It also creates
the Firefox kiosk profiles for the YouTube/Twitch tiles and installs Stremio. The only
manual step left after a full install is copying BIOS/firmwares (PS1/PS2/PS3, DS/3DS,
Switch keys) — those can't be distributed.

For reference, what the installer wires up:

- **[gamepad-tv-bridge](https://github.com/p4v1c/gamepad-tv-bridge)** — daemon translating gamepad input to keyboard events for apps that don't speak gamepad (Firefox kiosk, EmberTV…). Cloned in `/opt/gamepad-tv-bridge`, installed editable in `~/.venv` (`pip install -e .`), runs as the **user** unit `gamepad-tv-bridge.service` (`WantedBy=default.target`, so linger starts it at boot rather than at graphical login). Per-app YAML profiles in `profiles/` (window-title matching). It is wired by `arch.sh`, not by a pack: it serves both the YouTube and the Twitch kiosk, so it belongs to neither.
- **[Twitch-TV / EmberTV](https://github.com/p4v1c/Twitch-TV)** — controller-first Twitch client, installed entirely from `catalog/twitch/`: the checkout in `/opt/Twitch-TV` (`sources`), `config.json` rendered from a template with your Client ID/Secret — or a demo config when you leave them empty (`files`), the **user** unit `embertv.service` on HTTPS **8097** (`services`), then the TLS certificate generated and trusted in the Firefox kiosk profile's NSS database (`postInstall`). GameCore's Twitch tile opens `https://localhost:8097`.

`"gamepadTrigger": true` in `config/apps.json` re-triggered udev for Flatpak apps on Linux; the Windows edition ignores it.

The Stremio tile runs the **official Flatpak desktop client**, unmodified. All it lacked for couch use was an on-screen keyboard, so a small local proxy serves Stremio's own web interface with a keyboard script injected — no fork, no browser kiosk, no host-side node or `ffmpeg` transcoding. See [`docs/STREMIO.md`](docs/STREMIO.md).

---

## Project structure

```
backend/          FastAPI — systems, games, playtime, covers, settings, OTA
  routers/        API endpoints (systems, games, covers, media, addons, sysinfo…)
  services/       Process manager, gamepad monitor, scrapers
    gamemedia/    ScreenScraper + LaunchBox — hash matching, 54 media types
                  (two vendored stdlib-only files + the adapter; see
                  VENDORED.md there before editing them)
  data/           gamecontrollerdb.txt (vendored SDL mappings)
frontend/         React + Vite + Framer Motion + Zustand
  src/
    components/   UI components (HomeScreen, LibraryScreen, modals…)
    hooks/        useWebSocket, useGamepad, useTheme
    lib/          themeLoader, themeSdk, sounds, formatting helpers
    store/        Zustand store (screen, selection, modal depth, session)
electron/         Electron kiosk shell + HUD toast window
config/           never touched by OTA. Two kinds of file live here:
                  · versioned catalogues, regenerated from install/generated/*.dist on
                    every install — systems.json, apps.json
                  · per-box state, never in git — auth.json, auth_secret,
                    addons.json, standby.json, theme.json, session.json,
                    playtime.db
  themes/         installed themes — an update adds ones you do not have,
                  and never touches ones you do
assets/           logos/
emu/              ROMs per system (emu/gamecube/, emu/melonds/…), covers/ cache,
                  gamemedia/ manifests + artwork, gamescrape/ LaunchBox index
catalog/          THE source of truth — one directory per emulator or app:
                  pack.json (declaration), logo.png, seed/ (curated config),
                  generator.py (controller bindings), files/ + steps/ (what the
                  install writes and runs), tests/. See docs/architecture/10.
  _schema/        pack.schema.json — what check-catalog.py validates against
install/          arch.sh (the engine, + --unattended) and uninstall.sh at the
                  root; everything else grouped by what it IS:
  bin/            installed into /usr/local/bin — gamecore-addon, -emu,
                  -launcher, -session-select, -xsetup
  system/         installed into /etc — Caddyfile, gamecore-restart.service
  steps/          called by arch.sh, never installed on the box
  generated/      apps.json.dist, systems.json.dist — written by
                  scripts/gen-catalog.py, never hand-edited
  installer-gui/  the Qt wizard + its PyInstaller .spec
scripts/          catalog-query.py, gamecore-provider.py, gen-catalog.py,
                  check-catalog.py, check-install.sh
update/           OTA update script (linux.sh)
docs/             architecture/ (10-part deep dive), themes/ (contract + prompts),
                  SECURITY.md, TESTING.md, CONTROLLER_MODELS.md, STREMIO.md,
                  STREAMING.md (assessed, not shipped — why a Moonlight host is
                  not an addon)
```

> **Working on the code?** Start at [`docs/architecture/`](docs/architecture/) —
> runtime topology, sequence diagrams for every flow, a function-by-function
> reference of the backend and frontend, the controller pipeline, and the
> invariants that are easy to break.
