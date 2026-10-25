# GameCore for Windows

A game console front-end for a PC under the TV.
React interface in an Electron shell, a FastAPI backend: plug in a controller and play.

![GameCore in the Orbit theme: a Nintendo 3DS picked in a row of consoles](docs/img/themes/orbit-home.webp)

Website: [p4v1c.github.io/GamecoreWindows](https://p4v1c.github.io/GamecoreWindows/)

This is the Windows edition of [GameCore](https://github.com/p4v1c/GamecoreRenew),
which runs on a Linux box. Same interface, themes, profiles and library; the
parts that talk to the operating system were rewritten for Windows 10 and 11.
Releases are numbered `v0.x` until the emulator packs are back (see
[Not on Windows yet](#not-on-windows-yet)).

---

> **Licence**: GPL-3.0-or-later, see [`LICENSE`](LICENSE).
> **Contributing**: [`CONTRIBUTING.md`](CONTRIBUTING.md).
> **Updating a PC**: [`CHANGELOG.md`](CHANGELOG.md) lists what needs your hand.

## Table of contents

1. [What it does](#what-it-does)
2. [Not on Windows yet](#not-on-windows-yet)
3. [Requirements](#requirements)
4. [Installation](#installation)
5. [Moving from the Linux box](#moving-from-the-linux-box)
6. [Uninstallation](#uninstallation)
7. [Development setup](#development-setup)
8. [First launch](#first-launch)
9. [Adding ROMs](#adding-roms)
10. [Controller navigation](#controller-navigation)
11. [Settings](#settings)
12. [Themes](#themes)
13. [Addons](#addons)
14. [Cover art and game media](#cover-art-and-game-media)
15. [Standby and Rest mode](#standby)
16. [Updates](#updates)
17. [Adding an emulator or an app](#adding-an-emulator-or-an-app)
18. [What runs on the PC](#what-runs-on-the-pc)
19. [Project structure](#project-structure)

---

## What it does

GameCore turns a Windows PC connected to a TV into a console. The PC signs in
by itself, GameCore covers the screen, and everything is done with a
controller: browse the library, launch a game, suspend it with the Guide
button, come back to it later, put the PC to rest.

- Five themes: Default, Orbit, Shelf, Summer, Jelly
- Profiles with their own saves, playtime, recently played and favourites
- Covers, 3D boxes, screenshots, videos and synopses, scraped or read from the game itself
- Standby screens (a CRT room playing your games' videos), Rest mode, wake by controller
- Settings for Wi-Fi, Bluetooth, display, audio, controllers, BIOS, storage and logs
- Web access from a phone or another PC, behind one password
- Updates over the air, with an automatic way back

## Not on Windows yet

| Missing | Why, and what happens instead |
|---|---|
| **Emulator and app packs** | The Linux packs install Flatpaks; each is being rewritten for Windows. Until then no console tile appears and the interface says no system is installed. ROM folders, covers and playtime already work. First one back: the **Steam** tile (install Steam first; launching it closes a Steam already open and reopens it in Big Picture) |
| Multiplayer DS, PC games through Lutris, the Stremio, Twitch and YouTube tiles | Features of those packs; they come back with them |
| The bezel overlay around games | Left out of the port on purpose |
| A graphical installer | The installer is a PowerShell script for now |

## Requirements

| | Minimum |
|---|---|
| OS | Windows 10 version 1809 (build 17763) or Windows 11, 64-bit |
| Account | A local or Microsoft account with administrator rights: GameCore signs it in at boot |
| Display | 1920×1080 |
| RAM | 4 GB |
| Storage | the code takes about 1 GB; your ROM library goes where you choose |
| Controller | Any pad SDL2 opens: Xbox (XInput), DualShock 4, DualSense, Switch Pro, generic HID |

Nothing else to install first: the installer brings its own Python and Caddy.

---

## Installation

> Run this on the PC that will sit under the TV. It changes how that PC
> starts: automatic sign-in, power settings, a logon task. Read the dry run first.

1. Download `gamecore-ota.zip` and `gamecore-ota.zip.sha256` from the
   [latest release](https://github.com/p4v1c/GamecoreWindows/releases/latest).
2. Check the download, then unpack it to a folder of your choice:

   ```powershell
   (Get-FileHash .\gamecore-ota.zip -Algorithm SHA256).Hash   # same as the .sha256 file
   Unblock-File .\gamecore-ota.zip
   Expand-Archive .\gamecore-ota.zip -DestinationPath .\GameCore-setup
   cd .\GameCore-setup
   ```

3. See what it would change, then install:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\install\install.ps1 -DryRun
   powershell -ExecutionPolicy Bypass -File .\install\install.ps1
   ```

   It asks for administrator rights (UAC), where your data should go, a
   password for web access, and your Windows password for automatic sign-in.
4. Restart the PC. It signs in and GameCore starts.

What the installer does, one step per file in `install\steps\`:

- copies the code to `C:\Program Files\GameCore` (`-CodeRoot` to change it)
- installs Python 3.12 and the backend's packages there
- prepares the data folder, `Documents\GameCore` by default (`-DataRoot D:\GameCoreData`
  for another drive; avoid a folder OneDrive syncs)
- writes `%ProgramData%\GameCore\session.env`, readable by administrators
  only: both folders, the port and your scraper keys
- installs Caddy as the `GameCoreCaddy` service for web access on port 8443,
  and opens that port (and SSH, if chosen) on private networks only
- installs OpenSSH Server if you want it
- sets the power options a console needs, in every power plan: power button =
  sleep, no wake timers, no password on wake, Fast Startup off
- signs your account in at boot (the password is stored as an LSA secret, never in a file;
  a Microsoft account needs its password, a PIN cannot sign in automatically)
- registers the `\GameCore\Session` logon task and a `GameCore` shortcut
- makes GameCore your account's Windows shell in place of Explorer: the PC
  boots straight into GameCore, as the Linux box does (`KIOSK=0` keeps Explorer)
- quiets the boot: no Windows logo or spinner, no sign-in animation or
  status text (`QUIET_BOOT=0` keeps them). Your PC maker's logo is drawn by the
  firmware: turn it off in the BIOS/UEFI setup if it offers that
- downloads the offline game database (LaunchBox, 234 MB)

Re-running it is safe: it changes only what differs. Scripted install:
copy `install\install.conf.example` to `install.conf`, fill it in, then
`.\install\install.ps1 -ConfigFile install.conf -Unattended` (automatic
sign-in is then left for you: `.\install\install.ps1 -Only configure-autologon`).
From a clone of this repository instead of a release, the installer also
builds the interface and the shell, which needs Node.js 22 and npm.

**Return to desktop** in the Power menu opens the Windows desktop (Explorer,
taskbar and all); the `GameCore` shortcut brings GameCore back and closes
the desktop. If GameCore ever shows nothing: Ctrl+Alt+Del, Task Manager,
Run new task, and type
`"C:\Program Files\GameCore\.venv\Scripts\pythonw.exe" "C:\Program Files\GameCore\install\bin\gamecore-shell" --desktop`.
Apps that start with Windows (Startup folder, Steam's autostart) start only
with the desktop.

---

## Moving from the Linux box

The Linux edition keeps everything under `/userdata`, in the same layout this
edition reads. Plug in (or mount) the disk that holds it, then from the
installed GameCore:

```powershell
$py = 'C:\Program Files\GameCore\.venv\Scripts\python.exe'
& $py 'C:\Program Files\GameCore\scripts\import-linux-data.py' E:\userdata --to D:\GameCoreData          # the plan
& $py 'C:\Program Files\GameCore\scripts\import-linux-data.py' E:\userdata --to D:\GameCoreData --apply  # the copy
```

It copies ROMs and their saves, covers, scraped media, the offline database,
profiles and their saves, per-game settings and playtime to the same place in
the new folder. It never changes the source and never overwrites a file that
is already in the destination. What only made sense on Linux (the Flatpak
tiles, the addons' services, controller bindings, logs) goes to
`linux-import\` in the destination instead, so nothing is lost. A file name
Windows refuses is renamed, and a link to another disk is not followed; the
report lists both and is saved in `logs\import\`.

Then point GameCore at the new folder: `.\install\install.ps1 -DataRoot D:\GameCoreData`
(or run the import before installing and give the same folder to the installer).
A Linux box still on the old `dolphin` or `mgba` tile should run
`scripts/split-systems.py` there first
([how](docs/architecture/07-config-and-data.md#splitting-a-system--scriptssplit-systemspy)).

---

## Uninstallation

Remove the addons first (`gamecore-addon remove <name>` for each, see
[Addons](#addons)), then from an elevated PowerShell:

```powershell
& 'C:\Program Files\GameCore\install\uninstall.ps1' -DryRun   # what it would undo
& 'C:\Program Files\GameCore\install\uninstall.ps1'
```

The installer records what it changed in `%ProgramData%\GameCore\install-manifest.json`,
and the uninstaller undoes exactly that, in reverse: the logon task and the
shortcut, automatic sign-in, the power settings (put back to their values
before GameCore), the firewall rules, OpenSSH (only if GameCore installed it),
the Caddy service, `session.env` and the code folder. **Your data folder is
never touched**: ROMs, saves, covers and settings stay. Caddy's certificate
authority stays in `%ProgramData%\GameCore\caddy` so phones that trust it keep
working after a reinstall.

---

## Development setup

> For working on the code: no automatic start, no kiosk.

```powershell
git clone https://github.com/p4v1c/GamecoreWindows.git
cd GamecoreWindows
py -3.12 -m venv .venv
.venv\Scripts\pip install -r backend\requirements.txt pytest
$env:PYTHONUTF8 = '1'      # every Python tool runs in UTF-8 mode, as the installed backend does

# Interface (Vite, http://localhost:5173)
cd frontend; npm install; npm run dev

# Tests: see docs/TESTING.md and CONTRIBUTING.md
.venv\Scripts\python -m pytest backend\tests catalog -m "not network"
```

Run the real backend (`uvicorn backend.main:app --host 127.0.0.1 --port 8765`)
only on a PC you do not mind changing: at start it takes over Windows' screen
and sleep timers and the power plan, as on a console. For interface work use
the read-only dev server (`.claude\skills\gamecore-legibility\scripts\devserve.py`,
port 8766), and start the shell against it with
`$env:GAMECORE_BACKEND_PORT = '8766'; $env:GAMECORE_MANAGED = '1'` then
`npx electron .` in `electron\`.

---

## First launch

On first boot the splash screen appears, then the **Home** screen.

- **Home**: your recently played games and quick-launch apps
- **Library**: every system and its games, navigate with the D-pad

If no ROMs are found, the library stays empty until you add some (see below).

---

## Adding ROMs

ROMs go in `emu\<system_id>\` inside your data folder (for example
`Documents\GameCore\emu\melonds\`).

**Option 1: the ROM Manager addon.** Install it (`gamecore-addon install rom-manager`),
then from any device on the same network open:
```
https://<pc-ip>:8443/roms/
```
The addon itself listens only on `127.0.0.1:8770`; every LAN request goes
through Caddy on `:8443`, which asks for the web password first. Pick a
system, then drag and drop your ROM files.

**Option 2: copy them.** Into the matching `emu\<system_id>\` folder, from a
USB drive or over the network.

**Supported formats per system.** The scanner filters strictly on this list:
a format that is not declared is not shown. The list comes from the catalogue
(`install/generated/systems.json.dist`, pinned to this table by
`backend/tests/test_systems_extensions.py`); on Windows a system appears once
its pack is installed (see [Not on Windows yet](#not-on-windows-yet)).

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
> Launch the **`.cue`**: it knows about the other tracks.

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

---

## Settings

Open Settings from the top-right icon or press **Start** on the controller.

- **Wi-Fi**: turn it on or off, scan and join networks. Turning it off with no
  cable plugged in takes the PC offline, so it asks for a second press
- **Bluetooth**: pair controllers and headsets
- **Display**: resolution and refresh rate, with a revert timer
- **Audio**: output device and volume
- **Controllers**: who is player 1 to 4, battery, button mapping
- **Emulators & apps**, **BIOS**: what is installed and what each system still needs
- **Themes**: change the look of the whole interface
- **Update**: check for and install updates
- **System**: restart, shut down, storage, logs
- **Profiles**: who plays on this PC: add, rename, recolour, delete, switch

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
its own log, under `logs\` in the data folder: `logs/launch/<system>/` holds the last 10 launches per system,
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

Optional modules live in [p4v1c/gamecore-addons-windows](https://github.com/p4v1c/gamecore-addons-windows)
and are managed with one command (from an elevated prompt, or the Addons screen):

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

### Scraper keys

The installer asks for them; to add or change them later, edit
`%ProgramData%\GameCore\session.env` from an elevated Notepad and restart the PC:

```
THEGAMESDB_API_KEY=your_key
SCREENSCRAPER_DEV_ID=your_dev_pseudonym
SCREENSCRAPER_DEV_PASSWORD=your_dev_password
SCREENSCRAPER_USER=your_member_login
SCREENSCRAPER_PASSWORD=your_member_password
```

ScreenScraper needs **two accounts**, and they are not the same thing:

| | |
|---|---|
| **developer** | asked for on the [ScreenScraper forum](https://www.screenscraper.fr), granted per piece of software. The dev id is the **pseudonym**, not the number in the `devinfos.php` URL. Without it: `403` |
| **member** | your own account on screenscraper.fr. It carries the daily quota and the number of threads you may use |

> The developer credential is **shared by every PC running GameCore**. That is
> why requests are spaced 1.2 s apart and never committed to the repo: exceeding
> the rate gets the id blacklisted for everyone.

**Or no account at all**: LaunchBox publishes its metadata dump, 185 000 games
with 172 000 synopses, and it works offline once indexed. The installer builds
it; to build it again: `.\install\install.ps1 -Only build-media-index`.

TheGamesDB (free key at https://thegamesdb.net) covers PS3, Switch, Nintendo 64,
DS, GBA, PSP, PS1, PS2, GameCube, Wii U and 3DS. Without a key the scraper
falls back to libretro only.

---

## Standby

After a configurable idle time (Settings → Standby), GameCore shows a
screensaver (Orbit and Shelf: a room at night where a CRT plays short videos
of the games you play most; Jelly: your covers floating in jellies on the cyan
floor, one of them growing now and then to play its game's video; other
themes: a cover-art slideshow), then turns
the screen off and switches to Windows' Power saver plan when the PC has one.
**Any controller button wakes it** (read by the backend, so it works even
with the interface asleep); mouse and keyboard work too. A running game
always blocks standby. The PC keeps running meanwhile, as the Linux box does:
on a Modern Standby PC, where a display switched off puts the whole PC to
sleep, the screen is left on and black instead.
While standby is on, GameCore sets Windows' own "turn off the display" and
"sleep after" timers to never and gives them back when standby is switched off.

The standby videos come from ScreenScraper, so they need its account (the same
one that brings the 3D boxes). The backend downloads them in the background,
the most played games first, never while a game is running, and keeps at most
5 GB; `GAMECORE_STANDBY_VIDEO_CAP_GB=2` in `session.env` lowers
that, `0` turns the videos off and deletes them.

### Rest mode

The Power menu opens on **Rest mode**: it closes every game and app (the
suspended ones too), then puts the PC to sleep. **Turn off**, **Restart** and
**Return to desktop** are next to it.

How it sleeps depends on the PC:

- **S3 sleep** (most desktops): GameCore sleeps the PC and allows the
  Bluetooth adapter and your pads to wake it, at every start and whenever a
  new pad connects. Press the PS / Guide button on a paired pad to wake it.
- **Modern Standby** (most recent mini PCs and laptops): GameCore closes every
  game, turns the screen black and keeps the PC running, as the Linux box
  does; any button on a connected pad wakes it. Windows is kept from sleeping
  meanwhile (switching the display off would put this kind of PC to sleep),
  so the TV shows black rather than going to its own standby.

After a wake the screen stays black until the TV has the signal back, then
the theme's boot animation plays with its sound and you land on Home. TVs
differ: if yours still shows "no signal" at the start of the animation, raise
Settings → System → **Wait for the TV after waking**. Games are closed before
*any* sleep, whatever started it: the power button, the Start menu or
Windows' own sleep timer. Pads stay on while the PC sleeps: switch them off
by holding the PS / Guide button.

---

## Updates

From the interface: **Settings → Update → Check for update → Install**.

GameCore downloads the newest release of this repository (`gamecore-ota.zip`),
checks its SHA-256, then restarts to swap its program folder for the new one.
Your data folder is not touched. If the new version does not start within
three minutes, the previous one is put back by itself. To go back by hand,
from an elevated PowerShell:

```powershell
& 'C:\Program Files\GameCore\update\apply-update.ps1' -Rollback
```

The outcome of the last update is in `%ProgramData%\GameCore\update\last-update.json`,
the details in `logs\update\update.log` in your data folder. Every change
merged into `main` here is a release; the full story is in
[docs/architecture/13-release-and-ota.md](docs/architecture/13-release-and-ota.md).

---

## Adding an emulator or an app

**One directory is one system or one application.** Everything it needs lives
in `catalog/<id>/`, and nothing about it is written anywhere else: not in the
installer, not in the tile catalogues. Adding one is dropping a directory.

```
catalog/myemu/
├── pack.json        the declaration, the only required file
├── logo.png         the tile
├── seed/            curated config, deployed to the emulator's config folder
├── generator.py     controller bindings (optional)
└── tests/           this pack's own tests, run by CI with the rest
```

Then, from the repository root:

```powershell
.venv\Scripts\python scripts\check-catalog.py    # validate against the schema
.venv\Scripts\python scripts\gen-catalog.py      # regenerate the derived files
```

**`gen-catalog.py` is not optional**: `install/generated/systems.json.dist`
and `apps.json.dist` are generated from the packs, and CI fails when they are
stale. The packs in the tree today are the Linux ones, kept as the model for
the Windows rewrite; their install providers (`flatpak`, `github-asset`,
`github-archive`) are Linux ones, and a Windows provider is still to be added
to the schema. Full reference:
[`docs/architecture/10-catalog-and-install.md`](docs/architecture/10-catalog-and-install.md).

---

## What runs on the PC

| What | Started by | Listens |
|---|---|---|
| `install\bin\gamecore-session`, the supervisor | the `\GameCore\Session` logon task, elevated, in your session | none |
| the backend (FastAPI) | the supervisor, restarted if it crashes | `127.0.0.1:8765` |
| the shell (`shell\GameCore.exe`, Electron) | the supervisor | none |
| a game | the backend, inside a Job Object (the whole process tree) | none |
| each addon | its `\GameCore\Addons\<name>` logon task | `127.0.0.1:8770`-`8799` |
| Caddy | the `GameCoreCaddy` service | `:8443`, the only port open to the LAN |
| OpenSSH (optional) | the `sshd` service | `:22`, private networks only |

Everything but Caddy and SSH binds to loopback: the TV is the trust boundary,
the LAN only sees Caddy, which asks for the web password. Details:
[`docs/architecture/01-runtime-topology.md`](docs/architecture/01-runtime-topology.md)
and [`docs/SECURITY.md`](docs/SECURITY.md).

---

## Project structure

```
backend/          FastAPI: systems, games, playtime, covers, settings, updates
  routers/        API endpoints
  services/       process manager, pads, standby, scrapers
    platform/     every Windows call: jobs, windows, audio, Wi-Fi, Bluetooth,
                  display, power, sleep, pads (SDL2), drives
    gamemedia/    ScreenScraper + LaunchBox (see VENDORED.md there before editing)
  data/           gamecontrollerdb.txt (vendored SDL mappings)
frontend/         React + Vite + Framer Motion + Zustand
electron/         the shell: kiosk window, HUD toasts, Return to desktop
config/themes/    the bundled themes, copied into the data folder at install
catalog/          one directory per emulator or app (the Linux packs, for now)
  _schema/        pack.schema.json, what check-catalog.py validates against
install/          install.ps1, uninstall.ps1, lib\, one step per file in steps\
  bin/            gamecore-session (the supervisor), gamecore-addon (+ .cmd)
  generated/      systems.json.dist, apps.json.dist, written by gen-catalog.py
update/           windows.py (download and check), apply-update.ps1 (swap, rollback), finish.py
scripts/          import-linux-data.py, split-systems.py, package-release.py,
                  check-catalog.py, gen-catalog.py, check-docs.py
docs/             architecture/, themes/, dev-log/, SECURITY.md, TESTING.md
```

The data folder holds `config\` (settings, `playtime.db`, profiles, themes),
`emu\` (ROMs per system, `covers\`, `gamemedia\`, `gamescrape\`,
`profile-saves\`), `addons\` and `logs\`. Updates never touch it.

> **Working on the code?** Start at [`docs/architecture/`](docs/architecture/):
> runtime topology, sequence diagrams for every flow, a function-by-function
> reference of the backend and frontend, the controller pipeline, and the
> invariants that are easy to break. The port's decisions and status are in
> [`docs/dev-log/windows-port.md`](docs/dev-log/windows-port.md).

