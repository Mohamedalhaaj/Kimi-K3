# Kimi Workspace 2 — visual world

**Mode: Operate.** The interface serves a task. Familiarity is a feature; the
tool disappears into the work. Brand lives in precise details, not decoration.

## The idea

*A quiet instrument.* The surface is paper-calm and the accent is used like a
surgeon uses ink — only where the user must look or act. Every claim the
product makes about freshness, sources, or timing is rendered as plain,
checkable metadata rather than as a badge that decorates.

## Color — Restrained

Accent carries **primary actions, current selection, and focus only**. It is
never a decorative fill, never a gradient, and never used on an inactive state.

| Token | Light | Dark | Notes |
|---|---|---|---|
| `--accent` | `#0171DD` | `#0171DD` | Button fill. White label = 4.76:1 both modes. |
| `--accent-fg` | `#0171DD` | `#4DA3F5` | Accent **text/icons**. Brand blue is only 3.81:1 on dark, so dark lifts it to 6.80:1. |
| `--bg` | `#FFFFFF` | `#0F1622` | Content surface. |
| `--bg-sunken` | `#F6F8FB` | `#0B111B` | Sidebar / toolbars — the second neutral layer, cooler than content. |
| `--fg` | `#0B1220` | `#E8ECF3` | 18.7:1 / 15.3:1. |
| `--fg-muted` | `#5B6577` | `#98A2B3` | 5.88:1 / 7.04:1 — never below AA. |
| `--border` | `#E3E8EF` | `#1E2938` | Hairlines only. |

Dark is a deep blue-black, not neutral gray and not pure black: it pairs with
the accent and stays comfortable in the evening reading scene.

Every value above was measured, not guessed — see `apps/web/scripts/contrast.mjs`,
which fails the build if any pair drops below AA.

## Type — one family, fixed scale

One well-tuned sans for headings, labels, body, and data. No display/body
pairing; product UI does not need it.

- **Latin:** Geist (bundled, no network request).
- **Arabic:** the platform's own — SF Arabic on macOS, then Noto Sans Arabic.
  Bundling a second webfont would cost more than it buys when the OS face is
  excellent and already installed.
- **Fixed rem scale at ratio ~1.14**, not fluid: `12 / 13 / 14 / 16 / 18 / 22 / 28`.
  A clamp-sized heading that shrinks inside a sidebar looks worse, not better.
- Prose measure capped at **72ch**. Tracking floor -0.02em on the largest steps only.

## Layout

- **Structural responsiveness**, not fluid type: the sidebar collapses to a
  drawer under 900px; the composer and rail keep their rhythm.
- The app shell is a **CSS grid with `grid-template-rows: minmax(0,1fr) auto`**.
  The scroll region and the composer are siblings, so the composer *cannot*
  overlap the last message — this is a structural guarantee, not bottom padding
  that breaks when the textarea grows.
- Space: tight within a group, generous between groups; more space above a
  heading than below it.

## Depth

Shadows carry an offset **and** a soft blur — `0 1px 2px` / `0 8px 24px` with
low-alpha ink. No zero-offset colored halos. Elevation is reserved for things
that genuinely float: popovers, the model menu, the drawer.

## Motion — one authored moment

The assistant message arrival is the single authored moment: a 180 ms rise of
6px with an exponential ease-out from an already-visible default, so nothing
pops in from nothing. Everything else is state feedback at 150–200 ms.

No page-load choreography — the app loads into a task. Streaming text itself is
never animated; animating each token would make reading harder.

All of it collapses to opacity-only under `prefers-reduced-motion`.

## Refused for this surface

Card grids as page structure; gradient text; glass/blur as decoration; colored
`border-left` accents; monospace as a "technical" costume; sparklines standing
in for content; emoji in chrome; a modal for anything that can be inline.
