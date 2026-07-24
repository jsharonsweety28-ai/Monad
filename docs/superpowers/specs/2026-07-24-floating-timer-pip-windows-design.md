# Floating Focus Timer over other apps (Windows) — Design

Date: 2026-07-24
Status: approved, not yet implemented
Scope: Windows/desktop (Chrome & Edge) only. Android and iPhone are out of scope for this spec.

## Goal

Let the focus pill float over other applications on a Windows desktop, so a
student can see and control the running session while working in another app
(notes, a document, a browser). It should look and behave like the in-app pill
— countdown plus a working pause/resume control — but live in an always-on-top
window instead of inside the page.

## Why this is desktop-only

Floating over other apps is a native-app power. The one browser feature that
approximates it is **Document Picture-in-Picture**, an always-on-top window
that holds live, interactive HTML. It exists on desktop Chrome and Edge (116+).
It does not exist on Firefox, Safari, or Android. Because support maps almost
exactly to the target, the feature self-limits: the entry point only appears
where it can work.

Android (a view-only video bubble) and iPhone (no overlay possible; a
completion notification is the substitute) are deliberately deferred.

## What the user sees

- A small **pop-out button** (⧉) on the pill, shown only when the browser
  supports Document Picture-in-Picture. Everywhere else it is simply absent —
  no broken control.
- Clicking it opens a compact always-on-top window styled like the pill: the
  `MM:SS` countdown (or elapsed, for stopwatch), the session label, and a
  pause/resume button. A stop button ends the session.
- The window is a real OS window: the user drags it anywhere and it floats over
  every other application.
- Closing the window returns to the normal in-app pill. The session is
  unaffected either way — it lives in the shared store.

## Architecture

### Entry point: `FocusTimer.popOut()`

Added to the `FocusTimer` module (`website/static/focus_timer.js`). Guarded by
`('documentPictureInPicture' in window)`. The pill's pop-out button is only
rendered when that guard is true.

### The floating widget

Rather than move the existing pill DOM into the PiP window and back (brittle,
and it fights the pill's own render loop), render a **fresh, self-contained
widget** into the PiP window's document:

- Minimal inline CSS copied into the PiP document — no dependency on the app's
  stylesheet or the Bootstrap Icon font (which would not be loaded there).
  Icons are plain Unicode (⏸ ▶ ⧉ ✕).
- Reads the live session via the shared `FocusTimerStore` / `FocusTimerCore`,
  so it never holds its own copy of the time.
- Pause/resume and stop call the same `FocusTimer.pause/resume/stop` the pill
  uses, so the in-app pill and the floating widget always agree.

### Keeping it ticking while the main tab is throttled

When the user switches to another app, the main Monad tab is backgrounded and
its timers are throttled. The PiP window stays visible, so its own event loop
is not throttled. The render interval is therefore scheduled on the **PiP
window** (`pipWindow.setInterval(...)`), not the main tab. The callback still
derives the time from the clock via `FocusTimerCore`, so the value is always
correct; running it in the PiP window keeps the display updating every second
rather than once a minute.

### Lifecycle

1. Pop-out click → `documentPictureInPicture.requestWindow({width, height})`.
2. Inject styles + widget markup into `pipWindow.document`.
3. Wire the widget's buttons to `FocusTimer` and start `pipWindow.setInterval`.
4. Hide the in-app pill while the PiP window is open (redundant otherwise).
5. On the PiP window's `pagehide` (user closed it, session ended, or the opener
   navigated): clear the interval and restore the in-app pill.
6. If the session completes while floating, the existing
   `FocusTimer.onComplete` handling still runs (save + clear). The widget shows
   a brief "Done" then the window closes.

## Known limitations (stated, not fixed)

- **Navigating within Monad closes the floating window.** Document PiP is owned
  by the opener document; a full page load ends it. The intended use — pop out,
  then switch to a *different application* — is unaffected, because switching
  apps does not reload Monad. Bouncing between Monad's own pages requires
  re-popping.
- **Desktop Chrome/Edge only.** By design. Other browsers never see the button.
- **Only one floating window.** Requesting a second replaces the first (browser
  behaviour); acceptable.

## Testing

No automated coverage is possible for the OS-level float. Verification is
manual, on a real Windows desktop in Chrome or Edge:

1. Start a session, confirm the ⧉ button appears on the pill.
2. Click it — a small always-on-top window opens showing the countdown.
3. Switch to another application (a text editor). Confirm the window stays on
   top and the countdown keeps updating every second.
4. Drag the window to another screen position; confirm it moves freely.
5. Press pause in the floating window; confirm the in-app pill also shows
   paused when you return to Monad. Resume; confirm both agree.
6. Let a short session finish while floating; confirm it saves (appears in
   Recent Sessions) and the window closes.
7. Open Monad in Firefox; confirm the ⧉ button is absent and nothing breaks.

What can be checked without a desktop: that `popOut()` opens a window and the
widget ticks, inside the in-app browser if it supports the API. Whether it
truly floats over other applications can only be confirmed on the user's
Windows machine — this feature ends with that confirmation.

## Out of scope

- Android floating bubble (view-only video) — separate future spec.
- iPhone — no overlay is possible; the completion notification (Stage 2 of the
  background-timer work) is the substitute.
- Making the floating window survive in-app navigation.
