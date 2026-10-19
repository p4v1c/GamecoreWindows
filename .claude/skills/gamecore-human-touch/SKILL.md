---
name: gamecore-human-touch
description: Make GameCore look and read like a product people designed, not AI output. Use for ANY UI work (themes in config/themes/, frontend components, settings pages, Electron HUD, boot screen) and ANY user-facing text (UI strings, toasts, errors, README, release notes, PR/commit text). Lists the visual and writing "AI tells" to remove, what to do instead, and ships slop-audit.py to measure them.
---

# GameCore human touch

A generated UI is recognisable because nobody chose anything: default font,
default violet, default glow, default copy. Every rule below is the same rule:
**make a decision, write it down, apply it everywhere.**

Sources: Anthropic `frontend-design` skill, Wikipedia "Signs of AI writing",
`stop-slop` / `humanizer` skills, the 16-pattern "AI design slop" checklists.

## 0. Before touching a theme: its DESIGN.md

Each theme keeps `config/themes/<id>/DESIGN.md` (the default UI:
`frontend/src/DESIGN.md`), ≤ 40 lines:

- **Idea** in one sentence (Shelf: "boxes on a papered wall").
- **Type**: one family (two at most, roles named), sizes as a scale, where
  tabular numbers are used.
- **Palette**: named tokens with hex; one accent; contrast checked.
- **Shape**: radius scale by role (tile, panel, control), not one radius.
- **Depth**: which surfaces get shadow/blur, and which never do.
- **Motion**: durations, easing, what moves and what never does.

Code reads the tokens; nothing hardcodes a hex the DESIGN.md does not name.

## 1. Visual tells → what to do instead

| Tell | Instead |
|---|---|
| Inter / Outfit / Space Grotesk / Figtree + JetBrains Mono "for character" | A family chosen for the theme's idea, **self-hosted** (`fonts/` in the theme, `@font-face`, OFL licence file). The box is offline; never fonts.googleapis.com |
| Monospace for labels, numbers, metadata | Body font with `font-variant-numeric: tabular-nums`. Mono only for real code/paths |
| Violet/indigo accent (`#7c3aed`, `#8b5cf6`, `#6366f1`…) on near-black | An accent that comes from the theme's idea; one accent, used for focus and primary action only |
| Gradient washes, glows, coloured shadows, neon rings | Flat fills; one shadow scale in neutral tones; light only where the metaphor has light (Summer sun, Orbit ceremony) |
| Glass/backdrop-blur on every panel | Blur on at most one layer (the one floating over content); opaque elsewhere — also cheaper on the box GPU |
| TRACKED-OUT ALL-CAPS eyebrow over every heading | Sentence case, normal tracking. Caps only for real abbreviations (BIOS, USB, HDMI) |
| Same radius on everything | Radius by role from DESIGN.md |
| Identical cards: icon + title + two lines, ×3 | Layout from the content: lists for lists, a table for specs, one card when there is one thing |
| Emoji as icons (🎮 ⚙ 🔍 ⚡ ⏳ ⚠ ✓ ♥) | Drawn SVG icons (one stroke width, one grid), or plain words. Pad glyphs ✕ ○ △ □ are controls, keep them |
| Fade-and-slide-up on every block, hover lift on every card | Motion with a job: focus moves, a game launches, a panel opens. `prefers-reduced-motion` honoured |
| "01 / 02 / 03" markers, stat banners, badges above titles | Only when the content is really a sequence or a number the player cares about |
| Decorative borders and hairlines everywhere | A border separates or states focus; otherwise remove it |

TV rules still win: readable at 3 m, focus visible without colour alone,
contrast ≥ 4.5:1 for text, pad-only navigation. Measure them with the
`gamecore-legibility` skill (`legibility-audit.mjs`).

## 2. Writing tells → what to do instead

Applies to UI strings first, then docs, release notes, PRs, commits.

| Tell | Instead |
|---|---|
| Em dash as the default joiner ("Saved — restart to apply") | Two sentences, a colon, or a comma. At most one per paragraph, never in a button or toast |
| "A · B · C" meta strings | Say it: "12 games, 3 h played". Keep "·" only between pad hints (`✕ Select   ○ Back`) — or use spacing there too |
| "→" / "->" in button or link text | The verb alone: "Open settings". Arrows only as d-pad hints |
| Title Case Everywhere, Bold Everywhere | Sentence case; bold only for the one thing to act on |
| Hype words: seamless, effortless, powerful, unleash, elevate, delve, crucial, robust, vibrant, boasts, enhance, "your gaming journey" | Plain verbs and nouns: "Install", "3 emulators need a BIOS" |
| "Not just X, but Y", rule of three, "It's not X, it's Y" | State Y |
| Throat-clearing: "Please note that", "Simply", "Just", "Oops!", "Looks like…" | The fact, then the action: "No controller. Press any button on a pad." |
| Vague errors ("Something went wrong") | What failed, what to do: "Couldn't reach GitHub. Check the network, then retry." |
| Exclamation marks and emoji in messages | None. Success is shown by the state changing |
| Passive, agentless ("Settings were updated") | Actor or result: "Saved." |
| Curly/straight quote and apostrophe mix | One style per file (UI: typographic ’ “ ”) |

No Linux vocabulary in UI text (Plasma, KDE, Flatpak, systemd, udev): the
player is on Windows. Name what they see ("Windows desktop").

Voice for GameCore: a console, not a chatbot. Short, calm, specific, English,
second person only when giving an instruction. Button = verb (+ object).

## 3. Measure

```powershell
.venv\Scripts\python .claude/skills/gamecore-human-touch/scripts/slop-audit.py      # counts per theme
.venv\Scripts\python .claude/skills/gamecore-human-touch/scripts/slop-audit.py -v   # with samples
```

It counts fonts, uppercase/tracking, gradients, glows, blur, violet, emoji,
"·" meta, "→" in text and em dashes in UI strings, per area (orbit, shelf,
summer, _skeleton, default-ui). A pass must lower the counts or explain each
one that stays in the theme's DESIGN.md. It is a heuristic: read the samples.

## 4. Pass checklist (per theme or screen)

- [ ] DESIGN.md exists and the code uses its tokens
- [ ] fonts self-hosted, no external request (`grep googleapis` is empty)
- [ ] no emoji used as an icon; icons share one style
- [ ] no violet default, one accent, contrast checked
- [ ] caps and tracking only where DESIGN.md says
- [ ] glows/gradients/blur only where DESIGN.md says
- [ ] every visible string read aloud: short, specific, no tells from §2
- [ ] slop-audit before/after pasted in the PR
- [ ] theme `version` bumped; screenshots reviewed twice (`gamecore-visual-review`) before merge
