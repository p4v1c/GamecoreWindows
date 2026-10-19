---
name: gamecore-legibility
description: Make every GameCore screen readable on a TV from the sofa. Use for ANY UI change (themes in config/themes/, frontend components, settings pages, pad hints, overlays, Electron HUD). Sets the contrast and size floors, says how to fix a failure without making the UI look generated, and ships legibility-audit.mjs, which renders the real screen and measures contrast against the pixels behind each text and icon.
---

# GameCore legibility

GameCore is read at 3 m on a TV that washes out colour. A hint the player
cannot read is a broken feature, whatever it looks like on a monitor.
Measure it. Do not trust your eye on a laptop.

Pairs with `gamecore-human-touch`: fix legibility with the theme's own
material. Its paper, glass or ink. Never with a generic glow or gradient.

## 1. Floors

| What | Floor |
|---|---|
| Body text, labels, hints | contrast ≥ 4.5:1 against the pixels behind it |
| Text ≥ 24px, or ≥ 18.66px bold | ≥ 3:1 |
| Icons, pad glyphs (✕ ○ □ △, d-pad, L1 pills) | ≥ 3:1 |
| Any text a player reads | ≥ 14px at 1080p; hints and legends 16px+ |
| Focus | visible without colour: shape, ring, scale or weight too |

"Behind it" means the real backdrop: wallpaper pattern, photo, sand, a
panel's glass over the sky. The worst common pixel counts, not the average.

## 2. What breaks it, and the fix

| Cause | Fix |
|---|---|
| Hierarchy by low alpha (`rgba(255,255,255,.3)`, `opacity: .4`) | Named ink tokens (`--ink-2`, `--ink-3`, `--gc-ink-3`, `--set-ink-3`) each checked ≥ 4.5:1 on the surfaces they sit on. The faintest ink is still readable |
| `opacity` on a container to dim it | Dim the drawing only; text inside keeps full ink. Unfocused tiles: no opacity, use the focus signals |
| Text straight on a pattern, photo, sand | A plate in the theme's material behind it (Shelf: paper label; Summer: glass pill). Not a text-shadow, not a gradient scrim over everything |
| Pale pad glyphs on a light surface | Set `--gc-pad-cross`, `-circle`, `-square`, `-triangle` on that surface (≥ 3:1). `padKey.js` defaults are for dark screens; `initial` restores them in a dark panel inside a light theme |
| Brand colour as text on dark (PS blue `#003087`) | Lighten for text: `color-mix(in srgb, <brand> 45%, #fff)`; keep the raw brand colour for fills |
| 8–12px labels "for elegance" | 14px floor, 16px for hints. Hierarchy from weight and ink, not from shrinking |
| Decoration over text (a shell on the hint bar) | Move the decoration. Decoration never covers text |
| Status colours tuned for dark (`#f59e0b`) on a light card | Theme sets `--set-warn/info/danger/ok` dark enough for its card |

Exempt, and only these:
- text drawn as part of an object, when the same fact is readable elsewhere
  (box spine, back-of-box fine print);
- illustrations marked `aria-hidden="true"` (the controller drawing, the
  ghost art when no pad is connected);
- disabled controls (`disabled` / `aria-disabled="true"`), e.g. index letters
  with no game. The ones that do something must pass. If something carries information that
  is not repeated as readable text, it is not decoration.

## 3. Measure

Dev server. It is read-only (every write but the theme switch gets a 403:
an audit press once launched Steam on the box) and never runs the real
lifespan (it changes the power plan, the display mode and the screen
timers of the machine it runs on):

```powershell
$d = "$env:TEMP\gcdata"
New-Item -ItemType Directory -Force "$d\config", "$d\emu\azahar" | Out-Null
Copy-Item install\generated\systems.json.dist "$d\config\systems.json"
Copy-Item install\generated\apps.json.dist "$d\config\apps.json"
Copy-Item -Recurse -Force config\themes "$d\config\"
# a library with games: empty files with real names
'Mario Kart 7', 'Pokemon X', 'Kid Icarus - Uprising' | ForEach-Object { New-Item -ItemType File -Force "$d\emu\azahar\$_ (Europe).3ds" | Out-Null }
Push-Location frontend; npx vite build; Pop-Location
$env:GAMECORE_PATH = $PWD; $env:GAMECORE_DATA = $d; $env:PYTHONPATH = $PWD
Start-Process .venv\Scripts\python .claude\skills\gamecore-legibility\scripts\devserve.py
```

Re-copy `config/themes` into `$env:TEMP\gcdata` after each theme edit;
rebuild the frontend after each `frontend/src` edit. The audit drives
headless Chrome, or Edge when Chrome is absent (`GAMECORE_CHROME` overrides
the executable).

Audit, per theme and per screen:

```powershell
$A = '.claude/skills/gamecore-legibility/scripts/legibility-audit.mjs'
foreach ($t in 'default', 'orbit', 'shelf', 'summer', 'jelly') {
  node $A --theme $t                           # home
  node $A --theme $t --press confirm          # library (Orbit: --press r1,confirm)
  node $A --theme $t --press x                # controller screen
  node $A --theme $t --press power            # power menu
  node $A --theme $t --press menu             # settings
}
node $A --theme shelf --press x --shot "$env:TEMP\shelf-pad.png" --all   # every item + screenshot
node $A --theme orbit --press power --shot "$env:TEMP\p.png" --crops --clip 660,300,600,560   # + 2x quarters, 2x region
```

Controller states need pads. `--init` runs a script before the page and
`--eval` runs expressions after the presses (`;;`-separated, 900 ms apart);
`scripts/fake-pads.js` fakes the Gamepad API, the roster and a suspended game:

```powershell
$F = '.claude/skills/gamecore-legibility/scripts/fake-pads.js'
# □ opens the controller screen; hold east, R2 at 60 %, left stick moved
node $A --theme orbit --url 'http://127.0.0.1:8766/?pads=ds4,xbox' --init $F `
  --eval '0;;0;;0;;__press(2);;__hold(1);;__hold(7,0.6);;__axes([0.8,-0.5,0,0])'
node $A --theme shelf --url 'http://127.0.0.1:8766/?pads=ds4&bg=1' --init $F --eval '0;;0;;0;;__press(8)'  # power menu
```

The audit's Chromium is muted and sees no real pad (the box's controller
drove audit pages while the owner played); `--init` pads are the only ones.

The three `0` let the shell mount; the first real press after load only
wakes the box. The built-in UI answers these presses too.

It prints `FAIL` for contrast, text under 14px, or text under decoration, and
counts 14–16px text as "small" (`--all` lists them). Exit 1 on any `FAIL`.
How it measures: one screenshot with all text and icons hidden, then the
backdrop is sampled under each item and the 10th-percentile ratio is kept.
Items another screen covers are skipped.

Limits: empty ROM files all get the same scraped art; fine for layout.
The built-in UI ignores synthetic `--press` events; drive it with
`fake-pads.js` instead. The TV is the final check: look at it before merging.

## 4. Checklist

- [ ] audit run on every theme × screen the change touches: 0 `FAIL`
- [ ] new colours are tokens in the theme's DESIGN.md, with their ratio
- [ ] no text dimmed by `opacity` or by an alpha below the theme's ink-3
- [ ] hints and legends 16px+, nothing read under 14px
- [ ] pad glyphs checked on every surface they appear on (light buttons too)
- [ ] fixes use the theme's material (see `gamecore-human-touch`), no glow
- [ ] `--shot` screenshots before and after in the PR, reviewed with `gamecore-visual-review`
