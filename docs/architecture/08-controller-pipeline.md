# 8 — Controller pipeline

The hardest part of the project, and the one most likely to be broken by a
well-meaning refactor.

Companion: `docs/CONTROLLER_MODELS.md` for per-emulator format notes.

## Where the code is

`backend/services/controller_profiles.py` used to be ~1700 lines. It is now a
**121-line facade** that re-exports names; the logic lives in two places, split
along one line:

| | |
|---|---|
| `backend/services/configgen/` | everything common to all emulators — SDL resolution, the give-up type, snapshots, the mapping database, the write primitives |
| `backend/services/configgen/sdl_probe.py` | which SDL a Flatpak loads (`bundled_sdl2/3`, `flatpak_location`) and the cached subprocess probes (`sdl2_probe`, `sdl3_identity`); re-exported by `controllers.py` |
| `catalog/<id>/generator.py` | one emulator's own format. Nothing else knows it |

The facade survives because `gamepad_monitor` and `routers/` import from it, and
that hotplug path must keep working unchanged. **The move was a move, not a
rewrite**: `backend/tests/test_controller_characterisation.py` replays fourteen
pad × slot scenarios against the real generators and compares byte for byte to
recorded output. That harness is also the only proof this repository has for two,
three and four pads — the development box has exactly one DualShock 4.

The declarative half lives in `catalog/<id>/pack.json` under `controllers`
(`strategy`, `maxPlayers`, `order`, `target`, `padType`, `multitap`). **The
`what` is data, the `how` is the generator.** A new emulator is a directory, not
an edit to a dispatch table — see [10](10-catalog-and-install.md).

## The problem

"Plug in any controller and play" means every emulator must recognise a pad
nobody configured. They do not agree on how to be told.

```mermaid
flowchart TD
    pad["a controller is plugged in"] --> q{"how does this emulator<br/>identify a device?"}
    q -->|"by SDL role vocabulary<br/>PCSX2, DuckStation"| sdl["clone slot 1's role bindings<br/>onto an SDL index<br/><b>sdl-index-clone</b>"]
    q -->|"by device NAME string<br/>RPCS3, Dolphin"| nm["resolve_name() must be<br/>trusted, or nothing is written<br/><b>rewrite-player-block / -device-line</b>"]
    q -->|"by device GUID<br/>Ryujinx"| guid["ryu_guid_from_sdl2()<br/>exact, or the slot dies silently<br/><b>guid-rebind</b>"]
    q -->|"by GUID + raw button indices<br/>Azahar, Cemu, mGBA, melonDS, RMG"| snap["ask THAT emulator's SDL for the<br/>indices — configgen/inputs.py —<br/>and keep the owner's own capture<br/>above it <b>snapshot-restore</b>"]
```

