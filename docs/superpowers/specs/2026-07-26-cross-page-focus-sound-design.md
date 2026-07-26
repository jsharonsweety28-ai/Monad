# Cross-page Focus Sound — Design

Date: 2026-07-26
Status: approved, not yet implemented

## Goal

Keep the focus sound playing as the user moves between Monad pages, the way
the pill already persists. Today the audio is a page-local `new Audio()` in
`pomodoro.html`, destroyed on every navigation.

## Approach

Move audio ownership into the shared, always-loaded `website/static/focus_timer.js`
(where the pill lives). It persists the chosen sound and, on each page load with
a running session, recreates the audio element and resumes playback.

The sound-chip UI on the pomodoro setup screen stays; it just calls
`FocusTimer.playSound(...)` instead of managing its own audio element.

## The autoplay constraint

A page can create and `play()` audio, but after a fresh navigation with no user
gesture the browser may reject `play()` (autoplay policy). In practice desktop
with engagement resumes seamlessly; mobile or low-engagement may block it.

Fallback: if `play()` rejects, set a `soundNeedsGesture` flag and show a muted
hint on the pill. A global capture-phase `click`/`keydown` listener resumes the
sound on the next user interaction (a gesture the browser accepts).

## Custom sounds

Custom uploads play from a blob URL that dies with the page, so they cannot be
recreated on another page. Built-in sounds (rain, lofi, cafe, forest) survive;
a custom sound is played but marked non-resumable, so on navigation it simply
stops (today's behaviour). Stated, not fixed.

## Components

### `focus_timer.js` — single audio controller

State: `soundEl` (the one Audio element), `soundNeedsGesture` (bool), and a
persisted `monad.focusSound` = `{ name, url, resumable }`.

- `playSound(name, url)` — `none`/empty → `stopSound()`. Otherwise stop any
  existing audio, persist `{name, url, resumable: url==null}` (built-ins pass no
  url and are resumable; custom passes a blob url and is not), create
  `new Audio(url || '/static/sounds/'+name+'.mp3')`, loop, volume 0.3, play;
  on reject set `soundNeedsGesture` and re-render the pill.
- `stopSound()` — clear persistence, drop the audio element, clear the flag.
- `resumeSoundOnLoad()` — on load: if no session, clear persistence and return;
  else if persisted `resumable` and `playing`, recreate from
  `/static/sounds/<name>.mp3` and play (may set the gesture flag).
- `armGestureResume()` — capture-phase `click`/`keydown` that, while
  `soundNeedsGesture`, retries play and clears the flag.

`FocusTimer.stop()` also calls `stopSound()` so ending a session ends its sound.
Exposed: `playSound`, `stopSound`, `soundNeedsGesture` (for the pill hint).

### `pomodoro.html` — delegate audio

`previewSound(sound, el)` keeps the chip UI and room image, but for playback:
`none` → `FocusTimer.stopSound()`; custom → `FocusTimer.playSound('custom', customSoundURL)`;
built-in → `FocusTimer.playSound(sound)`. The page-local `audioElement` and its
own `stopSound()` are removed; all callers route through `FocusTimer`.

The line in `startSession` that restarted the page-local audio is dropped —
`playSound` on chip selection already owns it, and `resumeSoundOnLoad` covers
reloads.

## Pill hint

When `soundNeedsGesture`, the pill shows a small muted glyph (🔇) so the user
knows a tap will bring the sound back. Cleared once resumed.

## Testing

Core audio output cannot be asserted headless, but the logic can:
1. `playSound('rain')` persists `{name:'rain', resumable:true}` and creates an
   audio element with the right src.
2. With a session and persisted sound, `resumeSoundOnLoad` recreates the audio.
3. With no session, `resumeSoundOnLoad` clears persistence.
4. `stopSound()` clears persistence and the element.
5. A blocked play sets `soundNeedsGesture`; a simulated click resumes and clears
   it.
6. `FocusTimer.stop()` also stops the sound.

On the user's devices: start a built-in sound, navigate between Monad pages,
confirm it keeps playing (desktop) or resumes on first tap (mobile).

## Out of scope

- Custom sounds surviving navigation (blob URL limitation).
- Playing with the browser fully closed (impossible for web audio).
