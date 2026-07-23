# Background Focus Timer — Design

Date: 2026-07-23
Status: approved, not yet implemented

## Problem

The focus timer only exists inside `pomodoro.html`. Two consequences:

1. **Navigating anywhere destroys the session.** The countdown is a JavaScript
   variable in the page. Opening a note, checking tasks, or following any link
   ends the session silently — which is exactly what a student needs to do
   while studying.
2. **Backgrounding the tab makes it drift.** The countdown decrements once per
   `setInterval` tick. Browsers throttle timers in background tabs, often to
   once per minute, so the displayed time falls behind real time and the
   session may never reach zero.

## Scope

In scope: the session survives navigation within the app, tab switching, a
minimised browser, and a sleeping screen. It completes and notifies wherever
the user is.

Out of scope: surviving a fully closed browser or a device restart. That needs
server-side session tracking and scheduled push, and was explicitly declined.
The design must not make that harder later, but does not build it.

## Approach

### Record the finish time, don't count ticks

On start, compute and store `endsAt` (epoch milliseconds). Any view that shows
a countdown derives `remaining = endsAt - Date.now()`.

This is the heart of the design. It makes the session immune to throttling,
page loads, and sleep, because elapsed time is read from the clock rather than
accumulated by the page. It also fixes existing drift in the current timer.

Stopwatch mode inverts this: store `startedAt` and derive elapsed time upward.
It has no `endsAt`.

Pausing stores `pausedAt`. Resuming shifts `endsAt` forward by the paused
duration, so pause time is not counted as focus time.

### State lives in localStorage

One key, `monad.focusSession`, holding:

| Field | Meaning |
| --- | --- |
| `mode` | `pomodoro` \| `timer` \| `stopwatch` |
| `startedAt` | epoch ms, when the run began |
| `endsAt` | epoch ms, null for stopwatch |
| `pausedAt` | epoch ms, null when running |
| `taskId` | selected task, or null |
| `label` | user-typed session name, or null |
| `sessionsDone` | completed pomodoros this run, for break length |
| `isBreak` | whether the current leg is a break |
| `runId` | monotonic id, used to reject stale timeouts |

Absence of the key means no active session.

### Shared module, loaded everywhere

Timer logic moves from `pomodoro.html` into `static/focus_timer.js`, included
from `base.html` so it loads on every page. It owns:

- reading and writing the localStorage state
- deriving remaining time
- rendering the floating pill
- detecting completion, saving, and notifying

`pomodoro.html` keeps the setup screen and the fullscreen view, and becomes a
consumer of the shared module rather than the owner of the countdown. This
split is required, not cosmetic: the pill needs identical logic on every page,
and duplicating it guarantees the two copies drift.

### The pill

A small fixed-position element on every page showing remaining time and a
pause/resume control. Tapping it navigates to `/pomodoro`, which opens the
fullscreen view onto the running session. Hidden when no session is active.

The fullscreen view gains a **–** (minimise) button beside the existing **✕**.
Minimise hides the fullscreen and leaves the session running. **✕** keeps its
current meaning: end the session now, with the existing partial-save rule.

### Completion

Whichever page is open when `Date.now() >= endsAt`:

1. Claim the completion (see below), then `POST /pomodoro/save` as today.
2. Show a notification via the existing service worker registration. If
   permission was never granted, fall back to the in-page toast and a "done"
   state on the pill.
3. Clear the session state, or for pomodoro, transition to the break leg.

The user is not navigated away from whatever page they are on.

### Duplicate-tab guard

Two open tabs would both observe completion and both save. Before saving, a tab
writes `completedRunId` into the state via a read-modify-write. Only the tab
that finds the slot empty proceeds to save. The `storage` event lets the losing
tab update its pill without saving.

### Returning after the end time passed

If the app is reopened and the stored `endsAt` is in the past, the session is
recorded as completed. The elapsed time genuinely passed and the user
deliberately set the timer.

Known trade-off: a session started and abandoned still counts. Accepted for
now; revisit if it proves annoying in practice.

## Risks

**This rewires code stabilised on 2026-07-23.** Auto-start on Start Session,
saving on completion, and exiting focus mode after save were all fixed today
(`c6cfb3b` and earlier). Each must be re-verified after this change rather than
assumed to survive.

**`pomodoro.html` is already ~1300 lines.** Moving the timer core out reduces
it, but the boundary between "page owns setup UI" and "module owns the session"
must stay sharp or both ends get tangled.

**localStorage is per-browser.** A session started on a phone is invisible on a
laptop. Acceptable within the chosen scope; only server-side tracking fixes it.

## Verification

No test framework exists in this repo, so verification is manual and must be
done against the deployed app, per mode:

1. Start a timer, navigate to a note, confirm the pill shows and counts down.
2. Background the tab for two minutes, return, confirm the remaining time
   matches real elapsed time rather than lagging.
3. Let a session finish while on another page — confirm it saves, notifies, and
   appears in Recent Sessions.
4. Minimise then reopen the fullscreen view, confirm it shows the same session.
5. Confirm **✕** still ends a session and partial-saves past one minute.
6. Repeat 1 and 3 for pomodoro and stopwatch.
7. Open two tabs, let a session complete, confirm exactly one row is saved.