That last branch used to read "no synthesis possible", and for a long time it
was true: a vendor:product says nothing about raw button indices, so azahar and
gopher64 sent the owner out to the emulator's own settings screen and mGBA
shipped a seed carrying one particular DualShock 4's numbers. **What was missing
was not a way to write them but a way to ask for them** — see
[the abstract input model](#the-abstract-input-model) below. Cemu is still
copy-only, for the reason recorded in `derive.cemu_is_not_derivable`.

**RMG is the exception that proves the branch is about INDICES.** It sits here
because it needs the owner's capture kept above anything synthesised and because
it identifies a device by three strings that have to be read from SDL. But its
`InputType` 0 and 1 carry SDL_GameControllerButton and Axis *constants*, not raw
indices (`Source/RMG-Input/common.hpp`), so its binding table is the same on
every pad and `catalog/gopher64/generator.py` writes RMG's own `fallback_profile`
when no snapshot exists. Types 2/3/4 are the raw index space and it never writes
those — which is the same distinction, drawn on the other side, that the abstract
input model exists to hold.

The strategy names in bold are the literal `controllers.strategy` values in each
pack. `scripts/catalog-query.py` will print the current map; do not retype it
here, it moves.

## SDL GUIDs

An SDL GUID packs bus type, vendor and product as little-endian 16-bit words at
fixed offsets.

| Function | Role |
|---|---|
| `vidpid_of(guid)` | `(vendor, product)` — SDL packs them at a fixed offset |
| `ryu_guid_vidpid(dashed_guid)` | the same, for Ryujinx's dashed dialect. For **reading** a config, never for deciding what to write |
| `ryu_guid_from_sdl2(sdl_hex)` | the exact GUID Ryujinx will compute, from the one SDL2 reports — .NET `System.Guid` byte order, **name CRC (bytes 2-3) zeroed** |
| `sdl2_probe(vendor, product, lib)` | what SDL2 itself says about a connected pad: raw GUID **and** GameController mapping, from a subprocess. `lib` picks *which* SDL2 answers |
| `bundled_sdl2(app_id)` | the SDL2 a flatpak'd emulator really uses — the one it **ships**, else the one its **runtime** provides — or `""`. Its answer, not the host's, is what goes in that emulator's config. With two branches of one app installed, the lookup follows the app's `current` link (the one `flatpak run` starts) |

> A GUID carries bus type, version and driver signature as well as
> vendor/product, so two pads with the same vendor:product can have different
> GUIDs — the same DualShock 4 over USB and over Bluetooth, for instance.
> Substituting vendor/product bytes into someone else's GUID therefore yields a
> device that does not exist. Two functions used to do exactly that
> (`swap_vidpid`, `_ryu_swap_vidpid`); both are gone. Ask SDL.

**Ask the emulator's SDL, not the host's.** They can disagree. Measured on the
reference box, same DualShock 4, same instant:

| library | GUID | bus |
|---|---|---|
| host `libSDL2-2.0.so.0` — sdl2-compat 2.32.70 over SDL3 | `05008fe5…` | `0x0005` Bluetooth |
| Ryujinx's bundled `libSDL2.so` — real SDL 2.30.0 | `03008fe5…` | `0x0003` USB |

One byte. SDL3 reports the transport; SDL2 2.30 reports USB for anything HIDAPI
drives, Bluetooth included. Writing the host's answer made Ryujinx's
`IndexOf(id)` return -1 and dispose the slot in silence — `Hid Remap: No
matching controllers found` in its log, the controller applet on screen.

RPCS3 and PCSX2 ship SDL3, which agrees with the host, and Dolphin uses the
runtime's. But Ryujinx is **not** the only one affected, and assuming it was is
what kept azahar unsynthesisable. azahar, melonDS and RMG bundle no libSDL2 at
all: they link `org.kde.Platform`'s, and `bundled_sdl2()` used to answer `""` for
them, which means "ask the host". Measured on this box, same DualShock 4, same
instant:

| library | D-pad | touchpad |
|---|---|---|
| host `libSDL2-2.0.so.0` — sdl2-compat over SDL3 | `dpup:h0.1 dpdown:h0.4 dpleft:h0.8 dpright:h0.2` | `b11` |
| `org.kde.Platform` 6.9 — real SDL 2.32.10 | `dpup:b11 dpdown:b12 dpleft:b13 dpright:b14` | `b15` |

`docs/CONTROLLER_MODELS.md` and `snapshots.py` both record azahar writing
`button_up = 11` for this pad and call it irreconcilable with "SDL's own mapping,
which claims a hat and calls button 11 the touchpad". Both statements are true.
They come from two SDL2 builds, and azahar's is the runtime's.

**Ryujinx zeroes the name CRC.** SDL 2.26+ packs a CRC16 of the device name into
bytes 2-3 to tell apart pads sharing a vendor:product. Ryujinx clears it before
building its id, so it must not survive into what we write.

Established by binding the pad by hand in Ryujinx's own Input settings and
reading back what it wrote — the only source of truth here, since every backup in
that directory was written by GameCore:

| | |
|---|---|
| Ryujinx wrote for itself | `0-00000003-054c-0000-cc09-000000006800` |
| its own SDL reports | `03008fe54c050000cc09000000006800` |
| host SDL3 reports | `05008fe54c050000cc09000000006800` |

Bus byte and CRC are two independent corrections and both are required: zeroing
the CRC on the host's answer still yields bus `0x05`, an id Ryujinx never
computes.

**Ryujinx: no two slots may carry the same `id`.** It resolves every entry in
`input_config` with `_gamepadsIds.IndexOf(id)`, by value — so two slots holding
one id both resolve to the same physical pad, and the game sees two controllers
connected. A right GUID is not enough on its own.

The duplicate is not created by a bad write; it is created by a *good* one
landing in a new slot. Profile a pad into Player 2 in one session and Player 1 in
the next — a Bluetooth reconnection is enough — and Player 2 keeps the id. Found
on the reference box with a DualShock 4 held by both. Releasing cannot help: no
disconnect ever happens for the surviving slot. So the Ryujinx generator clears
its own id from every other slot before writing, and its early "already correct,
don't rewrite 11 KB" return is conditional on there being no duplicate —
otherwise the slot that is right is exactly the one that hides the phantom.

## Naming a device — and refusing to guess

RPCS3 and Dolphin record the *device name string*, so it has to match exactly.
`resolve_name()` returns a **`ResolvedName`**, a `str` subclass carrying where
the answer came from. That type exists because a bare string could not tell a
reliable answer from a guess, **and the guesses were being written into configs.**

| `source` | meaning |
|---|---|
| `sdl3_live` | libSDL3 named this pad with it connected. The truth. |
| `fallback_table` | `SDL3_FALLBACK_NAMES`, measured SDL3 names. Reliable. |
| `unknown` | neither answered. The value is the pad's *kernel* name, fine for a toast, **not** what an SDL3 emulator enumerates |

`SDL3_TRUSTED` is the first two. A generator that matches by name must refuse to
write anything on `unknown`: the kernel name in an RPCS3 config produces
`SDL: Adding empty device` in its log and a pad that is dead in game, with a
config that looks perfectly correct.

**The community database is deliberately no longer in this chain.** It carries
SDL2-era names and is wrong for SDL3 on several pads — a DualSense is "DualSense
Wireless Controller", not "PS5 Controller". It survives in `display_name()`,
where being approximately right is the whole job.

The failure this guards against is an **absence, not an exception**: SDL3 answers
perfectly well and simply does not know the pad. No error is raised, and the old
chain walked quietly down to the kernel name and wrote it. Every rung below the
first now logs, once per pad, and again only when the answer *changes* — a pad
moving from `sdl3_live` to `unknown` is a pad that went to sleep.

`identification()` surfaces the same question at the API, so the Power menu can
say "this pad cannot be named" at the moment the owner is holding it.

## The abstract input model

`backend/services/configgen/inputs.py`. One `Input(kind, index, direction)` per
semantic control — `kind` being `button`, `hat` or `axis` — so a generator can
ask "which raw index is this pad's L1, and is it even a button" and translate the
answer into its own format. The shape is Recalbox's and Batocera's idea; the code
and the convention are not, and neither is their model of regenerating every
config at launch. **A generator here still writes only the sections it owns.**

The kind is the load-bearing half. mGBA stores a button as `keyA=<n>` and a hat
direction the other way round as `hat0Up=<gba key id>`; melonDS folds a hat into
`0x100 | hat<<4 | dir`. A model carrying only a number makes both unwritable,
which is exactly why those generators had nothing to write.

### Where the indices come from

The hard question, and the one that decides whether this helps or hurts. Two
sources exist and they describe the same pad differently:

| source | driver | authoritative for |
|---|---|---|
| the wizard's capture | the driver the backend's own SDL2 uses for that pad (`platform/gamepads.py`, raw layer); for a pad with no HIDAPI driver that is DirectInput/RawInput | pads SDL has no HIDAPI driver for, i.e. the ones the wizard exists for |
| SDL's own GameController mapping | whichever driver SDL uses — **HIDAPI** for Sony, Microsoft and Nintendo | everything else |

`derive.evdev_driven()` refuses a capture whenever the two disagree, and that
refusal is correct: azahar recorded `button_up = 11` for a DualShock 4 that SDL's
HIDAPI driver describes with a hat. The refusal was read as the wizard being
pointless for modern pads. **It is the opposite.** A pad SDL drives through
HIDAPI is a pad SDL already ships a mapping for, so the two sources are
complementary — each authoritative exactly where the other is silent — and
`for_pad()` never uses one in the other's territory.

`for_pad()` returns the GUID and the indices **from a single probe**, and callers
must use the pair they were handed. Mixing them is the bus-byte failure above
reached by another road: an id from one library beside indices from another binds
nothing.

`None` means neither source could answer, and a generator that gets it writes
nothing at all. That is not a gap to fill with a plausible default — a config of
invented indices looks correct, survives reboots, and is undiagnosable from a
sofa.

## `Skip` — a give-up is an answer

`helpers/base.Skip` is a `str` subclass returned by a generator that wrote
nothing *and has a reason*. `None` keeps its old meaning and only it: nothing to
do, nothing to say.

Every writer used to return `str | None`, and only truthy values were collected.
So "I retargeted Player 2" reached the log and "there is no Player 1 pad to clone
from" reached nobody — a give-up was byte-for-byte indistinguishable from a
success. **RPCS3's players 2-4 sat dead for a week that way**, and the old
`scan_mapping()` answered `{"ok": True}` on a snapshot it had taken of the wrong
controller.

`apply_profile` files Skips separately into `ProfileResult.skipped`;
`.complete` is false when any is present, and that bit is what makes a retry
possible at all.

## What a pad is, for profiling purposes

The footprint deciding whether a slot needs rewriting is
`(player, vendor, product, resolved name, dup, bustype)`. Two were added after
being found missing:

- **`player`** — a pad that moves slot rewrites different sections. Without it, a
  slot change was invisible.
- **`bustype`** — Ryujinx binds by a GUID that encodes the bus. MAC, vendor and
  product are all identical across a transport change, so moving a pad from
  Bluetooth to a cable left every GUID-bound emulator pointing at a device that
  no longer existed, silently. The same Xbox pad is `0x05…` over Bluetooth and
  `0x03…` over USB, while a DualShock 4 reads `0x03…` either way because HIDAPI
  drives it.

Not a pad at all: the Xbox 360 copies the PC pack makes during a Lutris game
(phys `gamecore-virtual-pad/<n>`, [10](10-catalog-and-install.md#xbox-pads-for-pc-games)).
`_find_gamepad_devices` and `detect_pads` skip them; counted, one took player 2
and rewrote every emulator's player 2 each time it appeared or left.

Those counters describe the **roster**, not one pad — which is why one pad
leaving invalidates the others.

## Releasing a slot

```python
release_profile(player_index, occupied=(), pack_ids=None) -> list[str]
```

The inverse of `apply_profile`, and both share `MAX_PLAYERS` so the write side
can never know a ceiling the un-write side does not.

This used to be documented as "only Dolphin needs it; the others just go
input-less when a pad leaves". **The reference box proved that false.** They do
not go input-less — they keep *naming an absent device*. With one DualShock 4
connected, RPCS3 still presented "Xbox One Wireless Controller 3" as Player 4 and
Ryujinx still held indexes 2 and 3. A PS3 game at four then sees four players,
two of whom cannot move, which is not the same thing as receiving no input.

The two extra parameters are not decoration:

- **`occupied`** — the slots still held *after* this release. Some of what
  `apply_profile` writes belongs to the roster rather than to a slot: the
  multitap PCSX2 and DuckStation need before slot 3 exists is the measured case.
  A release knowing only its own index cannot tell whether player 3 is still
  sitting there.
- **`pack_ids`** — narrows the sweep. The hotplug path passes `None` and sweeps
  everything, because a pad leaving concerns every emulator. The **launch** path
  (`services/launch.py`) passes only the emulator about to start: rewriting Cemu
  because someone launched PCSX2 is a side effect nobody asked for, and it is
  also what would make the pass too slow to sit in front of a launch. That sweep
  is bounded by `RECONCILE_BUDGET` and abandoned on timeout — the launch matters
  more, and the monitor comes back within three seconds anyway.

Note where it is **not** called: `_reconcile`'s departure loop no longer releases.
Hanging it there meant a slot was only freed for a departure this process
witnessed, and it named the slot the pad held *before* `compact()` moved anyone.
The sweep decides from the roster instead, which is the thing that actually says
whether a slot is occupied.

## Slots close up between games

`controller_registry.compact()` renumbers occupied slots to 1..N preserving
order, and returns what moved. Slots are handed out lowest-free-first and never
taken back from a connected pad — deliberately, so nobody changes player number
mid-game — but that leaves a gap: unplug player 1 during co-op and the survivor
keeps slot 2 for the session. Observed with a lone DualShock 4 on Player 2, and
Ryujinx therefore presenting no Player 1 at all to a Switch game that wanted one.

`_reconcile` calls it **only when no game is running**. Closing the gap
mid-session would silently turn player 2 into player 1. The pads that move are
re-profiled on their own, since the player number is part of the footprint.

## melonDS: players 2-4 are written at launch, not on connect

melonDS profiles slot 1 only (`maxPlayers: 1`): on connect, its one
`[Instance0.Joystick]`. Players 2-4 exist only when melonDS runs one instance
per pad, so their `[InstanceN]` sections are written by the multiplayer
launcher (`setup.prepare`) just before melonDS starts, and so is every
`JoystickID`: the SDL probes take about a second per pad model, too long for
the `launch_command` hook's budget.
melonDS picks a pad by SDL **index**, and only melonDS's own SDL, in its
sandbox, knows that order (a DualShock 4 is `/dev/hidrawN` there, an event node
to GameCore). Details in [10](10-catalog-and-install.md#melonds-local-multiplayer).

## A failed pass is not a finished pass

`gamepad_monitor._reconcile` used to mark a pad done *before* attempting it, so a
give-up was remembered exactly like a success and the pad was never revisited.
Measured: a DualShock 4 and an Xbox pad plugged in together; Dolphin, RPCS3,
PCSX2 and DuckStation got both players, Ryujinx got no Player 2 at all. Replaying
the same call afterwards created the slot correctly — the failure had been
transient, SDL had not yet caught up with a pad that had just connected, and the
generator rightly refused to invent an id.

The footprint now carries a retry budget (`PROFILE_RETRIES = 5`, ≈15 s): an
incomplete pass is retried on the next scan, a clean one settles immediately, and
a reconnection restores a full budget. Bounded on purpose — some give-ups are
permanent (an emulator with no gamepad slot to clone from will never succeed) and
each retry pays for SDL probes with an 8 s timeout apiece. `run()` also had to
stop gating `_reconcile` on `was != live`: nothing about the pad changes while
SDL is simply behind.

## The autoconfig switch — one gate, and it has to be visible

`Settings → Controllers` carries a switch that stops GameCore writing emulator
controller configs at all, plus a per-emulator exception behind an advanced row
("I configure Dolphin by hand, the rest can look after itself").

**One control point.** `backend/services/controller_autoconfig.py` answers
`enabled_for(pack_id)`, and exactly one function consults it:
`configgen.autoconfigured_packs()`, which splits `profilable_packs()` into the
ones still ours to write and the ones left alone. A split pack keeps the owner's
choice: `gamecube` also reads the record left under `dolphin`
(`configgen.superseded_ids()`), and switching it back on clears both. Both halves of the write path —
`apply_profile` and `release_profile` — go through it. **A generator never learns
the switch exists**, which is what makes a pack added tomorrow obey it for free.

`profilable_packs()` itself stays unfiltered on purpose: it answers "does this
pack profile pads AT ALL", a fact about the catalogue, and `controller_capture`
uses it to find which SDL libraries to probe. **The wizard keeps working with
autoconfig off** — it writes the user's SDL mapping database, not an emulator's
config, and that database is precisely what makes the manual mode workable.

| | |
|---|---|
| **Global off** | nothing is written anywhere; a per-emulator row saying "on" is overridden, and the settings screen shows those rows as readings rather than switches |
| **Turning it off** | the slots GameCore filled are **emptied** — `Device = ""`, `id = ""`, multitap off. Not restored to the seed: that is the inverse of a write, and the owner is meant to open the emulator's own input UI and find nothing configured rather than inherit a mapping they cannot explain |
| **Turning it on** | `gamepad_monitor.request_reprofile()` — the next scan, ≤3 s away, rewrites for the connected pads. It **overwrites** anything set up by hand meanwhile |

**The order in `configgen.set_autoconfig` is load-bearing.** Going off, the
clean-up runs *before* the new state is persisted, so `release_profile` meets an
autoconfig that is still on and empties the slots through the ordinary gate.
Persisting first would need a bypass, and a bypass that exists is one something
else will eventually use. `autoconfig-turned-off-after` in the characterisation
suite fails the moment that order is swapped.

**A silent switch is a trap, not a feature.** The failure mode this was designed
around: somebody turns it off to fiddle, forgets, plugs a new pad in three weeks
later and nothing happens — no error, no log they will read. So "off" is said in
five places: the settings rail row, the Controllers page heading and its own row,
the controller screen the □ button opens (all three shipped views), the pad
toast — in-app and on the Electron HUD, which is the one that reaches somebody
mid-game — and one `configgen:` line in the journal naming both the systems and
the reason.

The toast rides the `Skip` transport (`skipped_labels` → `gp:connected` → toast)
but on its own field. `ProfileResult.off_labels` is deliberately not
`skipped_labels`, because `complete` is computed from the latter: a Skip is an
unfinished pass and gets five retries, while this is the owner's instruction and
retrying it would only produce a "still incomplete" warning about a box doing as
it was told. It is only sent when the pad got **nothing at all** — one emulator
carved out by hand is a choice, and toasting it on every connect is nagging.

State lives at `<DATA>/config/controller-autoconfig.json`, written tmp +
`os.replace` like `themes.set_active`. **Missing or unparseable reads as ON**:
the failure of the setting has to be the harmless direction, and a lost config
directory must not leave a box where plugging a pad in does nothing.

## The three entry points

### 1. Automatic — on connect/disconnect

```mermaid
sequenceDiagram
    participant gm as gamepad_monitor
    participant reg as controller_registry
    participant cg as configgen
    participant gen as catalog/&lt;id&gt;/generator.py

    gm->>reg: connect(key, label) → player slot
    gm->>reg: compact() (only when no game runs)
    gm->>cg: apply_profile(player, vendor, product, evdev_name, dup)
    cg->>cg: resolve_name() → ResolvedName(source)
    loop each profilable pack, in controllers.order
        cg->>gen: generate(player, pad, opts)
        gen-->>cg: message · Skip(reason) · None
    end
    cg-->>gm: ProfileResult(.complete)
    Note over gm,cg: incomplete → retried, up to PROFILE_RETRIES
    Note over gm,gen: on unplug — the roster sweep, not this loop
    gm->>reg: disconnect(key)
```

**A launch waits for this to have happened.** An emulator reads its input config
once, at startup, and never looks again — so a pass that lands a second later
lands nowhere. Measured on the reference box, twice: RPCS3 started at 08:52:53
and its config was written at 08:52:59; Dolphin's arrived two seconds after it
started. Both times the pad was dead in game, both times it worked at the next
launch.

`services/launch.py` therefore calls `gamepad_monitor.await_profiled()` before
`process_manager.launch()`. It **waits**, it does not profile: profiling means
SDL probes carrying an eight-second timeout apiece, which cannot sit in front of
a launch — the same reason `_free_stale_slots()` is the release half only.
`gamepad_monitor` publishes its roster and its `applied` footprints for exactly
this question, and `unprofiled()` answers it with three dict lookups and no I/O.

Three states never wait: a settled roster (one scan period after boot, which is
every launch but the first), a monitor that is not running (nothing will ever
profile, so there is nothing to wait for), and a pad that has exhausted
`PROFILE_RETRIES`. The budget — `PROFILE_BUDGET`, 8 s against the 6.36 s a cold
pass measures — is a give-up, not a refusal: it broadcasts `game:notice` and
launches anyway. A late config is a playable game; a launch that does not happen
is a dead box.

### 2. Saved snapshots — restored, no longer created

For the `snapshot-restore` and `snapshot-or-synth` emulators, a snapshot under
`~/.local/share/gamecore/controller-snapshots/<emu>/<vid>_<pid>.snap` is the
owner's own input config for that pad model, and `snapshots.restore()` puts it
back on every connect. **A saved snapshot always wins** over a synthesis.

The snapshots were created by "Scan mapping" (`POST
/api/controllers/scan-mapping`) and deleted by "Forget mapping" (`DELETE` on the
same path). **Both actions were removed** — the buttons, the route and
`snapshots.capture()` / `snapshots.forget()`. What stays is everything that
READS a snapshot: `exists()`, `restore()` and `block_disagrees()`. A snapshot
whose config plainly describes a *different* controller is still **refused** on
restore and reported (the box holds a Cemu snapshot named for an Xbox pad that
contains a DualShock 4's config); with the forget button gone, the way out of
one is deleting the file named in the warning.

An owner who wants their own mapping in one of these emulators now configures
it in the emulator and turns that emulator's autoconfig off (Settings →
Controllers → Per-emulator exceptions), so GameCore stops writing it.

### 3. The mapping wizard — for a pad SDL does not know

`POST /api/controllers/mapping/{start,commit,cancel}`, plus a WebSocket at
`/ws/controllers/mapping` pushing every press as an SDL token.

This is the case no hand-made config can help with. There is no config to
remember, because the owner cannot make one — the emulator's input UI
will not bind a device its SDL never enumerated. So the pad is mapped here, once,
button by button, and the result is written as an SDL mapping line every
SDL-based emulator on the box reads at startup. **One gesture, thirteen systems.**

It is driven entirely by the pad being mapped — press to record, hold to skip a
button the pad does not have, double-press to go back — because a controller the
box cannot understand is exactly the one you cannot navigate a normal UI with.

**Reaching it is the host's job, not a theme's.** The button lives in
`GamepadViewProps.onRemap`, and a view is allowed to leave it out — but neither
shipped theme destructured it, so on every box anyone actually runs the wizard
was invisible. `GamepadModal` therefore owns a gesture as well: a one-second hold
of △ on the controller screen. A hold and not a press, because that screen's rule
is that every press is a test and must only light up its counterpart on the
diagram. `test_shipped_theme_views.py` holds the two shipped themes to drawing
the button too; `scripts/check-theme.mjs` cannot, since a view that ignores a
prop is perfectly valid JavaScript.

**A session reads only the pad's JOYSTICK nodes.** A DualShock 4 publishes three
under one MAC — pad, touchpad, motion sensors — and reading all three meant the
accelerometer drove the wizard by itself: 4335 events in three seconds with the
pad lying still, 2561 of them turned into `a0`..`a5` tokens indistinguishable
from the real sticks, each arming the hold timer that skips a step. SDL does not
enumerate those nodes either, so an index taken from one names nothing any
emulator will compute. Axis presses are measured as **deflection from where the
axis rests**, not against the kernel's `flat`: a DualShock 4 declares its sticks
`0..255 flat=0` resting at 128, so `abs(value) <= flat` passed every reading and
never released.

## The mapping database — two files, and the order is measured

`configgen/mapping_db.py`. `process_manager` exports
`SDL_GAMECONTROLLERCONFIG_FILE` to every game it launches.

| file | |
|---|---|
| `gamecontrollerdb.txt` (vendored, `backend/data/`) | community work, ~2200 lines, **replaced by every OTA**. Not ours to edit |
| `gamecontrollerdb_user.txt` (under `~/.local/share/gamecore/`) | the owner's captures. Never shipped, never touched by an update |

What SDL actually reads is neither: `served()` is a concatenation, regenerated
when either source changes. Keeping the owner's lines in their own file is what
makes them survive an OTA.

**The order was measured, not assumed.** SDL parses top to bottom and does *not*
stop at the first entry for a GUID. Against four independent SDL builds — Ryujinx's
bundled SDL2, PCSX2's SDL3, RPCS3's SDL3, the host's SDL3 — the **last** line wins,
unanimously. So "the user's mapping takes priority" means the user's lines go at
the **end**. Reading that as "user first" produces a file that contains the
capture and an emulator that ignores it. `test_mapping_db.py` re-runs the probe
against every SDL it can find, so the day one changes its mind the suite says so
instead of the pad.

**Staleness is a fingerprint, not a timestamp** — and an OTA is exactly why.
`update/linux.sh` uses `rsync -a`, which implies `-t`: the vendored database
arrives carrying the mtime it had in the archive, which can be *older* than the
merge already on disk. A "newer than me?" test therefore answers no to a database
that has genuinely just changed, and the box serves the previous release's merge
for ever — silently, because the file is present and looks right. Size and mtime
of both sources are recorded in the served file's header and compared on every
call.

## If you touch this

- **Always `backup()` before writing, and write through `atomic_write()`.** A
  wrong write costs the user their manual mapping; a truncated one costs them the
  whole config, and this pipeline runs at backend startup — the moment someone
  can still cut the power at the wall.
- **Return a `Skip`, never a bare `None`, when you give up.** See above: that is
  how RPCS3's players 2-4 stayed dead for a week.
- **Never reformat a config wholesale.** A generator writes only the sections
  GameCore owns and leaves the rest intact to the byte. That is the deliberate
  divergence from Batocera, which regenerates everything at launch — and it is
  what makes `.bak-preinstall` and the snapshot mechanism coherent at all.
- **Never write a name whose `source` is not in `SDL3_TRUSTED`.**
- **Test with two identical pads, and unplug one.** `dup_index` exists because
  RPCS3 names devices `"<name> 1"`, `"<name> 2"` — most bugs here are
  second-controller bugs, and the rest appear when a pad leaves.
- **Run the profiler twice and diff.** Every generator must be a no-op the second
  time. Ryujinx's was not, and rewrote 11 KB on every connection.
- **A pad's D-pad is not a settled question.** Check `pad_has_hat()` before
  assuming: a DualShock 4 reports buttons where SDL claims a hat.
- **Add an emulator by adding a directory**, not by editing a dispatch table. A
  pack missing from what used to be a tuple in `configgen` was not profiled at
  all, and the only symptom was a pad that did nothing in that one emulator.
- **Never ask the autoconfig switch a second time.** It is answered once, in
  `autoconfigured_packs()`. A second `if` somewhere else is the signal it went in
  at the wrong level — and the two would drift, which for this setting means an
  emulator being written while the screen says it is not.
- **Never exercise the pipeline outside pytest.** `backend/tests/conftest.py`
  redirects `HOME` before any import because `configgen.HOME` is `Path.home()`
  evaluated at module scope. A hand-run script skips that and writes to the
  developer's own emulator configs — measured: one ad-hoc `TestClient` emptied
  Player 1 across RPCS3, Dolphin, Ryujinx and RMG.
