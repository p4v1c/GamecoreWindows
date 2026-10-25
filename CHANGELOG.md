# Changelog

**This is not the release log.** Every push to `main` is tagged and published
automatically with generated notes — read those for "what changed":
<https://github.com/p4v1c/GamecoreWindows/releases>. Duplicating them here would
produce a second source of truth that goes stale on the next push, which is the
failure mode this project keeps paying for.

What this file records is the short list the generated notes cannot: **changes
that need something from the operator**, and changes to how the project is put
together. If updating a box requires you to do anything, it is written here.

The format is loosely [Keep a Changelog](https://keepachangelog.com); versions
are the auto-incremented tags.

---

## Unreleased

- Windows edition, Rest mode: a Modern Standby PC now really sleeps (screen
  off, Windows' low-power sleep) instead of staying on with a black screen.
  Wake it with the power button or the keyboard; a pad wakes it only where
  the PC supports it. Bluetooth pads are disconnected before every sleep.
  Nothing to do: the update brings it.

- Windows edition, kiosk boot: re-run the installer (or
  `install.ps1 -Only set-kiosk-shell,configure-boot-visuals`) as administrator,
  then restart. GameCore becomes the account's shell (no Windows desktop
  until Return to desktop), and the Windows boot logo, sign-in animation
  and status text go. `KIOSK=0` / `QUIET_BOOT=0` in `install.conf` keep
  them; `uninstall.ps1` puts both back.

- Windows edition, first pack: the Steam tile. Install Steam yourself; the
  installer (and each update) adds the tile to `config\apps.json`. Launching
  it closes a Steam already open and reopens it in Big Picture inside the
  GameCore session. Packs reach a Windows grid only when marked
  `"windows": true`.

- Windows edition, moving from the Linux box: `scripts\import-linux-data.py
  <linux data root> --to <new data root>` prints the plan; `--apply` copies
  the library, saves, covers, media, profiles and playtime into the layout the
  Windows edition reads, keeps Linux-only files in `linux-import\`, and saves
  a report in `logs\import\`. Then install with `-DataRoot <new data root>`.

- Windows edition, addons: `gamecore-addon` is a Python script now
  (`install\bin\gamecore-addon`, `gamecore-addon.cmd` for a prompt) and
  installs from `p4v1c/gamecore-addons-windows`. Run it from an elevated
  prompt. Each addon becomes a scheduled task `\GameCore\Addons\<name>`;
  before uninstalling GameCore, `gamecore-addon remove` each addon.

- Windows edition, updates: Settings → Update installs this repository's
  releases (`p4v1c/GamecoreWindows`, `gamecore-ota.zip`), never the Linux
  original's. The archive's SHA-256 is checked; GameCore restarts to swap its
  folder and puts the previous version back by itself if the new one does
  not start. By hand, from an elevated PowerShell:
  `& 'C:\Program Files\GameCore\update\apply-update.ps1' -Rollback`.
  The first release, `v0.1.0`, is cut when this change reaches `main`. A box gets updates only once `install\install.ps1` has installed it.
  The Linux updater and its root pieces (`update/linux.sh`, the sudoers
  step, `gamecore-restart` and its unit) are gone.

- Windows edition, Rest mode: the box sleeps through Windows (S3 sleep, or
  the screen switched off on a Modern Standby PC) and closes every game
  before any sleep, power button included. The Linux root pieces are gone
  (the sudoers suspend rule, `gamecore-wake-setup` with its boot unit and
  udev rule, the logind drop-in). GameCore allows the Bluetooth adapter and
  the pads to wake the PC, which needs the elevated backend. Pads are no
  longer switched off before a sleep: Windows cannot drop one device's link.

- Windows edition, devices: the backend reads pads through SDL2 (`SDL2.dll`
  ships in the venv with `pysdl2-dll`), Wi-Fi and Bluetooth through WinRT,
  sound through Core Audio. While standby is on, GameCore sets Windows' own
  "turn off the display" and "sleep after" timers to never and gives them back
  when standby is switched off; the sleep stage uses the Power saver plan when
  the PC has one. On Windows 11 24H2 and later the Wi-Fi scan needs location
  access for desktop apps (port step 7 grants it). Bluetooth Connect cannot
  force a link on Windows: switch the device on instead.

- Windows edition: the bezel overlay is gone (owner's decision), window,
  backend and per-game choice alike. `config/overlays.json`, `assets/overlays/`
  and the `bezel-*.json` caches of an imported Linux box are no longer read;
  they can be deleted. The Electron shell runs on Windows: restart and turn off
  through `shutdown.exe`, Desktop mode starts Explorer, the pointer hides over
  the interface, and the logon task must start it with `GAMECORE_MANAGED=1`
  (port step 7).

- Rest mode and every other sleep turn the Bluetooth pads off first, as a
  console does: an Xbox pad left to the kernel paged back and woke the box at
  once. The pad's home button still wakes it. No operator step.

- Bluetooth mice and keyboards no longer wake the box from Rest mode (BlueZ
  allowed it by default; a mouse did on 15/10). Pads, USB keyboards and the
  power button still do. No operator step.

- Rest mode: the screen stays black from the press to the boot animation (the
  menu used to come back as the box fell asleep, and showed again, half
  black, on the wake), and the backend journal names what woke the box
  (`wake_source: woken by …`). No operator step.

- Waking from Rest mode: the screen stays black until the TV shows a picture
  again, then the boot animation plays from the start. How long to wait once
  the signal is back is in Settings → System → "Wait for the TV after waking"
  (2.5 s by default, up to 8 s). No operator step.

- Rest mode in the Power menu (first, and where the cursor opens): closes every
  game and app, then suspends; a pad wakes it to Home. No operator step: the
  update installs `/etc/sudoers.d/gamecore-rest`, `gamecore-wake.service` and a
  udev rule through `gamecore-session-migrate.service`. If Rest mode answers
  "Couldn’t sleep" after the update, run once (see `journalctl -u gamecore-session-migrate`):
  `sudo bash <install>/install/steps/setup-rest-mode.sh <user>`.
- Every sleep now closes games and apps first (power button, desktop menu,
  idle, `systemctl suspend`), through a logind delay lock. A short press of the
  power button sleeps instead of powering off (`/etc/systemd/logind.conf.d/90-gamecore.conf`,
  also installed by the update; applied by a logind reload, or at the next boot
  on a systemd older than 254). In a Plasma desktop session the button keeps
  Plasma's own setting.

- PC games (Lutris) see every PlayStation pad as an Xbox 360 pad. It needs
  `/dev/uinput`: a box that has neither melonDS nor Azahar installed gets it with
  `sudo gamecore-emu install lutris` (`hostAccess.uinput`); until then pads are
  left as they were. Per game opt-out: `GAMECORE_XBOX_PAD=0` in Lutris.

- **Operator step** for the `bios-manager` addon: Caddy routes `/bios/` to it
  (`127.0.0.1:8773`). An update does not rewrite `/etc/caddy/Caddyfile`; apply the
  shipped one as `update/linux.sh` prints, then `sudo systemctl reload caddy`.

- BIOS page: MAME lists its common BIOS sets (`neogeo.zip`, `qsound.zip`, `pgm.zip`…,
  in `system/mame/bios/`) and PS4 lists the shadPS4 system modules (`sys_modules/*.sprx`).
  GameCube lists Dolphin's IPLs (`GC/<region>/IPL.bin`). All optional: nothing new
  blocks a launch.

- Standby fades instead of cutting: the screensaver and the black screen fade in
  and out on every theme, and the TV switches off only once the picture is black.

- Shelf: a box no longer turns to face you as a blank beige card when its cover is
  slow to arrive; it waits edge-on until the cover is ready, then turns.
- New system: **PC**, your Windows games through Lutris. Installing it adds Lutris
  (Flatpak), gamemode and GE-Proton, and adds the player to the `gamemode` group
  (effective at the next login). Games are installed from Lutris's own window
  (Settings → Desktop); they then appear under PC and start without Lutris
  showing. The default and Summer libraries no longer print an extension for
  them.
- PC (Lutris) games now get their description, developer, genres and artwork
  from the LaunchBox index; the `.lutris` extension was kept in the title and
  every lookup missed. A PC game already scraped as "not found" keeps that
  answer until it is refreshed once:
  `curl 'http://127.0.0.1:8765/api/media/lutris/<file>.lutris?refresh=1'`.
- Shelf: the back of a PC game's box prints its minimum system requirements
  (from the Steam store, matched by exact title) and two gameplay shots. Games
  already scraped get them after one refresh, then a restart of the UI, which
  keeps the media list in memory:
  `curl 'http://127.0.0.1:8765/api/media/lutris/<file>.lutris?refresh=1'`,
  then `systemctl --user restart gamecore-ui`.
- Jelly: with 3D boxes on, a game with no 3D box art (most PC games) is drawn as
  a box instead of showing a flat cover among the boxes.
- Jelly has its own standby screen: your covers float in jellies that bounce
  and wobble on the cyan floor; every half minute or so one grows in the middle
  to show its cover large, and now and then a jelly pops and another game
  wobbles in. No videos: Jelly's standby is the covers alone.
  Theme SDK 12 adds `defaults.useLocalWake` and `format.playedAgo` for themes
  that draw their own standby.
- Orbit and Shelf have a new standby screen: a small CRT in a room at night playing
  short videos of the games you play most, its light on the walls. Videos are
  downloaded in the background (never during a game) from ScreenScraper, up to
  5 GB; set `GAMECORE_STANDBY_VIDEO_CAP_GB` in the backend's environment to
  change that, `0` to keep none. Without ScreenScraper the TV shows screenshots,
  or snow.
- An application left in the background is named as on its tile ("YouTube", not
  "youtube") in the session bar and menu, on every theme.
- Orbit: an application left in the background (YouTube, Steam, Twitch…) shows its
  logo in the session dock and menu, instead of two letters.
- An update that fails half-way (dependencies, console helpers) now puts the previous
  version back by itself and is offered again, instead of leaving a half-updated box.
- A screen that stops reading the box's live updates (a frozen browser tab) no longer
  holds up launches and update progress for every other screen.
- A power cut while game info is being saved no longer leaves that game's entry unreadable.
- Pressing Play twice in a row no longer resets the saves of the game that is starting.
- After a power cut, the box no longer believes an old game is still running, and
  double-PS can no longer kill an unrelated process in its place.
- **The Switch runs Ryujinx (Ryubing) again.** Same tile, same `emu/switch/` games. After
  the update: back up `~/.var/app/dev.eden_emu.eden/`, then install Ryujinx from
  Settings or with `gamecore-emu install switch`. That copies Eden's keys, firmware and
  saves into Ryujinx (`logs/packs/switch/eden-import.log`); each other profile's Eden
  saves are copied at its first Switch launch. Eden's files are only read, and the Eden
  variant stays on the `switch-eden` branch.
- If controller autoconfig was off for Ryujinx, it is off for the Switch: turn it back on
  in Settings.
- A profile's page lists its controllers: add a connected pad, remove one. For display
  only: nothing (saves, login, theme) follows a pad. A pad shows on one profile at most.
- A theme picked in Settings → Themes now stays with the profile playing. A profile
  that never picked one used to take that theme at its first turn; it now starts on
  the look a new box shows (Shelf). A profile already dressed that way keeps it: pick
  its theme again on its page.
- A d-pad move in Settings and on Orbit's home is drawn one frame sooner (about 17 ms).
- No menu sound plays over a game, whichever screen or theme asks for one.
- Jelly opens per-game overlay options with Options on a focused Collection game. On Play and Consoles, Options opens Settings.

### Behaviour that changed on its own

- **Profiles.** Settings → Profiles adds, renames, recolours and deletes the
  people who play on the box. With two or more, "Who's using this
  controller?" opens when the interface starts, unless "Log in automatically"
  is on (`PUT /api/profiles/auto-login`). A box starts with one unnamed profile (created on
  first start in `config/profiles/`) and looks exactly as before; naming it
  ("Set up profiles") starts profiles. A controller added on a profile's page
  is then shown by that profile's name instead of P<n> in every theme, the
  controller screen and notifications, whichever profile is active; the
  others keep their number (`sdk.players`, `profiles:changed`). The profile cannot
  change while a game is open or suspended.
- **Each profile has its own theme and an animal picture.** The theme picked
  in Settings → Themes, or on a profile's page, is the profile's: switching
  profile puts it back on. Pictures are now eleven drawn animals; a profile
  that had one of the old line icons shows its initial until it picks an
  animal.
- **Profiles, redrawn.** Settings → Profiles shows a card per profile (Switch,
  Edit), and Edit opens the profile's own page: picture, colour and theme all
  in view, with the playtime of the profile playing. "Who's using this
  controller?" shows each profile as a card with its theme.
- **Saves per profile, on every emulator but Xenia.** `profileSaves` in
  pack.json, mostly with no code: the save options (`keys`) or the folders
  with no option (`dirs`, swapped for a link) to point at the profile's
  folder; a `place_saves` hook for melonDS (`per-instance`); or
  `{supported: false, why}` (Xenia). RetroArch's 17 packs, mGBA, Snes9x, RMG,
  DuckStation, PCSX2, Dolphin, Azahar, melonDS, PPSSPP, Eden, Ryujinx, RPCS3,
  shadPS4 and Cemu. A
  profile other than the primary one keeps its saves in
  `emu/profile-saves/<profile id>/<system>/`; the primary profile keeps every
  existing save where it is, and nothing is moved. Players 2-4 are unchanged
  until pads carry profiles. When the game ends the emulator's save paths are
  put back, so an emulator started from Desktop Mode saves where it always did.
- **Playtime, recently played and favourites per profile.** The primary
  profile keeps every figure and favourite it had; another profile has its own
  (`profile_playtime`, `sdk.players.storageKey`).

- **Logs you can read without a terminal.** Each launch keeps the emulator's
  output and the backend writes its log under `<data>/logs/`, capped in size:
  one file per area (controllers, media, session, network, updates, addons,
  interface errors) and one for every warning. Settings → System → Logs purges them.

- **DS multiplayer arrows stay on the touch screen, and show on white.** With
  one mouse per player, an arrow no longer goes up onto the top screen (it
  takes no touch). The cursor theme keeps its look, with a dark ring around
  it so a pale stylus reads on a white DS screen.

- **Bluetooth pairing works from GameCore again.** A mouse or a DualSense
  could only be paired from the desktop session; the console's pairing ran
  with no agent to answer the device.

- **DS multiplayer no longer falls back to one fullscreen DS.** With three
  different pads, getting the game ready took longer than the launch allowed,
  and melonDS started solo in fullscreen. melonDS now takes a few seconds
  longer to appear instead.
- **The main pointer really stays out of DS multiplayer.** The previous fix
  did not hide it on the box: it now goes back to the screen corner after
  every touch.

- **L3 only acts in a solo DS game.** In multiplayer it could leave one
  player's window showing the top screen alone; it is now ignored there.
- **No stray system pointer in DS multiplayer.** A player's click no longer
  shows the box's main mouse pointer at the touch point.

- **Players 2-4 start their own DS game.** In multiplayer, a player with no
  save for a game now gets a blank one instead of a copy of player 1's
  progress (Pokemon trading needs two different trainers).

- **Smoother mouse arrows in DS multiplayer, in your cursor theme.** Every
  player's arrow uses the box's own cursor (a custom theme included) and is
  drawn once per frame instead of on every mouse report.
- **DS multiplayer keeps its layout after a suspend.** Coming back to a
  suspended multiplayer game put one DS screen in fullscreen over the others,
  and a bezel could pull one window into its frame.
- **No more battery pill for a mouse.** A Bluetooth mouse's battery showed
  as an extra controller pill, numbered like a player (a second "P2").

- **One mouse per player in DS multiplayer.** With two mice or more, each
  mouse gets its own arrow, stays on its player's DS and touches only
  that DS, so players can draw at the same time. One mouse works as before.

- **Nintendo DS local multiplayer.** With two to four controllers connected, a
  DS game opens one melonDS instance per controller, side by side, each on its
  DS menu, linked by melonDS's own local wireless. One controller launches
  exactly as before. A bezel steps aside while several game windows are open.

- **PS twice in a game always lands on the interface, without the session
  menu.** The menu opened on some suspends and not others, depending on
  whether the browser or the backend saw the presses first. It now opens only
  on PS twice from the interface.

- **The interface no longer zooms and fades in over a suspended game.**
  KWin's window animations are off in the console session, so the theme's own
  handover plays alone at each launch, suspend and resume. Applies from the
  next boot after the update.

- **No more 8 s wait and "not configured in time" notice on the Switch tile
  after Ryujinx is uninstalled.** The superseded `ryujinx` pack now profiles
  controllers only while its old tile is still on the grid.

- **The controller screen draws any pad, by position.** No more PlayStation
  drawing for every controller: the four face buttons are a diamond that
  lights where you press, missing controls (an arcade stick's sticks) are
  dashed, and the screen names the pad, says whether the box recognises it,
  and follows whichever pad you touch. The power menu and this screen have a
  design of their own on Orbit, Shelf and Summer; ← → also move in the power
  menu. Theme SDK 9: `sdk.defaults.GamepadView`, new `gamepadView` props, and
  `GET /api/controllers/pads`. A third-party theme with its own controller
  view keeps working (the old props are still filled).

- **The power menu has no Cancel button and the controller screen no
  peripherals list.** ○ closes the menu (the button could not be reached with a
  pad); Orbit's power menu is a borderless PS5-style panel.

- **Pad buttons moved so no theme can collide with them.** Per-game options
  (bezel) are on **Start / Options** on a library game (was R2); the menu of
  suspended games opens on **PS twice** outside a game (was L2), and from a new
  first row of the Power menu. L2 and R2 now belong to the theme: on Shelf, L2
  only turns the box and R2 only restacks, and Shelf gets the bezel picker it
  never had. Theme SDK 8 reserves `gp:menu` and `gp:power` for the host.

- **Pressing Home twice no longer quits the game — it suspends it.** The game
  freezes exactly where it was, the interface comes back with the controller
  working, and a session bar along the bottom of the screen offers *Resume* or
  *Close*. Closing is now a separate, deliberate action.

  Nothing to do, and nothing is lost: the previous behaviour destroyed an
  unsaved save on two accidental presses, and this one cannot. But anyone used
  to double-Home *ending* a game will press it twice, see the game suspended
  instead, and need to be told once.

  The suspended game keeps its memory, so the number of sessions a box holds at
  once is capped and the launch past it is refused by naming what to close. The
  cap is **read off the box**: two, or three where there is 16 GiB or more. It
  was a flat two, which is one slot for what is on the screen and one for
  everything else — put YouTube in the background and the next game was refused
  with both slots spent. Playtime does not count the time a game spends
  suspended. `docs/reports/orbit-et-sessions-2026-09-08.md` has the owner
  checklist, including the emulators that have **not** yet been tried across a
  freeze.

- **Themes: SDK 5.** `sdk.session` is new, and so is the optional `sessionBar`
  surface. Existing themes keep working untouched — a theme only needs `api: 5`
  if it *calls* `sdk.session`; taking the bar as props does not. See
  `docs/themes/README.md` §5f.

- **"Scan mapping" and "Forget mapping" are gone** — from Settings →
  Controllers, from the power menu, and from the API (`POST`/`DELETE
  /api/controllers/scan-mapping` now answer 404). Mappings they already saved
  under `controller-snapshots/` are kept and still restored on connect. To keep
  a hand-made mapping in one emulator, turn that emulator off under
  Settings → Controllers → *Per-emulator exceptions*; a pad SDL does not know is
  still mapped with the wizard, from the controller screen (□). A saved snapshot
  that names another pad is still refused, and the warning in the journal now
  names the file to delete, since there is no button for it any more. Themes:
  the `powerOmit` shell part and the `scanning` / `scanResult` power-view props
  are removed; a theme that still passes or reads them is unaffected.

### Needs action on an already-installed box

- **Nintendo Switch runs Eden** (`switch` tile). A fresh install gets it
  directly. A box with the **Ryujinx** tile keeps it, and it keeps launching
  Ryujinx; nothing moves on its own. To switch, as the box's user, every game
  closed: `sudo gamecore-emu install switch`, start Eden once and close it,
  copy the keys, firmware and saves (Eden does not read Ryujinx's; one-shot
  helper on branch `tools/ryujinx-to-eden`, not shipped), then move the games
  with `scripts/split-systems.py --data /userdata` (dry run, then `--apply`)
  and restart the backend and the UI. `.zip` dumps stay on the Ryujinx tile:
  Eden does not open them. Keep one profile in Eden.

- **An AMD GPU now runs at its top clock all the time.** The `auto` level
  dropped a Radeon 680M to 400-533 MHz between frames, and the ramp-up added
  input lag in heavy games while the frame rate looked fine. The installer
  writes `/etc/udev/rules.d/99-gamecore-gpu.rules`; the OTA cannot (root), so
  on a box installed earlier run once:
  `echo 'SUBSYSTEM=="drm", KERNEL=="card[0-9]*", DRIVERS=="amdgpu", ATTR{device/power_dpm_force_performance_level}="high"' | sudo tee /etc/udev/rules.d/99-gamecore-gpu.rules && sudo udevadm control --reload-rules && sudo udevadm trigger -s drm`.
  Intel and NVIDIA boxes are not affected.

- **GameCube and Wii, and Game Boy, Color and Advance, are now one system
  each** (`gamecube`, `wii`, `gb`, `gbc`, `gba`), with their own ROM folder,
  tile, logo, bezel and playtime. A fresh install gets them directly. An
  installed box keeps its **GameCube / Wii** and **Game Boy Advance** tiles
  after the update, and they keep launching every game as before; nothing moves
  until you run, as the box's user and with every game closed:

  ```bash
  tar -czpf ~/userdata-before-split.tgz -C / userdata          # back up first
  /opt/GameCore/.venv/bin/python3 /opt/GameCore/scripts/split-systems.py --data /userdata
  # read the plan, then the same command with --apply, then restart GameCore
  ```

  It moves each game (and its `.sav`, covers, hours and settings) by rename on
  the same disk, never copies or overwrites, and leaves anything it cannot place
  where it is, with the old tile. Details: `docs/architecture/07-config-and-data.md`.
  The save-manager addon reads saves from both layouts.

- **`/usr/local/bin/gamecore-addon` is a copy only the installer writes, and
  it is stale on every box installed before 9 August.** The OTA cannot replace
  it (root) and now says so with the command; the copy that runs today does
  not know `GAMECORE_DATA` at all. Harmless while the data still lives inside
  the install, and the thing that would split the box the day it does not — a
  `gamecore-addon update` from the old CLI would bake the old root into every
  addon's unit. Refresh it once:
  `sudo install -m 755 /opt/GameCore/install/bin/gamecore-addon /usr/local/bin/gamecore-addon`.
  The CLI now also finds the data root on its own, from the backend's unit,
  when the shell that runs it has none — see `docs/architecture/07-config-and-data.md`,
  *Moving the data out*.

- **A bezel per console is now possible, and no bezel is supplied.** One
  emulator is sometimes several machines: mGBA runs Game Boy and Game Boy Color
  in 10:9 and Game Boy Advance in 3:2 behind one system id, so a single frame
  bit into one of them — and the drift correction, keyed by the announced ratio,
  was learned from whichever console was played first and then frozen for all
  three (`for_launch` sets `measure: false` once an answer exists). Measured on
  the reference box: one `mgba@1:1` entry of `1234x1080`, a Game Boy rectangle,
  cutting 193 px off each side of every GBA game.

  **Nothing is required.** `config/systems.json` gains `consoles` through the
  `merge_file()` call the OTA already makes, and the old console-blind
  corrections simply become unreachable, so each console re-measures itself —
  which already makes the *hole* right per console even with the old artwork.
  What no update can deliver is the artwork: `assets/overlays/` is excluded from
  the rsync. To get a frame that actually fits, drop a PNG named
  `<system>.<console>.png` beside the system one — `mgba.gba.png`,
  `dolphin.wii.png`. `docs/reports/bezels-per-console-phase3.md` has the
  verification steps, the backup and the way back.

- **An overlay upload with no transparent area is now refused (422).** A valid
  image with no hole is a rectangle painted over the whole game; every previous
  check passed it. This applies to the existing per-system upload too, which in
  practice means JPEG is no longer usable as a bezel — it cannot carry an alpha
  channel. A PNG copied in by hand is still not validated: the guard is on the
  route, not the filesystem.

- **Stremio launches windowed until its tile is patched.** The `fullscreen` and
  `gamepadTrigger` blocks were lost when the tile moved to a wrapper script, and
  an OTA cannot deliver them: `update/linux.sh` excludes `config/` wholesale and
  only merges `systems.json`, never `apps.json`. A fresh install, or re-running
  `install/arch.sh`, regenerates `config/apps.json` from the shipped catalogue
  and fixes it. Otherwise patch the `stremio` entry by hand.

### Changed

- **Project rules as Claude Code skills** (`.claude/skills/`): English, short
  comments; docs updated with the code area; layer boundaries; naming; an
  800-line file budget with `check-file-size.sh`. CONTRIBUTING.md points at
  them.
- **Launch, display and the grid rows moved out of routers** into
  `services/launch.py`, `services/display.py` and `services/systems.py`; the
  X11 display env joined `services/session.py`; `configgen/sdl_probe.py` and
  `services/gamepad_devices.py` split off their parents. Behaviour unchanged.
- **Theme stylesheets are split by concern** under `config/themes/<id>/css/`;
  `theme.css` only `@import`s them in cascade order. Editing a theme now means
  editing the file under `css/`.
- **The melonDS layout daemon is five files** installed together. A box keeps
  running its current single-file copy until `sudo gamecore-emu install
  melonds` redeploys the pack, as before.
- The last French UI strings (boot recovery, Desktop mode, mapping wizard
  errors) are English.

- **A fresh install with its data outside the install now starts with its
  bezels.** `install/arch.sh` seeded the player's starting tree — the shipped
  bezels, `config/overlays.json`, the bundled themes — into the install "when
  absent there"; on a split install (`GAMECORE_DATA=/userdata`, which is what
  the ISO produces) the data directories already existed, empty, so nothing was
  seeded and no game got a frame. Seeding now targets the data root and fills a
  directory that is absent or empty; a populated one is left alone. Same
  decision, three consequences: the addons checkout is pre-created in
  `/opt/gamecore-addons` only on the old layout, the desktop shortcut carries
  the GameCore logo instead of the theme's generic gamepad, and the graphical
  installer asks for the data path (default `/userdata`) when it is not the ISO.
  Boxes already installed are not affected — nothing here runs at OTA time.

- **The power menu offers "Return to desktop".** Leaving the front end is the
  third way a session ends and it was reachable only from Settings → Desktop —
  four rows into a menu nobody opens in order to quit, while the button that
  means "I am done with this box" opened a screen that could reboot it and shut
  it down but not step out of it. Same two-press confirmation and the same
  failsafe as the other two. Every theme gets it; the settings page stays for
  the sentence of explanation the row has no room for.
- **A theme can now set the player's sound and haptics settings, and drop the
  mapping utilities from the power menu.** `sdk.system.sound` and
  `sdk.input.haptics` were read-only, so a theme rendering its own
  Settings → Audio silently removed three controls from the console; they take
  values now. `DefaultShell` also accepts `powerOmit`, letting a theme that
  gives "Scan mapping" and "Forget mapping" a proper home stop showing them in
  the power menu — `restart`, `shutdown` and `desktop` can never be dropped.
  Nothing changes for a theme that asks for neither.
- **Shelf's settings screen is a numbered rail, and Shelf v1 ships beside it.**
  The previous drawer menu is installed unchanged as the separate theme
  **Shelf v1**, selectable from Settings → Themes. It is the fallback if the
  new screen turns out to be awkward on a pad — nothing to restore by hand.

- **The kiosk is hosted on the machine's own X11 desktop session.** openbox is
  no longer installed and is no longer the auto-login target. Closing GameCore
  now reveals a usable desktop instead of an empty root window. Boxes installed
  before this keep their openbox session until `arch.sh` is re-run.
- **`gamecore-session-select` is a kiosk toggle, not a session switcher.** There
  is one session now, so it enables or disables `gamecore-ui.service` and
  touches no SDDM configuration. `gamecore` / `desktop` / `status` unchanged.
- **`install/` is grouped by role** — `bin/`, `system/`, `steps/`, `generated/`.
  After an OTA the old flat copies stay behind in `/opt/GameCore/install/`,
  inert; a clean reinstall avoids them.
- **Emulators and applications install from their pack**, through
  `gamecore-provider.py`. `install/arch.sh` no longer contains a list of
  emulator ids, a Flatpak loop, or the EmberTV/Stremio blocks.

### Fixed

- **The guard added to stop an unbootable ISO made every build fail instead,
  and the image it rejected was fine.** It located `efiboot.img` by asking
  `lsblk` for the partition typed with the EFI GUID `C12A7328-…`, which no
  archiso image ever reports: xorriso describes the appended ESP in both tables,
  but the isohybrid MBR it writes is not a *protective* MBR — no `0xEE` entry —
  so Linux takes the DOS parser, never reads the GPT, and types the partition
  `0xef`. The GUID is in the image, in the GPT the firmware reads; it is simply
  never what the block layer says. The lookup now reads the table out of the
  file with `sfdisk` and accepts either spelling, then hands mtools a byte
  offset — so it needs no loop device, no partition node and no udev.
  `bash install/iso/build.sh --esp-offset <image>` runs that lookup alone,
  without root, and three tests in `test_iso_profile.py` hold it against
  fixture tables of both kinds.

- **The installation ISO booted on nothing — no machine, no firmware, neither
  bootloader.** BIOS showed the syslinux menu, counted down and started over,
  for ever; UEFI said `Error preparing initrd: Not found`. One cause under both:
  every boot entry asked for `intel-ucode.img` and `amd-ucode.img` beside the
  kernel, and archiso stopped putting them there (upstream
  [archiso#226](https://gitlab.archlinux.org/archlinux/archiso/-/issues/226)) —
  the microcode belongs inside the initramfs now, via mkinitcpio's `microcode`
  hook, which this profile did not have either. The boot configs name one initrd
  each and the hook is in place. `intel-ucode` and `amd-ucode` stay in
  `packages.x86_64`: the hook builds the early cpio *from* them.
- **The guided disk install wrote the same three-initrd boot entry onto the
  machine it had just installed.** The files it named (`/intel-ucode.img`,
  `/amd-ucode.img` on the ESP) do normally exist there, so this was an
  assumption rather than a proven break — but the assumption is about what
  `mkarchiso` leaves in the airootfs's `/boot`, and being wrong about it means
  an install that completes and a box that never boots, which is strictly worse
  than an ISO that refuses at the menu. The target now gets the `microcode` hook
  in its own `/etc/mkinitcpio.conf` and a single-`initrd` entry, the same way
  the ISO does. `gamecore-fallback.conf` already named no microcode, so the two
  entries had disagreed with each other from the start; they now match.
- **The installed box had its `/etc/mkinitcpio.conf.d/` switched off, for ever.**
  The preset written for the target set `ALL_config`, which is the same `-c`
  mechanism as above — any drop-in a package installs on that machine afterwards
  would be written, be valid, and never be read. Arch's own preset keeps that
  line commented out. It is now left out.
- **The ISO's initramfs was never built from the ISO's own hook list.** Its
  preset set `ALL_config=/etc/mkinitcpio.conf`, mkinitcpio turns the
  `/etc/mkinitcpio.conf.d/` drop-in off whenever it is given a config file by
  name, and so `archiso.conf` — `archiso`, `archiso_loop_mnt`, `memdisk` — was
  read by nothing. The preset now names the drop-in, as releng's does. This was
  hidden behind the boot failure above and would have surfaced as an emergency
  shell the moment it was fixed alone.
- The graphical installer segfaulted on the first key typed into any field —
  PyInstaller bundled the runner's `libxkbcommon` next to the host's
  `libxkbcommon-x11`. Fixed by a `.spec` that keeps host-provided libraries out
  of the bundle, and by pinning the build's PySide6 and PyInstaller.
- A fresh install died at 4 % (`tar: /opt/GameCore: Cannot open`) and, once past
  that, at 66 % (Firefox kiosk profiles deleted by the catalogue refactor while
  `arch.sh` still read the old path). Only fresh installs were affected, which
  is why neither showed up for months.

### Added

- **`install/iso/build.sh` now refuses to finish a build whose image cannot
  boot.** It mounts the ISO it just produced, resolves every path the shipped
  boot configurations name (systemd-boot's `linux`/`initrd`, syslinux's
  `LINUX`/`INITRD` — comma lists split, which is where the ucode images hid),
  and checks each one against the ISO 9660 tree *and* against `efiboot.img`, the
  FAT partition a machine booting the ISO in El Torito actually reads. One
  missing path is fatal. `--verify-only <image>` re-runs the check on an
  existing ISO, so watching the guard fail does not cost a second build.
- Eight invariants in `backend/tests/test_iso_profile.py`, the millisecond
  half of the same guarantee: the microcode is in the initramfs **or** named by
  the boot configs and never both or neither, the ISO preset points at the
  drop-in that carries those hooks, `microcode` stays ahead of any `autodetect`,
  the ucode packages stay, and the installed system gets the same arrangement.
- `ruff` and `shellcheck` in CI, and a single tile builder
  (`backend/services/catalog/tiles.py`) replacing the two that had drifted.
- `docs/architecture/10-catalog-and-install.md` — the catalogue and the install
  pipeline, which the documentation did not cover at all.
- GPL-3.0 licence, `CONTRIBUTING.md`, `.editorconfig`.
