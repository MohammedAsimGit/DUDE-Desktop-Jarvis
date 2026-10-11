# Dude — window modes

Dude is a single frameless, transparent Tauri window. The Rust host owns its
geometry (`src-tauri/src/window.rs`); the UI only asks for a mode and mirrors
the result. The UI never positions or sizes the window itself.

There are three modes. Compact is the default; full-screen is strictly opt-in
and always reversible.

| Mode        | Native window                                             | Entered by                        |
| ----------- | --------------------------------------------------------- | --------------------------------- |
| Compact     | `272 x 76` logical, bottom-right of the work area          | default state                     |
| Expanded    | `272 x` up to `560` logical, height clamped to the display | the header chevron                |
| Full-screen | the current display's full monitor bounds                  | **Full-screen desktop mode** row  |

Gaps between the window and the work-area edges: `16 px` for the companion
modes, `24 px` for the panel inside the full-screen layer.

## Compact companion (default)

A small floating pill placed inside the primary display's work area (the
monitor rectangle minus the taskbar), 16 logical px from the bottom-right
corner. The panel is the native drag region, so the pill can be moved; it is
re-placed inside the work area on every size change so it can never end up
off-screen or under the taskbar.

## Expanded panel

The same window, grown downward. The height is **derived from the display**,
not fixed: the preferred height is 560 logical px, reduced so the window plus
its edge margins still fits the work area, and never below 240 logical px
unless the display itself is smaller. That keeps the panel usable on short or
heavily scaled displays instead of pushing content off-screen.

The window no longer needs to fit all of the content, because the panel body
scrolls:

- The header (mark, status, chevron) is outside the scroll container and stays
  visible.
- The body is the scroll container (`overflow-y: auto`, thin scrollbar) and is
  a focusable region, so keyboard scrolling and a visible focus ring work.
- No rule clips controls or text with `overflow: hidden`.
- Status card, presence controls, appearance, startup, voice controls,
  transcript and chat are all reachable by scrolling.

## Full-screen desktop mode

An explicit, reversible layer that covers the **whole current display** while
staying transparent, so the real Windows desktop and whatever is behind it
remain visible everywhere the panel does not paint. There is no wallpaper, no
simulated desktop, and no screenshot background.

- **Entering/leaving.** Toggle the **Full-screen desktop mode** row in the
  expanded panel. On entry the window is resized to the monitor bounds reported
  by the window manager in physical pixels — that is what keeps display scaling
  and multiple displays correct rather than guessing — and the panel is
  anchored to the top-right corner of the display. The applied state is echoed
  back to the UI, so the indicator cannot lie; a failure restores the previous
  layout instead of leaving half-applied geometry.
- **Leaving.** Any of: `Esc`, the exit control in the header (the chevron is
  replaced by it), or the labelled **Exit full-screen mode** button at the top
  of the panel body. Leaving restores the companion layout that was active
  before.
- **The tray is unaffected.** Hide and Quit keep working while the layer is
  up. Hiding does not leave the mode: the window state and the UI agree, so
  showing Dude again returns to the full-screen layer with its exit controls
  in place. Selecting **Quit Dude** is the one action that ends the process
  from here.
- **Not always-on-top.** Full-screen mode never pins Dude above other windows
  by itself; the separate **Always on top** toggle is the only thing that
  does. Full-screen is off by default and never persisted.
- **No new permissions.** The mode is implemented with native window geometry
  inside the Rust host, so `src-tauri/capabilities/default.json` is unchanged.
  There is no screen capture, OCR, computer vision, mouse/keyboard automation,
  or background input monitoring.
- **No dragging.** The panel stops being a native drag region while the layer
  is up, so the layer cannot be dragged off the display it is meant to cover.

### Limitation: the layer is not click-through

Underlying windows stay **visible** but not **interactive** while the layer is
up: the full-screen window receives mouse input across the whole display, so
clicking the desktop or another app does not reach it until you leave
full-screen mode. Leave with `Esc` or the exit controls, then click through
normally.

Per-region click-through was deliberately not implemented. Tauri can ignore
cursor events for a whole window, but without a global mouse hook (background
input monitoring, which this project does not use) there is no reliable way to
know when the pointer is over the panel and re-enable input for Dude's own
controls — and a window-wide “ignore everything” mode would make the exit
control itself unclickable. The safe, non-interactive limitation above is
documented instead. If you need the desktop interactive, stay in compact or
expanded companion mode.

For the same reason, full-screen mode is **not** a wallpaper replacement or a
screen-aware assistant; it is a transparent full-screen window with a reliable
exit path.

## Related

- [ARCHITECTURE.md](ARCHITECTURE.md) — process model, IPC protocol, layout
- [DEVELOPMENT.md](DEVELOPMENT.md) — setup, scripts, workflow, troubleshooting
- [VOICE.md](VOICE.md) — voice states and transcript handling
