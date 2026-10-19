---
name: gamecore-visual-review
description: Look at what you built before calling UI work done. Use for ANY change a player can see (themes in config/themes/, frontend components, settings, overlays, HUD). Screenshot every touched theme × screen, review each image against the request and the theme's DESIGN.md, fix, re-shoot, and review again — at least two passes, until a pass finds nothing. Uses the gamecore-legibility capture tools.
---

# GameCore visual review

Tests prove the code runs. They do not prove it looks right. A screen is done
when someone has looked at it, on every theme it touches, and found nothing
to fix. That someone is you, with screenshots, **at least twice**.

Costs tokens. It is still cheaper than the owner finding it on the TV.

## 1. Capture

Use the read-only dev server and the capture tool from `gamecore-legibility`
(never the real lifespan, never press ✕ on a game or app tile):

```powershell
$A = '.claude/skills/gamecore-legibility/scripts/legibility-audit.mjs'
$OUT = "$env:USERPROFILE\Downloads\<topic>\shots"          # the owner looks here too
node $A --theme <t> [--press <gp>] --shot "$OUT\<t>-<screen>-before.png" --crops   # on main, before
node $A --theme <t> [--press <gp>] --shot "$OUT\<t>-<screen>-after.png" --crops    # on the branch
node $A --theme <t> [--press <gp>] --shot "$OUT\<t>-<screen>-card.png" --clip 660,300,600,560   # one component, 2x
```

- Every theme the change can reach (default, orbit, shelf, summer, jelly), every
  screen and state it touches: focused and unfocused, empty and full, long
  names, several pads, error state, the modal open and closed.
- A 1920×1080 image is shrunk when you read it: small defects hide. Read the
  full shot for composition, then the `-q1..q4` quarters and `--clip`
  crops (2x) for detail.
- Dev data with games: empty files under `<data>/emu/<system>/` (see the
  legibility skill).

## 2. Review each image against three references

Write down, for each image, what you SEE before judging it: layout, colours,
text, what has focus. Then compare with:

1. **The request.** Does it do what was asked, on this theme, in this
   state? Name the part of the request each image proves.
2. **The theme's DESIGN.md** (`config/themes/<id>/DESIGN.md`,
   `frontend/src/DESIGN.md`): idea, type, palette tokens, shape, depth,
   motion. A new element that could belong to any theme is a finding.
3. **The neighbours.** Does it look like the screens around it on the same
   theme (spacing, radius, hint bar, focus style, icon style)?

Checklist for every image:

- [ ] nothing clipped, overflowing, overlapping, cut mid-word or truncated
      without an ellipsis that makes sense
- [ ] alignment and spacing on the theme's grid; no orphan element, no
      accidental gap
- [ ] focus visible without colour alone, on the right item
- [ ] pad hints match what the screen actually does
- [ ] icons from one family, same stroke and size; no emoji, no brand logo
      that is not ours to use
- [ ] no AI tells from `gamecore-human-touch` (default violet, glow,
      gradient wash, tracked caps, "·" meta, em dashes in UI text, hype copy)
- [ ] copy: short, specific, sentence case, English
- [ ] legibility numbers from the audit: 0 FAIL on what changed

## 3. Two passes minimum

- **Pass 1:** review, list findings as `image — problem — fix`, fix them.
- **Re-shoot everything touched**, not only the fixed spots: a fix moves
  its neighbours.
- **Pass 2:** review the new images cold, as if you had never seen them:
  describe first, judge second. If the session allows it, give pass 2 to a
  separate reviewer (a fresh agent with only the request, DESIGN.md and the
  images).
- Repeat until a pass finds nothing. Two passes is the floor, not the goal.

## 4. Report

In the PR and to the owner: a table per theme × screen with the before and
after images, the passes (findings, what was fixed), and what was not
checked (states the dev data cannot reach, the TV itself). Keep the
screenshots in `%USERPROFILE%\Downloads\<topic>\shots\`.
