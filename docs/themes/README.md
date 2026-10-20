# Theme SDK — specification

> **Status: implemented.** Items 1–5 and 7 of §16 are in the tree, and so is
> item 6 (extracting the whole default UI behind `sdk.defaults`): the surfaces
> listed in §5 are exposed, and so are the settings sub-pages, through
> `sdk.defaults.DefaultSettingsPages`.

A theme **replaces part or all of the UI**. It is not a skin: there is no CSS
layer to override in this app (see below), so a theme ships components, not
styles.

## Read this first

This document only covers what is **new**. Everything a theme consumes already
exists and is documented — do not duplicate it here:

| You need | Read |
|---|---|
| The gamepad event bus, its 3 invariants, the event list | [`../architecture/05-frontend.md`](../architecture/05-frontend.md#the-gamepad-event-bus--hooksusegamepadts) |
| The WebSocket event table | [`../architecture/05-frontend.md`](../architecture/05-frontend.md#the-websocket-event-table) |
| The store fields and actions | [`../architecture/05-frontend.md`](../architecture/05-frontend.md#store--storeindexts) |
| The `api` object and its groups | [`../architecture/05-frontend.md`](../architecture/05-frontend.md#apiindexts) |
| Every backend endpoint behind it | [`../architecture/03-backend-routers.md`](../architecture/03-backend-routers.md) |
| Why the UI must never become unnavigable | [`../architecture/09-gotchas.md`](../architecture/09-gotchas.md) |

## 1. Why themes ship components, not stylesheets

React components have **no CSS files** (the shared settings screen in `frontend/src/settings/` is written in the SDK idiom and ships `settings.css`). Most components style themselves with
inline style objects — 131 hardcoded hex colours and 172 `rgba(255,255,255,…)`
values live inside the components. The only global CSS in the project is six
lines in `frontend/index.html` (reset, scrollbar, keyframes).

An external stylesheet cannot override an inline style. So a theme cannot
restyle the existing UI — it replaces the components that contain the styling.
Inside its own markup a theme is free to use classes and a stylesheet of its
own.

## 2. Buildless

A theme is a directory of plain files. No bundler, no `node_modules`, no
compile step — the dropped folder is exactly what runs, the same principle the
addons repo already follows.

The entry point is a native ES module. Themes author markup with a tagged
template instead of JSX, which needs no transform. The host supplies React, the
hooks and Framer Motion through the SDK object, so a theme never imports them
and there is never a second React in memory.

## 3. On disk

| Path | Contents |
|---|---|
| `config/themes/<id>/theme.json` | manifest |
| `config/themes/<id>/index.js` | ES module, entry point |
| `config/themes/<id>/views/`, `lib/` | one feature per file (§5) |
| `config/themes/<id>/theme.css` | the theme's own stylesheet |
| `config/themes/<id>/css/*.css` | the stylesheet split by concern; `theme.css` only `@import`s them in cascade order (served no-cache, so imports never go stale). Tests read the whole sheet with `backend/tests/css_bundle.py` |
| `config/themes/<id>/preview.png` | thumbnail for the settings page |
| `config/themes/<id>/assets/` | images, fonts, audio |
| `config/theme.json` | the active theme, written by the API. Per-device: not in git, not in the OTA archive |

They live under `config/` because that whole directory is excluded from the OTA
rsync — that is what protects `systems.json`, your mappings and the playtime DB.

`update/linux.sh` makes one exception, and it is decided by `version`:

| On the box | In the release | What happens |
|---|---|---|
| — | yes | installed |
| yes | — | **never touched.** A theme you wrote is yours; the update only ever looks at what shipped |
| yes | newer `version` | replaced; the previous copy is kept in `config/themes/.prev/<id>/` |
| yes | same or older `version` | left alone |

The unit is the directory, so there is no merge and no half-updated theme built
from two releases. The swap is staged and renamed into place, so a theme is
never observed half-written and never briefly missing.

This is why `version` is mandatory. It is your statement that something
changed, and it is the only thing that decides whether a box takes your fix —
so **bump it in `theme.json` whenever you publish a change**, or the release
will ship a theme no box installs.

The cost is the honest one: editing a bundled theme in place on a box, without
bumping its version, means a later release can replace that edit. It is kept
under `.prev/<id>/` rather than lost, and `list_themes()` skips any directory
starting with `.` or `_`, so nothing there is ever offered to a player. Only
the most recent replacement is kept.

Your selection (`config/theme.json`) is untouched throughout — it is not in the
archive.

## 4. Manifest — `theme.json`

| Field | Type | Required | Meaning |
|---|---|---|---|
| `id` | string | yes | must equal the directory name |
| `name` | string | yes | shown in Settings → Themes |
| `version` | string | yes | the theme's own version |
| `api` | integer | yes | SDK major version targeted — `1` |
| `author` | string | no | |
| `description` | string | no | one line, shown under the name |
| `entry` | string | no | defaults to `index.js` |
| `preview` | string | no | defaults to `preview.png` |
| `styles` | string | no | defaults to `theme.css`; injected automatically when present |
| `provides` | string[] | yes | must list **every** surface: `["splash", "shell"]` (§5) |
| `schedule` | object | no | `{ "from": "MM-DD", "to": "MM-DD" }` — seasonal auto-activation |
| `launch` | object | no | `{ "ms": 1520 }` — how long your launch animation needs before the game starts (§5c) |
| `sounds` | object | no | `{ "move": "assets/move.wav" }` — UI sounds you replace (§13) |
| `settings` | object | no | `{ "pages": [...] }` — which host settings pages your own menu opens (§5d) |

A folder whose name starts with `_` is a **template, not a theme**:
`config/themes/_skeleton` is there to be copied, and never appears in
Settings → Themes. Name your own theme without the underscore.

`provides` is both declaration and gate: a surface must be listed here *and*
exported by the module. Miss either and the theme does not load at all — it is
not selectable, and if it was already active the default frontend runs whole.

## 5. The two surfaces

A theme provides **both** of these, always:

| Surface | What it is |
|---|---|
| `splash` | the boot animation |
| `shell` | the whole frontend body |

And **may** provide this one, which is the only optional surface:

| Surface | What it is | If you leave it out |
|---|---|---|
| `sessionBar` | the bar over a suspended game or application (SDK 5, §5f) | the host draws its own |
| `sessionMenu` | the panel the host opens on **PS ×2** (SDK 5, §5f) | the host draws its own |

`sessionBar` is not listed in `provides` and does not take part in the
all-or-nothing rule, because the host has a working one behind it. That is not
a courtesy: it is the only way back to a suspended session, and a theme that
simply forgot to draw one would leave a frozen emulator holding several
gigabytes of memory with nothing on screen able to resume or close it.

| Kept by the kernel, always |
|---|
| input bus, WebSocket, the global buttons (§6a), error boundaries, L1+R1 rescue, the *fact* that a splash runs, the *fact* that a session bar is on screen |

Picking a theme swaps the frontend, so a theme dresses all of it or none of it.
There is no per-surface fallback: half a theme — a beach dashboard behind the
stock purple splash — is the exact look this rule exists to prevent.

A themed splash draws what it likes but does not decide when booting ends: it
must call `onDone`, and the host treats the animation as over regardless after
20s.

**SDK 4 — the boot ground.** Declare in `theme.json` the colour your splash
paints first:

```json
"boot": { "background": "#F4F2ED" }
```

The shell reads it **from disk before your bundle exists** — before the backend
has answered anything — and paints it from the window's very first frame, on
the local boot screen, in the frame between that screen and the interface, and
on the interface page itself (`frontend/index.html`, before any module runs),
and on the cover the host holds under your splash. Get it wrong and the player sees
a flash of the wrong colour at every boot; leave it out and you get `#09090f`,
which is what every theme got before this existed.

Shelf declares `#F4F2ED`, the paper its splash starts on. Summer declares its
night sky: its own gradient is computed from the clock, so no constant can
match it at every hour — the sunrise rising out of a dark screen is the
intended reading rather than a flash to be hidden.

**SDK 4 — the boot contract.** Your splash receives `bootReady` as well as
`onDone`. An animation has a duration; a box being usable does not, and the two
used to be the same event: the splash ended, the dashboard appeared, and
whether there was anything on it was a matter of machine speed. So:

```js
return ({ onDone, bootReady }) => {
  const allowed = bootReady !== false      // an older host passes nothing
  // …play the animation, then hold its LAST frame…
  const leaving = useRef(0)
  useEffect(() => () => clearTimeout(leaving.current), [])
  useEffect(() => {
    if (leaving.current || !finished || !allowed) return
    setOut(true)
    leaving.current = setTimeout(onDone, FADE_MS)   // armed once
  }, [finished, allowed, onDone])
}
```

Three things in that shape are load-bearing:

* **`!== false`**, not `bootReady`. A front end older than SDK 4 passes no prop
  at all, and a splash waiting for one that never arrives is a box that never
  boots.
* **Hold the LAST frame, not the first.** The held frame is the finished
  picture — the cartridge seated, the sun up — so a box that was ready long ago
  holds it for one frame and adds nothing to the boot.
* **Arm the exit once, from a ref.** Writing it as an effect's own cleanup
  cancels the timer the effect just armed as soon as any dependency changes;
  Summer's splash did exactly that for one commit and stopped at its last frame
  for good.

`sdk.system.splashHoldMs` is **gone in SDK 4**. It held the first frame for a
fixed 4000 ms whenever the machine had booted recently, because the display
path — the X mode switch, then the TV's HDMI re-sync — is still black when the
splash mounts. The reasoning was right and the mechanism was not: a duration
measured on one box, applied to every box. A television still dark is a
television with nothing to miss; the host now waits for the interface, and
tells you.

### What is mandatory, and what is not

The all-or-nothing rule is about **surfaces**, not features. Read it as:

| | Mandatory? |
|---|---|
| `splash` and `shell` — declared in `provides` **and** exported by the module | **yes.** Miss either and the theme does not load at all |
| Which parts of the shell you override (`homeView`, `library`, `topbar`, the modals…) | **no** |
| `sessionBar` — exported only, never declared | **no.** The host draws one either way (§5f) |

So a theme that ships a splash and a shell, and overrides only the dashboard, is
a perfectly valid theme: everything it did not rewrite is the default UI,
rendered *inside the theme's own shell*. You are never mixing two frontends —
there is one tree, and you decide how much of it you author.

What that buys you is the failure mode: there is no half-loaded theme. Either
your theme runs, or the default one runs whole, and Settings → Themes tells you
which and why.

### Composing instead of rewriting

`sdk.defaults.Shell` **is** the default frontend, and it takes parts:

| Part | Replaces |
|---|---|
| `background` | a full-screen layer the shell places behind everything |
| `decor` | a full-screen layer above everything, non-interactive |
| `topbar` | clock, IP, storage, controller battery |
| `homeView` | the dashboard's **markup** (see below) |
| `homeGameOptions` | SDK 10: return `{systemId, rom, title}` for the focused home game, or `null` to open Settings; the host opens its game options panel on Options |
| `libraryView` | the game list, detail panel and metadata — **markup only**, like `homeView` |
| `homeOmit` | home-screen shortcuts you bind yourself: `'nav'` (d-pad), `'pages'` (L1/R1), `'confirm'` (✕). Take one and you own what it does — see §5g |
| `libraryOmit` | the same, for the library: `'nav'`, `'confirm'`, `'sort'` (L1/R1). The per-game options on ≡ are not omittable (§6a) |
| `screensaver` | the standby screen. The host's slideshow by default; `sdk.defaults.CrtStandby` (SDK 11) is the CRT room, see below |
| `settings` | the settings screen |
| `powerView` | the power menu's markup — the two-press confirmation, the pending lock and the failsafe stay with the host. Dress the host's with `sdk.defaults.createPowerView(sdk, { skin, layout })` (§5h) |
| `gamepadView` | the controller screen's markup — the live pad diagram arrives ready-made and bound. Dress the host's with `sdk.defaults.GamepadView` (SDK 9, §5h) |
| `toasts` | the notification stack's markup — the queue, the durations and the handover to the native HUD stay with the host |

A theme may also return an optional `ceremony` component beside `shell` and
`splash`. The host places it in a full-screen non-interactive layer above the
shell; it reads `sdk.nav.use(s => s.transition)` to draw `launch`, `resume`, or
`suspend`. The host owns the timing and stacking, so the component needs no
`z-index`.

Another optional one, `whoIsPlaying`: the start screen's dress, built with
`sdk.defaults.createWhoIsPlaying(sdk, { skin })` (§5i).

So "add snow to the dashboard" is a shell that renders `sdk.defaults.Shell` with
a `decor`, and "replace everything" is a shell that renders its own tree. Same
mechanism, effort proportional to ambition.

### Views, not screens

`homeView` and `libraryView` are those screens' *markup*, not the screens.
Paging, focus, sorting, search, launching and the d-pad bindings stay in the
host and are handed to the view as props — see
`frontend/src/components/HomeScreen/types.ts` and `LibraryScreen/types.ts`.
The library view is even given the cover-art and metadata components ready-made,
so a theme never reimplements the missing-art or 404 paths.

That seam is deliberate. When themes reimplemented navigation they drifted from
the default in ways nobody notices until a TV is involved — running off the
right of a row stopped dead instead of turning the page. **A view that cannot
navigate cannot navigate differently.** The rule the whole system rests on:
a theme changes the UI, never the behaviour.

### One feature per file

The default frontend is one file per feature (`WifiPage.tsx`, `PowerModal.tsx`,
`GamepadModal.tsx`…) and a theme should be too — see `config/themes/summer`:

```
summer/
  index.js          the wiring, and nothing else
  theme.json  theme.css  preview.png  README.md
  views/            splash.js  home.js  library.js  topbar.js
                    settings.js  themes.js  background.js  decor.js
  lib/              ocean.js  idle.js
```

The directory listing doubles as the check-list of what you still have to dress.

It splits the way the frontend does — what a screen looks like, and what it
needs to look like that — but it is flatter, and deliberately. There is no
`components/HomeScreen/` because a theme supplies only the *view* of a screen:
that folder would hold exactly one file. And there is no `hooks/`, `lib/`,
`store/` or `api/` layer at all — a quarter of the frontend's tree — because a
theme gets all of it from the SDK.

Subfolders work (they are served as-is, and relative imports resolve normally),
so a larger theme can nest further. Just remember there is no build step: the
path you write is the path the browser fetches.

### Why one surface and not nine

The first version substituted nine components inside the host's own layout, so
the theme and the default were **interleaved in one tree**. Every bug came from
that:

- a theme's background painted over the screens it had *not* replaced, because
  stacking was left to the theme's CSS;
- a themed modal never joined the modal stack, so the dashboard kept receiving
  the d-pad behind it and the cursor moved in two places at once;
- default settings pages were torn out of the `Overlay` they were written for
  and dropped into the theme's own box, which broke their layout.

One tree, one owner. The shell owns the stacking (a theme never writes a
`z-index`), and the shell registers whatever it shows as a modal — for default
and themed alike, so forgetting is not possible.

## 5b. Asking for a different dashboard grid

The dashboard is `cols × rows` cards per page — 4 × 2 unless the manifest says
otherwise:

```json
{ "id": "shelf", "home": { "cols": 8, "rows": 1 } }
{ "id": "shelf", "home": { "rows": 1, "paged": false } }
```

`"paged": false` puts the whole list on one page: no L1/R1, no boundary to walk
into, and `cols` is then derived from how many items there are rather than
declared. A number could not do that job — it would be right until the owner
installs one more system.

Absent, it is the host's grid, which is what every theme written before this
said by saying nothing. Naming only one of the two is fine; the other keeps the
host's value.

**Why this is negotiable when so little else is.** The grid is layout, and
layout is the theme's side of the line. A theme that wants one long row of big
icons cannot fake it: `HomeScreen.navigate()` walks the grid and wraps at the
row end, so a rail drawn as one continuous line would silently skip half its
contents whenever `rows > 1` — and a row that lies about where the cursor goes
is worse than a visible second row. What stays the host's is everything the
grid is walked *by*: paging, focus, wrap, the bindings.

`cols` and `rows` must be integers 1–16. Outside that they are dropped with a
warning and the host's value stands: a theme is code its owner installed, but 0
divides by zero in `pageCount` and 400 asks the host to render every system on
one page. Neither is a look; both are a broken screen. A bad value does not
take a good one down with it — `{"cols": 8, "rows": 0}` yields `cols` 8 and the
host's rows.

> A page holds `cols × rows`, so `8 × 1` keeps the 8-per-page of the default
> `4 × 2`: one row of eight instead of two rows of four, and L1/R1 behave
> exactly as before.

## 5c. Giving your launch animation time to finish

Your `libraryView` receives `launching`, and most themes answer it with a boot
animation. The host used to start the emulator on the same tick it raised that
flag, so the game's window arrived over the top of an animation that had barely
begun — a cartridge still going into its slot when the screen was taken.

You cannot fix that from your side: you never call the launch, you only watch
the flag. So say how long you need, and the host waits:

```json
{ "id": "shelf", "launch": { "ms": 1520 } }
```

Absent, the game starts immediately — which is what every theme did before this
existed, and what the default view still does, because it draws no ceremony and
a delay there would be dead air.

`ms` must be an integer 0–5000. Outside that it is dropped with a warning and
you get no delay: a theme is code its owner installed, but a minute of ceremony
is a console that ignores the button for a minute.

Count the whole animation, not the part you like. The value is the delay before
the game is asked to start — the emulator then takes its own time to appear, so
erring a little short costs nothing and erring long is felt as lag on every
launch.

**○ still means ○.** Waiting opens a window where the player can leave the
screen with a launch already promised. Pressing back during your animation
cancels it: the request is never sent, and the flag is cleared under you. Do
not treat `launching` going false as "the game is running".

## 5d. If you write your own settings menu, say what it opens

The settings sub-pages are the host's, and your menu resolves each entry
through `sdk.defaults.DefaultSettingsPages`. Leaving one out costs nothing at
load and shows nothing on screen: the page is still there, its route is still
there, and there is simply no way to get to it.

That has shipped twice. `catalog` was missing from the map, so **neither**
bundled theme had any way to install an emulator. `storage` was missing from
the map itself, so no theme could offer safe-eject for an external disk even if
its author had thought of it. Both were found by reading the code, not by a
player — which is the problem, because the pages people go looking for are the
ones they need when something is already wrong.

So declare what your menu reaches:

```json
{ "id": "shelf", "settings": { "pages": ["wifi", "audio", "bluetooth", "standby",
                                         "catalog", "bios", "storage", "themes",
                                         "update", "desktop"] } }
```

The host compares that to `Object.keys(sdk.defaults.DefaultSettingsPages)` and
names anything you have left unreachable, in Settings → Themes. CI does the
same:

```bash
node scripts/check-theme.mjs config/themes/<id>
```

**Say nothing and nothing is checked** — that is the right answer for a theme
that reuses the host's settings modal, which reaches everything by definition.
Warning about those would make the check noise, and a check that cries wolf is
how the first two got through.

Declaring the empty list is not the same as saying nothing: it means your menu
opens none of them, and you will be told so for every page.

## 5g. When your dashboard is not a grid of consoles — `homeOmit`

The home screen's cursor walks the **system grid**: the d-pad moves
`gridFocusIdx` across the systems on the page, L1/R1 turn the page, and ✕ opens
whatever the cursor is on. `homeView` is markup over that, which is the right
shape for a dashboard that IS a grid of consoles — the default one, Shelf,
Summer.

It is the wrong shape for a dashboard that is not. Orbit's home is three tabs:
a rail of recently played games, a grid of consoles, a rail of applications.
Only the middle one is the host's model. Left as it was, the host's d-pad moved
a selection nobody could see — the cursor landing on a console while the screen
showed a game — and ✕ then opened that console.

So the home bindings are droppable, the same way the library's sort is:

```js
html`<${sdk.defaults.Shell} homeView=${Home} homeOmit=${['nav', 'pages', 'confirm']} />`
```

| id | what you take |
|---|---|
| `nav` | the four d-pad directions |
| `pages` | L1 / R1 |
| `confirm` | ✕ |

**Dropped entirely, not deferred.** Both sets of handlers would otherwise fire
on the same press. And taking one means owning everything behind it: `confirm`
in particular is what opens a console (`sdk.nav.goLibrary`) and what launches an
application (`sdk.defaults.launchApp`), so a theme that takes ✕ has to do both.

Take nothing and nothing changes — this is opt-in, and the two shipped themes
whose dashboards *are* system grids take none of it.

## 5f. The suspended session, and the bar over it — SDK 5

Pressing Home twice used to **kill** the running game. It suspends it now: the
process group is stopped, the interface comes back with the controller working,
and closing the game is a separate, deliberate action. Two accidental presses
cost an unsaved save before; they cost nothing now.

That gesture belongs to the core — `gp:guide` is in the reserved set precisely
so no theme can take the one binding that gets a player out of a game — so the
way back belongs to the core too. **The host always mounts a session bar.** A
theme exports `sessionBar` to replace the picture, never the guarantee.

### What you get

```js
const SessionBar = ({ sessions, focusIdx, active, busy, title,
                      onFocus, onResume, onClose }) => { … }

return { splash, shell, sessionBar: SessionBar }   // not in `provides`
```

| Prop | |
|---|---|
| `sessions` | the suspended sessions, oldest first. Never empty when this renders |
| `focusIdx` | which one the cursor is on |
| `active` | the bar owns the pad right now — draw your cursor from this |
| `busy` | a resume or close is in flight |
| `title(session)` | the name the library would show, not the ROM filename |
| `onResume(s)` / `onClose(s)` | act on that session, by its run number |

Each session is `{ gameKey, systemId, session, kind }`. **`kind` is `'app'` or
`'game'` — say which.** An application launches with `game_key === system_id`
and that identity is the only thing telling the two apart once the session
exists; a bar offering to close a "game" the player never started is the
interface talking about something that does not exist.

Only one game may be resident. Selecting that same game calls `foreground` and
resumes it; selecting a different game opens the host-owned “Another game is
still running” dialog. The dialog uses the same `--gc-overlay-*` variables as
settings, so themes style it without reimplementing the launch rule. Suspended
applications may still coexist with a game, hence the plural `sessions` API.

**The bar takes no buttons at all.** It is drawn over a live screen, and that
screen's ✕ belongs to that screen: an earlier version borrowed it, so one press
resumed the session *and* opened whatever tile the cursor was on. The bar is a
picture and a pointer affordance; the keys live in the menu.

### The menu — `sessionMenu`

**PS pressed twice opens it** (outside a game), and it is a real modal:
`openModal()` raises `modalDepth`, so every host screen stands down and the pad
is unambiguously the menu's. That is also what gives Close somewhere to ask
first. On a pad whose guide button never reaches the box, the power menu (Share)
leads with an *In the background* row that opens the same menu.

It used to open on L2, while something was suspended. Shelf flips its box with
L2, so one press did both. PS is the button that suspended the game, and it is
reserved (§6a): no theme can take it.

```js
const SessionMenu = ({ session, sessions, index, confirming, busy,
                       actions, actionIdx, title }) => …
return { splash, shell, sessionBar, sessionMenu: SessionMenu }
```

`actions` arrives already resolved — `{ id, label, danger?, primary?, run }` —
and `actionIdx` says which one the pad is on. Draw them and call `run` on click;
**do not bind ✕ or the d-pad yourself**, the host is already holding them.
`confirming` is true once Close has been chosen and is waiting to be confirmed.

Without this, closing a session was reachable with a pointer and nothing else,
which on a console is not reachable at all: a player could resume a session for
ever and never end one.

**You do not position this.** The host supplies the layer, fixed to the bottom
with the z-index you are not allowed to write (§6), and your markup draws
inside it — the same bargain the themed splash has.

### Driving it yourself

`sdk.session` is there when a theme wants more than the bar:

| | |
|---|---|
| `sdk.session.use()` | reactive, inside a component → `{ foreground, background }` |
| `sdk.session.get()` | snapshot, in a handler |
| `sdk.session.background()` | suspend what is on the screen |
| `sdk.session.resume(id?)` | wake one; with no id, the most recent |
| `sdk.session.close(id?)` | end one; with no id, the one on the screen |

Every rule about them is the core's. How many sessions may exist, what happens
when one is resumed while something else holds the screen, and whether a launch
is refused are not a theme's decisions — a theme making its own would be a
second answer to a question that has to have one. These calls reject with the
host's own sentence when it refuses; show it or ignore it, but do not pretend
the action worked.

**Touching any of `sdk.session` means `"api": 5` in your manifest.** The version
gate reads your sources, computes the level you actually use, and refuses the
theme if the manifest declares less — an older front end would otherwise call
it compatible and then throw on the first read.

Taking the bar as *props only* does **not** need SDK 5, and both shipped themes
deliberately stay at 4 for that reason: a front end without the lifecycle
simply never mounts the bar, and the theme keeps working. Declaring 5 for a
surface you do not call would take your theme off that box for nothing.

### One thing that is not yours

`sessionGameKey` still means **"a game owns the screen"** and still blocks the
pad. A suspended session sets it to `null` — that is what gives the interface
back to the player while the game stays alive — so anything you key off it
keeps working unchanged.

## 5h. The power menu and the controller screen — dress, don't rewrite (SDK 9)

Both screens are **one markup each**, and a theme restyles it from its
stylesheet. That is how all three shipped themes do it; writing your own view
is still allowed, and then every prop below is yours to honour.

**Power** — `sdk.defaults.createPowerView(sdk, { skin, layout })`:

| Option | Effect |
|---|---|
| `skin` | a class on the scrim (`gcs-pwr-scrim <skin>`) your CSS keys on |
| `layout: 'row'` | rows side by side (Summer) and a ← → hint; the host moves the cursor on ← → as well as ↑ ↓, so nothing else changes |

Rows, in order: *In the background* (only while a session is suspended),
`rest` (Rest mode, where the cursor opens), `shutdown` (Turn off), `restart`,
`desktop`. Summer's row layout holds up to five tiles.

Hooks: `.gcs-pwr`, `.gcs-pwr-title`, `.gcs-pwr-list`, `.gcs-pwr-sep` (the
rule after *In the background*), `.gcs-pwr-row[data-id][data-on][data-confirm][data-dim]`,
`.gcs-pwr-icon`, `.gcs-pwr-text b/i`, `.gcs-pwr-hint`. No Cancel button: ○ closes,
and a pointer closes by clicking outside the panel.

**Controller** — `sdk.defaults.GamepadView`, handed every `gamepadView` prop:

```js
const Controller = (props) => html`<${sdk.defaults.GamepadView} ...${props} skin="my-pad" />`
```

Repaint it with CSS variables on `.<skin>.gcs-pad-scrim`. The panel's:
`--pad-scrim`, `--pad-bg`, `--pad-shadow`, `--pad-radius`, `--pad-ink`,
`--pad-muted`, `--pad-line`, `--pad-accent`, `--pad-control`, `--pad-hover`,
`--pad-ok`, `--pad-warn` (unset ones fall back to the built-in UI's). The diagram's: `--pd-body`, `--pd-body-line`, `--pd-center`,
`--pd-part`, `--pd-line`, `--pd-lit`, `--pd-lit-line`, `--pd-knob`,
`--pd-well`, `--pd-label`, `--pd-pos` (the legend's face symbol: keep it
≥ 3:1). Hooks: `.gcs-pad-*`, with `.gcs-pad[data-pads][data-state]` and
`.gcs-pad-roster > div[data-active]`. `callouts` is accepted and ignored.

The props a view gets, new in SDK 9:

| Prop | What |
|---|---|
| `pads` | every connected pad `{index, player, name, connection, battery, charging, known, raw, active}` |
| `pad` | the one being read: the last pad that did something deliberate. □ on pad 2 opens the screen on pad 2 |
| `status` | `{tone: 'ok'\|'warn', text}` — recognised, known-pads table, mapped on this box, not recognised, or raw |
| `missing` / `absent` | the sentence and the list of controls this pad lacks |
| `actions` | `{pos?, label?, keys?, action}` — what each position and key does |
| `Position` | the legend's icon: four dots, one filled (`<${Position} pos="north" />`) |

`layout`, `layoutLabel`, `glyphs` and `mappings` are still filled for views
written before SDK 9 (`layout` is always `'generic'`). The drawing is by
position, so there is no family to pick any more.

## 5i. Profiles — one name to draw, one thing to check

**Player labels.** A pad linked to a profile (the profile's page →
Controllers) is shown by that profile's name, whichever profile is active.
Wherever a theme writes a player number, it draws `sdk.players.useLabel()(n)`
instead: `"Max"` for a slot whose pad is Max's, `"P<n>"` for any other, and
`"P1"` to `"P4"` on a box without profiles. `sdk.players.label(n)` is
the one-shot read for a handler. A theme that must load on an older host guards
it: `const usePadLabel = sdk.players?.useLabel ?? (() => (n) => \`P${n}\`)`,
as the four shipped themes do in their status bars.

**The profile picture.** The shell hands a top bar `onProfile` beside
`onSettings` and `onPower`; it opens Settings straight on Profiles. Draw
`<${sdk.players.Avatar} className="my-avatar" onClick=${onProfile} />` next to
the Settings and Power buttons: the active profile's colour with its picture
or initial, and nothing at all on a box without profiles, so the bar looks as
it did. `.gcs-avatar` gives it its round shape at 40 px; size it with your
class. The four shipped themes put it at the right end of their top bar.

**Per-profile storage.** What a theme keeps for a person in `localStorage`
(favourites) is per profile: read and write it under
`sdk.players.storageKey('my-favourites')`, which is the bare key for the
primary profile (so what it had before profiles stays its own) and
`my-favourites:<id>` for another, and read it again in
`sdk.players.onChange(fn)`. Jelly and Orbit do this in `followProfiles(sdk)`.
Playtime and recents need nothing: `/api/playtime` already answers for the
active profile, and a switch sends `playtime:rekeyed`.

**"Who's using this controller?"** (2+ profiles, "Log in automatically" off,
once per interface start) is drawn by the
host over your shell, under the splash before the boot ends, so your splash
hands over straight to it, from `frontend/src/components/WhoIsPlaying.tsx`. Dress it
by returning `whoIsPlaying: sdk.defaults.createWhoIsPlaying(sdk, { skin: 'my-who' })`
with your surfaces and styling `.gcs-who.my-who` (and `.gcs-who-kb.my-who`, the
keyboard of "Add profile") in your stylesheet: the `--set-*` palette, the
background, the focus ring. When it shows, what a pick does and the pad stay
the host's; without it the host draws its own dark one. The four shipped
themes each do it in `css/who.css`. It raises `modalDepth` while it is
up, so a theme that already pauses its pad handlers on `modalDepth` (§5f) needs
nothing.

**Settings → Profiles** is the tenth category of the shared settings screen
(`frontend/src/settings/profiles.js`), drawn with the same `gcs-row2` rows as
System; a round `.gcs-row2-badge` carries each profile's picture or initial on
its colour. Each profile row is a `choice` row: Select and Edit (Edit only for
the profile playing), ←/→ between them, the one ✕ presses ringed
(`.gcs-choice .gcs-act[data-pick="1"]`). A theme that styles `.gcs-act` styles
them already.
It is not a `DefaultSettingsPages` id, so `settings.pages` in `theme.json` does
not change. Check one thing: a stylesheet that keys the rail by position
(`.gcs-set-row:nth-child(n)`) needs a tenth entry, and the rail has to fit ten
rows at 1080p. Jelly 1.4.4 does both.

## 5e. Check it loads before you ship it

```bash
node scripts/check-theme.mjs config/themes/<id>
```

It imports every module the way the browser will. `node --check` is **not**
enough, and the gap is not theoretical — it has bitten twice:

```js
html`
  <!-- `key` on this node restarts the animation -->
  <div class="hold" key=${id}>
`
```

A backtick inside an HTML comment inside an `html``` template closes the
template early. The file still parses, so `node --check` passes; the module
throws `Unexpected identifier` the moment it is loaded, and the theme is
disabled at runtime with no clue as to which line.

**So: no backticks inside HTML comments.** Write `key` instead of `` `key` ``.
The same goes for `${...}`, which is interpolation wherever it appears —
comment or not.

## 6. What a theme can do

The whole surface, in one table. Everything marked **no** is a deliberate line,
not a gap waiting to be filled — the reasons are in §11.

| | |
|---|---|
| **Redraw every screen** | yes — dashboard, library, top bar, settings menu, theme picker, power menu, controller screen, screensaver, boot animation |
| **Paint behind and in front** | yes — `background` and `decor` are full-screen layers the shell places for you |
| **Ship a stylesheet** | yes, and it is the only place in GameCore where CSS works: the default UI styles itself inline |
| **Restyle the screens it did *not* rewrite** | yes — the Wi-Fi, Bluetooth, audio and update pages read `--gc-overlay-*` and `--gc-accent*` (§7) |
| **Read the box's real data** | yes — systems, games, playtime, metadata, cover art, storage, network, controllers and their battery, through `sdk.api` |
| **Draw something other than the jacket** | yes — 3D box, clear logo, gameplay screenshot, title screen, ready-made mix, trailer, 54 types in all (§7.1) |
| **React to what happens** | yes — `sdk.system.onWsEvent` for standby, ROM uploads, controller connect/disconnect, battery, theme changes |
| **Draw the notifications** | yes — `toasts` is a part like any other. What each one *says* and how long it stays is the host's |
| **Make the pad buzz** | yes — declare a `rumble` table and the bus fires it (§13b). Nothing vibrates unless a theme asks |
| **Read input** | yes — `sdk.input.onGp` for every pad event, `useGamepadState()` for the raw 60 fps state (that is how a live pad diagram works) |
| **Know where the player is** | yes — `sdk.nav.use()` inside a component, `sdk.nav.get()` in a handler |
| **Move the player** | yes — `goHome`, `goLibrary`, `setGridFocus`, `setGridPage` |
| **Make sound** | yes — `sdk.system.getAudioContext()` for its own synthesis (Summer's surf), `sdk.system.playSound()` for the host's set, `sdk.system.sound` to respect the player's setting |
| **Ship assets** | yes — anything in the theme folder, resolved with `sdk.system.asset('…')` |
| **Change the five UI sounds** | yes — declare them in `sounds` and the bus plays yours instead (§13). What stays the host's is *when* they fire |
| **Change how a screen behaves** | **no** — paging, focus, sorting, search, launching, the shutdown confirmation. It supplies the markup, the host keeps the decisions |
| **Write a `z-index`** | **no** — the shell owns stacking. This is what let the first version paint over screens it had not replaced |
| **Skip the boot animation** | **no** — a theme draws its own, but `onDone` is the host's and a 20s watchdog sits behind it |
| **Take the global buttons** | **no** — PS, ≡ and Share are the host's on every screen (§6a) |
| **Take the rescue combo** | **no** — L1 + R1 held 2s forces the default theme, from anywhere |
| **Remove itself from the picker** | **no** — Settings → Themes is always reachable, so a theme can always be left |
| **Reach the network or the DOM outside its tree** | **no** — see §11 |

## 6a. The host's buttons, and the theme's

A button does one thing per screen. The host's global actions sit on buttons no
theme can bind, so a theme cannot run a second action on the same press, and
`homeOmit` / `libraryOmit` have no id that reaches them.

| Button | Event | Owner | Does |
|---|---|---|---|
| PS / Guide ×2 | `gp:guide` | host, reserved | in a game: suspend it. In the menu: the session menu (§5f), or home when nothing is suspended |
| Options / Start (≡) | `gp:menu` | host, reserved | on a library game or a focused home game supplied by `homeGameOptions`: that game's options (per-game emulator settings). Otherwise: Settings |
| Share / Select | `gp:power` | host, reserved | the power menu, which lists suspended sessions first |
| □ | `gp:x` | host | the controller screen |
| △ | `gp:y` | host | search, in the library |
| L1 / R1 | `gp:l1` `gp:r1` | host, omittable | pages at home, sort in the library (`homeOmit: 'pages'`, `libraryOmit: 'sort'`) |
| L2 / R2 | `gp:l2` `gp:r2` | **theme** | nothing from the host. Shelf turns the box (L2) and restacks (R2) |

`sdk.input.onGp` refuses the three reserved events with a console warning and
they are absent from `sdk.input.events`. Advertise the host's buttons in your
hints with `<PadKey k="Options" />` and `<PadKey k="PS ×2" />`.

Why: the per-game options were on R2 and sessions on L2. Shelf binds both, so
it dropped the options (no bezel picker on Shelf at all) and L2 flipped the box
*and* opened the session menu. SDK 8 moved them and reserved the buttons.

## 7. The SDK

The module receives **one argument**. It imports nothing from the host, so
there is no import map to maintain and only one React instance exists.

| Namespace | Contents | Detail |
|---|---|---|
| `sdk.ui` | `html` (tagged template), `React`, `useState`, `useEffect`, `useRef`, `useMemo`, `useCallback`, `motion`, `AnimatePresence` | Framer Motion is already bundled by the host |
| `sdk.api` | `systems`, `games`, `metadata`, `media`, `playtime`, `sysinfo`, `standby`, `update`, `wifi`, `audio`, `bluetooth` | [full signatures](../architecture/05-frontend.md#apiindexts) |
| `sdk.nav` | `use(selector)` for a reactive read inside a component, `get()` for a snapshot in a handler, plus `goHome`, `goLibrary`, `setGridFocus`, `setGridPage`, `setSelectedGameIdx`, `openModal`, `closeModal` | [store reference](../architecture/05-frontend.md#store--storeindexts) |
| `sdk.input` | `onGp(event, handler)`, `useGamepadState()`, `GP_BTN`, `events`, `rumble(pattern)`, `haptics` (read-only `enabled`) | [event bus](../architecture/05-frontend.md#the-gamepad-event-bus--hooksusegamepadts) |
| `sdk.session` | `use()` reactive, `get()` snapshot, `background()`, `resume(id?)`, `close(id?)` | **SDK 5.** Suspending and resuming a game or application — §5f. Touching any of it means declaring `"api": 5` |

**SDK 6** adds three things a theme that draws its own library grid needs
together: `onOpenSearch` and `onOpenOptions` on `libraryView` — the routes to
the host's search keyboard and per-game options, for a theme that draws its
own buttons for them —
and `__all__` as a system id, the cross-console library. Touching any of them
means `"api": 6`: on an older host the first two are `undefined` and the
theme's own buttons throw, and `__all__` is a console that does not exist, so
the screen renders empty and the tab looks broken rather than bare.
| `sdk.system` | `onWsEvent`, `playSound`, `getAudioContext`, `sound` (read-only `enabled` / `volume`), `gamecore`, `asset(path)` | `asset()` resolves a path inside the theme folder. `splashHoldMs` was here and is gone in SDK 4 — the splash is told when the interface is ready instead of being told how long to wait (§8) |
| `sdk.format` | `gameName`, `time`, `date`, `hexToRgb`, `systemColor`, `playedAgo` (SDK 12) | how the rest of the UI renders the box's data. Reimplementing these does not fail, it *drifts* |
| `sdk.themes` | `list()`, `select(id \| null)` | so a theme can dress its own theme picker. `select()` is the host's: it clears safe mode, resets the crash count and reloads the frontend |
| `sdk.defaults` | `Shell` (the default frontend, takes parts), every screen, `DefaultSettingsPages`, `SettingsOverlay`, `Label`, `BackBar`, `DefaultKeyboard`, `launchApp` | compose instead of rewrite. The pages already carry their own overlay — render them bare; `SettingsOverlay`, `Label` and `BackBar` are the chrome to build a page of your own that matches them |

SDK 7 adds `sdk.defaults.launchGame({systemId, path, gameKey})` for a ROM
already listed by `sdk.api.games.list`. It plays the theme's launch handover,
preserves navigation and selection, guards repeated calls, and sets
`transition: 'launch'` while the request is pending. The returned promise
rejects on failure so the view can display the error. Declare `"api": 7`
when using it. The host waits for the validated `launch.ms` duration before
contacting the backend, just as it does from Library. Without a declared
duration the request is immediate. The theme API preserves the manifest's
`launch: {ms: number}` shape; an absent or invalid duration is `null`.

SDK 9 adds `sdk.defaults.GamepadView` and the roster props of `gamepadView`
(§5h). A theme that dresses `GamepadView` renders nothing on an older host, so
it declares `"api": 9`.

SDK 10 adds `homeGameOptions` to `sdk.defaults.Shell`. Themes using it declare
`"api": 10`; the host keeps the game options panel and modal controls.

SDK 11 adds `sdk.defaults.CrtStandby`, the CRT standby: a rendered room at
night whose TV plays the library's game clips (`GET /api/standby/videos`) and
lights the room with their colour. Pass it as the Shell's `screensaver`:

```js
const Standby = sdk.defaults.CrtStandby
  ? () => html`<${sdk.defaults.CrtStandby} skin="my-standby" favourites=${() => keys} />`
  : undefined   // an older host keeps its slideshow
```

`skin` is a class on the root; set the caption's `--crt-cap-font`, `-bg`,
`-ink`, `-ink-2`, `-ink-3`, `-radius` and `-shadow` on it (Orbit:
`css/standby.css`, Shelf: a paper label). `favourites` returns
`system_id:filename` keys that come up more often. Feature-detect it as above
rather than declaring `"api": 11`: a theme that requires it would drop to safe
mode on an older host instead of keeping the slideshow.

SDK 12 adds two helpers for a theme that draws its own standby instead of
`CrtStandby`: `sdk.defaults.useLocalWake(stage)`, the mouse/key wake the
host's standbys use (call it with `sdk.nav.use(s => s.standby)`; pads are
woken by the host), and `sdk.format.playedAgo(lastPlayed, now)`, the CRT
caption's "played 3 days ago" (null when never played). Jelly's standby
(`config/themes/jelly/views/standby/`) uses both and feature-detects them
like `CrtStandby`: on an older host it returns no `screensaver` and the
slideshow stays.

SDK 8 removes things rather than adding them: `gp:menu` and `gp:power` from
`sdk.input` (reserved, §6a) and `'options'` from `libraryOmit`. No theme needs
to declare `"api": 8`; a theme that bound either event just stops receiving it.

`DefaultSettingsPages` is deliberately not enumerated here. It gained `catalog`
and `bios` after this table was written and the table did not follow, so the one
document a theme author reads to find out what exists listed seven of the nine
pages that did. `Object.keys(sdk.defaults.DefaultSettingsPages)` is the list, at
runtime, and it is the only copy of it that cannot go stale.

`sdk.defaults` has the same property, one level up: it is the whole
`components/defaults.tsx` namespace, not a hand-picked subset, so the row above
is a guide and `Object.keys(sdk.defaults)` is the truth. `Label` and `BackBar`
are what that gap looks like in practice — both have been exported and usable
the whole time, and both were missing from this table until the remake used
them.

`modalDepth` and `powerPending` are readable through `get()` but there is no
setter: they are the core's focus and shutdown locks.

`sdk.input.onGp` refuses `gp:guide`, `gp:menu` and `gp:power` — the host's
global buttons, §6a.

`sdk.defaults` is what makes "add a Santa on top of the existing dashboard" as
cheap as "rewrite everything" — override `homeView`, render the default inside it,
add your layer.

### 7.0 Pictures of a console — `system.art`

A theme does not ship console photos. Each pack does, in
`catalog/<id>/art/<name>.<ext>`, and every row of `sdk.api.systems.list()`
carries them as `art: { name: url }`. The name says what the picture is
(`console` is the hardware photo, the only one every console pack has); the
theme picks the name it wants and falls back when a pack has none:

```js
const photo = system.art?.console || logoOf(system)
```

Changing a photo is changing that one file in the pack: no theme to edit, and
every theme picks it up. An operator replaces one on a box without touching
the release by dropping `assets/art/<id>/console.png` (it wins, and updates
leave it alone). Orbit and Jelly read `art.console`; neither carries a copy.

### 7.1 Artwork other than the jacket

The default library draws a flat box front, because that is what every game has.
A theme is not tied to it: a game carries up to 54 media types, and any of them
can be the thing your library is built on.

Two ways in, and the first one covers most themes:

```js
// The Cover component you already receive takes an optional type.
html`<${Cover} filename=${game.filename} systemId=${systemId}
               color=${color} type="box-3d" />`
```

Leave `type` out and it draws the jacket from `/api/covers` exactly as before —
same URL, same cache, same fallback. Pass one and it draws that instead, falling
back to the jacket when this particular game does not have it. That fallback is
the point: the 3D box is a rarer artwork than the flat scan, and a theme built
on it would otherwise show a hole for every game nobody photographed in
perspective.

```js
// Or ask what a game actually has, and decide.
const index = await sdk.api.media.list(systemId, game.filename)
// index.media = { "box-3d": { category: "box", kind: "image",
//                             region: "wor", cached: true }, … }
const hero = index.media['mix-rbv2'] ? 'mix-rbv2' : 'box-front'
const src  = sdk.api.media.url(systemId, game.filename, hero)
```

The types worth knowing, by `category`:

| Category | Useful slugs | What it gives you |
|---|---|---|
| `box` | `box-front` `box-back` `box-3d` `box-spine` | the jacket, and **the box in perspective** — transparent background, irregular ratio, so do not force it into a fixed-ratio frame |
| `logo` | `clear-logo` `clear-logo-hd` | the game's logo cut out on transparency — what a title over a background wants |
| `screenshot` | `screenshot-gameplay` `screenshot-game-title` | in-game capture, title screen |
| `mix` | `mix-rbv2` `mix-rbv1` | **ready-made compositions** (box + screenshot + logo) built for TV grids. Often the best single image to show, and the cheapest way to look designed |
| `video` | `video-normalized` `video` | trailer or gameplay clip, ~15 MB `.mp4`. `video-normalized` is the better default: consistent format and loudness |
| `artwork` | `fanart-background` `square` | wallpapers, square thumbnail |
| `marquee`, `icon`, `bezel`, `document`, `theme`, `pinball` | | banners, list pictograms, 4:3 side fillers, the `.pdf` manual, Hyperspin themes, virtual pinball art |

Three things to design around:

- **Nothing exists for every game.** `available: false` on the index means the
  box has no ScreenScraper account and no offline index — not that the game is
  unknown. Draw the jacket and move on; do not show an error.
- **The first display of a type costs a round trip.** Media are fetched on
  demand, so `cached: false` means one download. On a slow link, prefer a type
  the index reports as cached for anything that has to appear instantly, and
  let the fancy one arrive after.
- **A video is not a cover.** 15 MB per game, and the library selection moves
  with the d-pad. Debounce it the way the host debounces `detailGame` (150 ms),
  or a fast scroll queues a download per step.

## 8. Module contract

The entry point default-exports a function taking `sdk` and returning
`{ splash, shell }` — both, always — and optionally `sounds` (§13). Neither
surface takes props beyond the ones listed here: everything else comes from the
SDK.

```js
export default (sdk) => {
  const { html, useEffect } = sdk.ui

  const Splash = ({ onDone }) => {
    useEffect(() => { const t = setTimeout(onDone, 1200); return () => clearTimeout(t) }, [onDone])
    return html`<div class="t-splash">GAMECORE</div>`
  }

  const Decor = () => html`<img src=${sdk.system.asset('santa.png')} class="t-santa" />`

  return { splash: Splash, shell: () => html`<${sdk.defaults.Shell} decor=${Decor} />` }
}
```

A theme may ship a stylesheet for its own markup and load it from its folder. The host waits for
that sheet (and its `@import`s) to load before it renders any surface, so a
splash is never painted unstyled; a sheet that fails to load does not block.

> **Reusing a default settings page?** Render it **bare** — each one already is
> a full-screen overlay, so putting it in your own panel nests a `position:
> fixed` layer inside a flex box and shatters its layout. To make it match your
> theme, set these in your stylesheet instead:
>
> ```css
> :root {
>   --gc-overlay-scrim:  rgba(6, 18, 26, 0.55);   /* the full-screen backdrop */
>   --gc-overlay-blur:   blur(18px) saturate(115%);
>   --gc-overlay-panel:  rgba(12, 26, 33, 0.82);  /* the card itself */
>   --gc-overlay-border: rgba(255, 253, 247, 0.14);
>   --gc-overlay-radius: 22px;
> }
> ```
>
> The defaults are the dark UI's, including a near-opaque scrim — leave them
> alone on a light or photographic background and every settings page goes
> black.
>
> `--gc-overlay-scrim` and `--gc-overlay-blur` also draw the layer behind the
> **session menu**, which is worth knowing if your theme exports `sessionMenu`:
> that markup is yours, but the surface under it is not, and a menu drawn with
> no panel of its own sits straight on the backdrop of the suspended game.
>
> The same applies to the accent. The host's settings widgets — focus rings,
> toggles, sliders, signal bars, the on-screen keyboard, the theme picker's
> marker — are drawn with inline styles and read three more variables:
>
> ```css
> :root {
>   --gc-accent:        #F0761E;   /* focus, fills */
>   --gc-accent-soft:   #FE9D7C;   /* secondary text */
>   --gc-accent-bright: #FFFDF7;   /* figures, emphasis */
> }
> ```
>
> Set them or your settings screen stays the default purple on your own
> background.

## 9. Fallback and composition

| Situation | Result |
|---|---|
| A surface missing from `provides`, or from the module | the theme does not load; the default frontend runs whole, reason shown in Settings → Themes |
| The shell throws while rendering | the default frontend, and the crash is recorded |
| A themed splash throws, or never calls `onDone` | the default splash, or the host moves on after 20s |
| Module fails to load | theme rejected, default frontend, reason surfaced |

Note what is *not* in this table: a per-screen fallback. Completeness is checked
once, at load. Either the theme runs or the default does.

## 10. Compatibility

- `api` major greater than the host's → theme is listed but **not selectable**,
  with the reason shown.
- `api` major lower → loaded while the host still supports that version.
- Adding a surface or an SDK key does **not** bump the major. Removing one does
  — and so does a shipped theme coming to *require* one, which is what SDK 2
  and SDK 5 both are. See `frontend/src/lib/themeSdk.ts` for the incident that
  established the rule.
- Theme files are never cached. The entry module's URL is timestamped, and
  `/themes` is served with `Cache-Control: no-store` — the entry's own relative
  imports and its stylesheet resolve without that query, so a header is the only
  thing that covers them. Electron's HTTP cache has hidden UI changes before —
  see the OTA section of [`09-gotchas.md`](../architecture/09-gotchas.md).

## 11. Safety

The project had no error boundary at all before this: a React throw produced a
white screen. With themes running arbitrary JS on a TV with no pointer, that was
the failure mode to close first. What is in place:

1. **A boundary around the shell** — the theme renders one tree, so one throw
   inside it hands the whole frontend back to the default. There is no
   half-running theme to reason about, which is the point: the first design had
   a boundary per surface and left you with a UI that was partly one theme and
   partly another. The splash has its own boundary, for the same reason.
2. **A global boundary** — anything that escapes lands on a readable message
   naming the rescue combo, never a white screen.
3. **Crash counting with an amnesty** — two crashes and the theme is refused at
   load. The counter is cleared only after the theme has stayed up 20 s without
   a surface crashing; clearing it merely because the *module* loaded meant a
   theme that broke on every boot never reached the limit.
4. **Persisted safe mode** — the reason is shown in Settings → Themes, and
   picking the theme again is what retries it.
5. **A rescue input** — hold **L1 + R1 for 2 s** anywhere to force the default
   theme, even if nothing renders.

Enforced by the core, not by convention:

- a theme cannot bind `gp:guide`, `gp:menu` or `gp:power` (§6a);
- it cannot write `modalDepth` or `powerPending`;
- the `decor` layer is `pointer-events: none`;
- a theme cannot remove the focus indicator.

A theme has access to `sdk.api`, so it can launch games and power off the box.
On your own machine that is "don't install junk". If themes are ever shared
between users, it becomes a real threat model.

## 12. Performance

The box also runs emulators, on a TV, at 1080p.

- Decor stops while a game is running — the UI already suppresses everything
  during a session.
- Decor stops during standby and the screensaver.
- Animate `transform` and `opacity` only.
- Cap the number of simultaneously animated elements.

## 13. Sounds

A theme can **add** sounds and **replace** the five UI ones.

`move`, `confirm`, `back`, `launch` and `startup` are fired centrally by the
input bus, before any screen is involved. That used to mean there was no hook
for a theme to take them over: a shell that had redrawn every screen still
answered every press with the stock bip, because the bus sits *under* the theme
layer. The seam is now the same one `homeView` draws — **the bus keeps the
*when*, the theme gets the *what*.**

### Declaring them

In the manifest, for files you ship:

```json
{ "id": "arcade", "sounds": { "move": "assets/tick.wav",
                              "confirm": "assets/coin.wav" } }
```

Or from the module, which also accepts a function — the shipped themes
synthesize rather than ship audio, and replacing one bip should not mean
bouncing a wav:

```js
return {
  splash: Splash,
  shell: Shell,
  sounds: {
    move: (ctx, out) => {                    // `out` is already at the player's volume
      const o = ctx.createOscillator()
      o.type = 'square'; o.frequency.value = 1200
      o.connect(out); o.start(); o.stop(ctx.currentTime + 0.03)
    },
  },
}
```

The module wins over the manifest for the same name, exactly the way
`{ ...DefaultSettingsPages, ...ownPages }` works.

Every shipped theme but the default does this: `lib/sounds.js` in Jelly, Orbit,
Shelf and Summer returns all five, built from two small helpers in
`lib/synth.js` (an enveloped tone and a burst of filtered noise). Copy that
pair as a starting point; the default theme keeps the host's bips.

### The cascade

**Your sound → the host's sound → silence.**

Declare `move` alone and the other four stay the host's — replacing one sound
does not saddle you with synthesizing the rest. A name neither of you has is
silent rather than an error, which is what lets you add sounds of your own:
`sdk.system.playSound('coin')` plays your coin, and does nothing in a theme
that never declared one.

A file that 404s or will not decode is dropped back to the host's sound. One
typo'd path costs one bip, not the theme.

### The two rules, unchanged

- **The player's sound setting always wins.** This is enforced, not requested:
  nothing plays while sound is off, and the `out` node you are handed is
  already scaled to their volume, re-read on every play so moving the slider in
  Settings → Audio is heard immediately. Connect to `out`, **not** to
  `ctx.destination` — the second is how you would escape the slider, and a
  theme that can be louder than the player allows is a theme they can only
  silence by uninstalling it.
- **A game on screen means silence.** `playSound` (yours or the host's)
  plays nothing while a game or app holds the screen.
- **Stop when nobody is listening.** A loop *you* start is outside all of the
  above, so read `sdk.system.sound` (`enabled`, `volume`) and follow it. A game
  running or the box asleep means silence; `useIdle` in Summer shows the
  pattern.

`sdk.system.getAudioContext()` is still there for ambience rather than events —
`config/themes/summer/lib/ambience.js` is the worked example: surf from
filtered noise, no audio file, no loop seam.

**What you still cannot do is decide when a UI sound plays.** You supply
`move`; the bus decides that a d-pad press is a `move`. That is the same line
`homeView` draws, and it is why a themed frontend cannot get navigation
feedback subtly wrong.

## 13b. Haptics

Same shape as the sounds, and for the same reason: you supply the *what*, the
bus keeps the *when*.

```js
return {
  splash: Splash,
  shell: Shell,
  rumble: {
    'gp:confirm': { duration: 35, strong: 0.35 },
    'gp:back':    { duration: 22, weak: 0.4 },
    // A sequence plays back to back. `delay` is the gap before a step.
    'gp:menu':    [{ duration: 18, weak: 0.5 }, { duration: 18, weak: 0.5, delay: 40 }],
  },
}
```

`duration` and `delay` are milliseconds, `strong` (low-frequency motor) and
`weak` (high-frequency) are 0–1. Keyed on the `gp:*` event names, so ○ can feel
different from ✕ without the host having to invent a vocabulary of feelings
first.

**Nothing on this box vibrated before, and nothing vibrates now unless a theme
asks.** There is no default table. A player who wants none of it turns
*Controller vibration* off in Settings → Audio, and that setting is enforced
here the way the volume is enforced on sounds — you cannot play through it.

Bounded, like `launch.ms` and the grid: a burst is capped at 1 s, a pattern at
3 s and 8 steps, magnitudes clamp into 0–1. A pad that buzzes for a minute is
not a look, and unlike a broken grid you cannot see it on screen to work out
what is wrong — it just reads as a broken controller.

`sdk.input.rumble(pattern)` is there for the moments the bus cannot know about:
your launch ceremony landing, your splash's impact frame. It is refused while a
game is running — the emulator owns the pad then, motors included. Use the
table for anything that is a response to a button press, or you have moved a
behaviour into the presentation layer.

Most pads report no actuator at all through Chromium, so treat all of this as
something that may simply not happen. It is feedback; it is never the only way
the player is told something.

## 14. Editing a theme

Theme files are served with `Cache-Control: no-store`, so **save and reload** is
the whole loop — no version bump, no cache clearing. (The loader also
timestamps the entry module and the stylesheet, but only the backend header
covers the entry's own relative imports.)

If a reload seems to change nothing, the theme almost certainly failed to load
rather than loading unchanged: open Settings → Themes, where an unusable theme
is listed as not selectable with the reason next to it.

## 15. Authoring loop

1. Copy the skeleton theme.
2. Edit `theme.json`, write one or more surfaces.
3. Drop the folder on the box — drag & drop through the rom-manager addon from
   any browser on the LAN, or over SSH.
4. Settings → Themes → select.
5. Iterate: reload, there is nothing to compile.

## 15b. Installing one from somewhere else

```bash
gamecore-theme install ./my-theme          # a directory
gamecore-theme install theme.zip           # an archive
gamecore-theme install https://…/x.zip     # a download
gamecore-theme install https://…/x.git     # a repository
gamecore-theme verify  <src>               # check it, install nothing
gamecore-theme list [--json]
gamecore-theme remove <id>
```

There is no `install.sh` and nothing to compile — a theme is a directory of
plain files (§2), so this tool never runs anything it fetched. It inspects,
validates and moves.

**Everything is validated in a staging directory before it is moved into
place**, and that ordering is the point rather than a nicety. The frontend
imports `config/themes/<id>/index.js` as a module with the SDK handed to it:
the moment a theme's files exist under `config/themes/`, they are code running
in the UI process. There is no "installed but not yet checked" state to back
out of, so a theme that fails validation never occupies that path at all.

Refused, rather than trusted, because the source is somebody else's:

- an archive entry that escapes the destination (`../../etc/…`) — zip slip;
- any symlink, in the archive or in the tree — the directory is served over
  HTTP and a link into the ROM library or `/etc` would be followed;
- an archive that expands past `GCT_MAX_MB` (64 by default);
- a manifest the **box's own reader** rejects. The rules are not reimplemented
  here: `backend/services/themes.py` is what the running frontend believes, and
  a second copy of "what makes a theme valid" would drift into accepting themes
  the frontend then refuses to load.

Reinstalling keeps the previous copy in `config/themes/.prev/<id>/`, the same
promise `update/linux.sh` makes. `remove` deselects the theme *before* deleting
it, so the box is never left pointing at a directory that is not there.

`OFFLINE=1` refuses every network fetch.

> A theme is arbitrary code with access to `sdk.api`, so it can launch games
> and power the box off. Installing one from a URL is a trust decision, and
> none of the checks above make it a safe one — they only stop the fetch itself
> from being the attack. Read what you install.

## 16. Implementation order

| # | Work | Size |
|---|---|---|
| 1 | Error boundaries + persisted safe mode + rescue input | small, **blocking** |
| 2 | Assemble the `sdk` object from existing modules | small |
| 3 | Backend: list themes, serve their files, persist the active one | small |
| 4 | Theme loader: dynamic import, surface resolution, fallback | medium |
| 5 | Settings → Themes page (follows `StandbyPage`, gamepad nav is free) | small |
| 6 | Extract the default UI into the components exposed as `sdk.defaults` | medium |
| 7 | Skeleton theme + [`PROMPTS.md`](PROMPTS.md) workflow | small |

Step 6 validates the spec: if the default UI cannot be expressed through this
SDK, the SDK is incomplete.

## 17. Shared code between themes

`config/themes/_shared/settings/` holds the settings screen and the power menu.
Shelf and Summer both import it; neither owns it.

It is **not a theme** and never appears in the picker — `list_themes()` skips any
directory starting with `_`. It lives under `config/themes` anyway because that
is the only tree `update/linux.sh` ships to a box, and it carries a `version` in
its `theme.json` for the same reason: that loop decides what to deliver by
comparing that field, so without one a fix would install on a fresh box and
never update an existing one.

It carries **no colour**. Every class is `gcs-*`, and each theme paints them: the
same screen comes out paper-and-teal under Shelf and sea-glass-and-mandarin
under Summer. Anything hardcoded there is one theme's decision imposed on the
other, which is why the classes are no longer named after either.

Import it with a relative path from your own module — `/themes/<id>/…` and
`/themes/_shared/…` are served from the same mount:

```js
import { createSettings } from '../_shared/settings/screen.js'
```

If you want a different settings screen, do not edit that folder: pass your own
page through `createSettings(sdk, { inline: { wifi: MyWifiPage } })`, or write
your own menu from scratch as both themes used to.

(The screen now ships in the bundle and is reached as
`sdk.defaults.createSettings` — the `_shared` path above is its history.)

Its third argument switches on the parts a theme's own design needs, and
leaving them out draws the rail the built-in UI and Summer have always had:

| Part | What it does | Who uses it |
|---|---|---|
| `Background` | your Home background **component**, drawn as the screen's first layer; the screen goes transparent over it | Orbit (`backdrop.Background`), Shelf (the wall) |
| `layout: 'index'` | a category list that opens one page at a time; ○ walks back up a level | Orbit |
| `pager: true` | L1/R1 change category whenever no dialog is open; the page names its place | Shelf |
| `detail: 'dialog' \| 'inline'` | a network's or a device's detail in a dialog, or beside the list; Bluetooth is the same two-column page (Paired / Nearby, with Forget) in every layout | Orbit |
| `skin`, `TopBar` | a class for your palette; your top bar above the screen | both |

Pass the component, not a copy of its CSS. Two definitions of one wall is how
the Home and Settings wallpapers drifted apart once already.

Every dialog on that screen owns the pad while it is up — the rail, the page
and L1/R1 stand down — and ignores a confirm that arrives within 350 ms of it
opening, so a held or auto-repeated press cannot open a confirmation and answer
it.

## 18. The acceptance test, and its removal

`config/themes/default-remake` **was** the default UI rebuilt as an ordinary
third-party theme — the only test that answered the question step 6 asks, run by
the suite against the real `buildSdk()` output rather than a stub, because a
stub would pass on exactly the day the SDK lost a key the theme needs.

It was removed with `shelf-v1` when the box was cut down to two themes. That is
a real loss and it is recorded here rather than quietly dropped: the SDK no
longer has a canary that fails when it stops being able to express the default
frontend. What remains is `config/themes/shelf`, which exercises far more of the
SDK but is not a remake of anything, so it cannot answer the same question — a
gap it opens is a gap in Shelf, not necessarily one in the SDK.

The six gaps the remake found, all closed before it went, were each of the same
shape: none prevented a theme from *loading*, and every one let a theme load and
then be quietly worse than the default. That is the failure mode to keep in mind
without it — and `git log -- config/themes/default-remake` is where it lives now
if it is ever worth restoring.

`config/themes/summer` remains the worked example for `background` and `decor`,
the two shell parts the remake deliberately left out.

| Found | Was |
|---|---|
| `sdk.format.systemColor` | a private function in `SystemCard`; a themed dashboard painted uncatalogued systems the house purple |
| `sdk.format.gameName` | unreachable; a themed library could not turn a ROM filename into a title |
| `sdk.format.time` / `date` / `hexToRgb` | unreachable; a theme phrased playtime differently from the rest of the UI |
| `sortKeys` / `sortLabels` | imported by the default view only; a theme's copy would drift from what L1/R1 actually cycles |
| `toasts` | rendered by the shell, not a part; a theme with its own tree lost every notification |
| `sdk.system.splashHoldMs` | read from the URL by the default splash and undocumented, so every themed splash started mid-animation at cold boot — replaced in SDK 4 by the `bootReady` prop, which is a fact rather than a duration (§8) |

If you add something to the default UI, add it to this theme. If you cannot, you
have found the next gap.
