# Focus Session Completion Notification (local) — Design

Date: 2026-07-26
Status: approved, not yet implemented
Scope: local notification only. Server-scheduled push (fires with browser closed) is deferred.

## Goal

When a focus session reaches zero, show a system notification so the user is
told even if they have switched to another app — as long as a Monad tab is
still alive to fire it.

## Chosen scope and its limit

Local only. The page fires the notification itself on completion, reusing the
app's existing service worker and notification permission. It fires whenever a
Monad tab is running — including a backgrounded desktop tab, and an Android tab
kept alive by the floating Picture-in-Picture timer.

It does NOT fire when no page is running: browser fully closed, or a phone that
has frozen the tab (a plain backgrounded mobile tab with no PiP, and iPhone
generally). That case needs server-scheduled push, which is deferred.

## Reuse

Everything needed already exists and is untouched:
- `website/static/sw.js` — `showNotification` on push, and a `notificationclick`
  handler that opens `data.url`.
- Notification permission + push subscription flow via the bell icon
  (`enableNotifications` / `subscribePush` in `base.html`).
- Icons `icon-192.png` / `icon-72.png`.

No server changes, no new dependencies, no new push subscription. This is a
local `registration.showNotification`, not a server push.

## Design

A single completion handler added to `website/static/focus_timer.js`:

```
function notifyComplete(session) {
  if (session.isBreak) return;                         // breaks are not focus time
  if (typeof Notification === 'undefined') return;
  if (Notification.permission !== 'granted') return;    // never prompt at completion
  const mins = Math.max(1, Math.round(core.elapsedSecs(session) / 60));
  const title = 'Focus session complete 🎉';
  const opts = {
    body: (session.label || 'Focus session') + ' · ' + mins + ' min',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-72.png',
    tag: 'focus-complete',
    data: { url: '/pomodoro' },
    vibrate: [200, 100, 200],
  };
  if (navigator.serviceWorker && navigator.serviceWorker.ready) {
    navigator.serviceWorker.ready
      .then((reg) => reg.showNotification(title, opts))
      .catch(() => { try { new Notification(title, opts); } catch (e) {} });
  } else {
    try { new Notification(title, opts); } catch (e) {}
  }
}
window.FocusTimer.onComplete(notifyComplete);
```

### Why this placement works

- Registered as its own `onComplete` handler, so it runs on **every** page,
  independent of the `pageOwnsCompletion` flag that gates the background-save
  handler. The pomodoro page's own completion UI still runs alongside it.
- Reads the `session` passed to the handler, not `store.load()`, so it is
  correct even though another handler calls `stop()` (which clears the store)
  in the same completion.
- The completion event is already fired once per `runId`, so exactly one
  notification per session — no double-fire.

### Behaviour when permission is not granted

Nothing changes: the existing in-page toast still shows. No permission prompt is
raised at completion — enabling stays on the bell icon.

## Testing

Automated: none possible for a real OS notification. The pure timing logic is
already covered by the existing suite.

Manual / in-browser, as far as the environment allows:
1. With permission not granted, complete a session — confirm no error and the
   toast still shows.
2. Stub `Notification.permission = 'granted'` and spy on
   `registration.showNotification`; complete a session; confirm it is called
   once with the title and a body containing the label and minutes.
3. Confirm a break completion does NOT call it.

On the user's devices:
4. Desktop, notifications enabled: start a short session, switch to another
   app, confirm the notification fires and clicking it focuses Monad.
5. Android, notifications enabled, floating timer popped out: switch apps,
   confirm the notification fires while away.

## Out of scope

- Server-scheduled push (fires with browser closed / phone frozen).
- A "break over" notification.
- Prompting for permission at session start or completion.
