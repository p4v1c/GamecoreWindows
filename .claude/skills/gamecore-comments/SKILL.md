---
name: gamecore-comments
description: Comment rules for GameCore for Windows. Use whenever writing or editing a comment, docstring or JSDoc in backend/, frontend/, electron/, catalog/, install/, scripts/, update/ or config/themes/ — including when adding a feature, fixing a bug, or reviewing a diff. English only, short, why-not-what.
---

# GameCore comments

Comments are English, direct, and short. They carry the **why** the code
cannot say. No stories.

## Rules

1. **English only.** Code, comments, docstrings, log messages, test names.
   French goes in chat, never in the tree.
2. **Why, never what.** If the comment repeats the line, delete it.
3. **One to three lines.** A comment longer than the code it explains is a
   doc — move it to `docs/` and link it.
4. **Lead with the fact.** First words = the constraint or the failure.
   - Bad: `# We noticed on the box last week that when the pad was unplugged…`
   - Good: `# Unplug during launch leaves slot 2 bound; release before rebind.`
5. **Name the failure, not the history.** "Breaks X when Y" beats a date and
   a narrative. Commit bodies and `docs/reports/` hold the story.
6. **Docstrings:** one summary line, then only what a caller must know
   (inputs, return, side effects, raises). Max ~5 lines unless it is a public
   contract (addon API, theme SDK, pack schema).
7. **No dead words:** no "Note that", "Basically", "It is worth noting",
   "As you can see", no emoji, no ASCII banners.
8. **No commented-out code.** Git has it.
9. **TODO format:** `# TODO(area): what, and the condition to do it.`
   Never a bare `TODO`.
10. **Do not rewrite old comments just for length** in a diff about
    something else. Fix the ones you touch.

## Examples

```python
# Bad — narrative, what, French
# Ici on attend un peu parce que sinon ça plante parfois sur le boîtier
time.sleep(0.5)

# Good
# SDL enumerates the pad ~300 ms after WM_DEVICECHANGE; earlier reads return no GUID.
time.sleep(0.5)
```

```ts
// Bad
// Set the focus index to 0
setGridFocus(0)

// Good
// A theme swap remounts the grid; keep focus on-screen.
setGridFocus(0)
```

```python
def release_profile(slot: int) -> list[str]:
    """Unbind `slot` in every emulator config. Returns the pack ids touched."""
```

## Self-check before finishing

- `git diff` → every added comment line: English? why? ≤3 lines?
- Quick French scan on added lines:
  `git diff -U0 | grep -E '^\+.*(#|//|\*) .*\b(le|la|les|une|pour|est|pas|dans|avec)\b'`
