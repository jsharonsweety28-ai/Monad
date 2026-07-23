# Background Focus Timer — Stage 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A focus session survives navigating anywhere in the app and stays accurate while the tab is backgrounded, showing a floating pill on every page.

**Architecture:** Stop counting ticks; store the session's finish time in `localStorage` and derive remaining time from the clock. Pure time arithmetic lives in a dependency-free core module testable under Node; DOM and lifecycle live in a companion module loaded from `base.html` on every page. `pomodoro.html` keeps its setup and fullscreen UI but stops owning the countdown.

**Tech Stack:** Vanilla JS (no build step, no bundler), Jinja templates, Flask. Tests run with Node's built-in `node --test` — no npm install, no `package.json`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-23-background-focus-timer-design.md`.
- No new runtime dependencies. No npm packages, no CDN scripts.
- Out of scope for Stage 1: OS notifications, and the two-tabs-open duplicate-save guard. Both are Stage 2.
- Sub-minute sessions continue to be discarded. This is the user's explicit decision, not an oversight — do not "fix" it.
- Pomodoro completion continues into its break rather than exiting focus mode.
- Duration is sent to `POST /pomodoro/save` in whole minutes, as today.
- Preserve behaviour fixed on 2026-07-23: "Start Session" begins the countdown immediately; timer and stopwatch return to the setup screen ~1.8s after saving; `✕` ends a session and partial-saves past one minute.
- localStorage key: `monad.focusSession`. One key, one JSON object.
- Every task ends with a commit. Do not push; the user pushes.

---

### Task 1: Pure time-arithmetic core

**Files:**
- Create: `website/static/focus_timer_core.js`
- Test: `tests/focus_timer_core.test.js`

**Interfaces:**
- Consumes: nothing.
- Produces: global `FocusTimerCore` in the browser, `module.exports` under Node, with:
  - `createSession({mode, durationSecs, taskId, label, now}) -> session`
  - `remainingSecs(session, now) -> number` (0 once finished; 0 for stopwatch)
  - `elapsedSecs(session, now) -> number`
  - `isComplete(session, now) -> boolean`
  - `pause(session, now) -> session`
  - `resume(session, now) -> session`
  - `isValid(session) -> boolean`

  `session` shape: `{mode, startedAt, endsAt, pausedAt, taskId, label, sessionsDone, isBreak, runId}`. All times are epoch milliseconds. `endsAt` is `null` for stopwatch. `pausedAt` is `null` while running.

- [ ] **Step 1: Write the failing test**

Create `tests/focus_timer_core.test.js`:

```js
const test = require('node:test');
const assert = require('node:assert');
const core = require('../website/static/focus_timer_core.js');

const T0 = 1_700_000_000_000; // fixed clock, keeps tests deterministic

test('createSession sets endsAt from duration', () => {
  const s = core.createSession({ mode: 'timer', durationSecs: 120, now: T0 });
  assert.strictEqual(s.endsAt, T0 + 120_000);
  assert.strictEqual(s.pausedAt, null);
});

test('stopwatch has no endsAt', () => {
  const s = core.createSession({ mode: 'stopwatch', durationSecs: 0, now: T0 });
  assert.strictEqual(s.endsAt, null);
});

test('remaining counts down with the clock', () => {
  const s = core.createSession({ mode: 'timer', durationSecs: 120, now: T0 });
  assert.strictEqual(core.remainingSecs(s, T0 + 30_000), 90);
});

// The whole point of the redesign: a gap where the page was not running
// must not slow the countdown down.
test('remaining is correct after a long gap with no ticks', () => {
  const s = core.createSession({ mode: 'timer', durationSecs: 120, now: T0 });
  assert.strictEqual(core.remainingSecs(s, T0 + 119_000), 1);
  assert.strictEqual(core.remainingSecs(s, T0 + 500_000), 0);
});

test('remaining never goes negative', () => {
  const s = core.createSession({ mode: 'timer', durationSecs: 60, now: T0 });
  assert.strictEqual(core.remainingSecs(s, T0 + 999_000), 0);
});

test('isComplete flips at the finish time', () => {
  const s = core.createSession({ mode: 'timer', durationSecs: 60, now: T0 });
  assert.strictEqual(core.isComplete(s, T0 + 59_000), false);
  assert.strictEqual(core.isComplete(s, T0 + 60_000), true);
});

test('stopwatch is never complete', () => {
  const s = core.createSession({ mode: 'stopwatch', durationSecs: 0, now: T0 });
  assert.strictEqual(core.isComplete(s, T0 + 9_999_000), false);
});

test('elapsed counts up for stopwatch', () => {
  const s = core.createSession({ mode: 'stopwatch', durationSecs: 0, now: T0 });
  assert.strictEqual(core.elapsedSecs(s, T0 + 45_000), 45);
});

test('paused time is frozen, not counted', () => {
  const s = core.createSession({ mode: 'timer', durationSecs: 120, now: T0 });
  const p = core.pause(s, T0 + 30_000);
  assert.strictEqual(core.remainingSecs(p, T0 + 30_000), 90);
  assert.strictEqual(core.remainingSecs(p, T0 + 90_000), 90); // still 90
});

test('resume pushes the finish time out by the paused duration', () => {
  const s = core.createSession({ mode: 'timer', durationSecs: 120, now: T0 });
  const p = core.pause(s, T0 + 30_000);
  const r = core.resume(p, T0 + 90_000); // paused for 60s
  assert.strictEqual(r.pausedAt, null);
  assert.strictEqual(r.endsAt, T0 + 180_000);
  assert.strictEqual(core.remainingSecs(r, T0 + 90_000), 90);
});

test('pausing a stopwatch freezes elapsed and resume excludes the gap', () => {
  const s = core.createSession({ mode: 'stopwatch', durationSecs: 0, now: T0 });
  const p = core.pause(s, T0 + 30_000);
  assert.strictEqual(core.elapsedSecs(p, T0 + 90_000), 30);
  const r = core.resume(p, T0 + 90_000);
  assert.strictEqual(core.elapsedSecs(r, T0 + 100_000), 40);
});

test('isValid rejects junk', () => {
  assert.strictEqual(core.isValid(null), false);
  assert.strictEqual(core.isValid({}), false);
  assert.strictEqual(core.isValid({ mode: 'nope', startedAt: T0 }), false);
  assert.strictEqual(
    core.isValid(core.createSession({ mode: 'timer', durationSecs: 60, now: T0 })),
    true
  );
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --test tests/focus_timer_core.test.js`

Expected: FAIL — `Cannot find module '../website/static/focus_timer_core.js'`.

- [ ] **Step 3: Write the implementation**

Create `website/static/focus_timer_core.js`:

```js
// Pure time arithmetic for the focus timer. No DOM, no storage, no globals
// beyond the single export — so it can be require()d by node --test.
//
// Everything derives from the clock rather than from accumulated ticks:
// a backgrounded tab, a sleeping phone, or a page load must not change how
// much time a session has left.
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.FocusTimerCore = api;
})(typeof self !== 'undefined' ? self : this, function () {
  const MODES = ['pomodoro', 'timer', 'stopwatch'];

  function createSession({ mode, durationSecs, taskId = null, label = null, now = Date.now() }) {
    return {
      mode: mode,
      startedAt: now,
      endsAt: mode === 'stopwatch' ? null : now + durationSecs * 1000,
      pausedAt: null,
      taskId: taskId,
      label: label,
      sessionsDone: 0,
      isBreak: false,
      runId: now,
    };
  }

  // While paused, the clock we measure against is frozen at pausedAt.
  function referenceNow(session, now) {
    return session.pausedAt === null ? now : session.pausedAt;
  }

  function remainingSecs(session, now = Date.now()) {
    if (session.endsAt === null) return 0;
    const ms = session.endsAt - referenceNow(session, now);
    return ms <= 0 ? 0 : Math.round(ms / 1000);
  }

  function elapsedSecs(session, now = Date.now()) {
    const ms = referenceNow(session, now) - session.startedAt;
    return ms <= 0 ? 0 : Math.round(ms / 1000);
  }

  function isComplete(session, now = Date.now()) {
    if (session.endsAt === null) return false;
    return referenceNow(session, now) >= session.endsAt;
  }

  function pause(session, now = Date.now()) {
    if (session.pausedAt !== null) return session;
    return Object.assign({}, session, { pausedAt: now });
  }

  // Shift both ends forward by the paused duration, so paused time counts as
  // neither focus time nor progress toward the finish.
  function resume(session, now = Date.now()) {
    if (session.pausedAt === null) return session;
    const delta = now - session.pausedAt;
    return Object.assign({}, session, {
      pausedAt: null,
      startedAt: session.startedAt + delta,
      endsAt: session.endsAt === null ? null : session.endsAt + delta,
    });
  }

  function isValid(session) {
    if (!session || typeof session !== 'object') return false;
    if (MODES.indexOf(session.mode) === -1) return false;
    if (typeof session.startedAt !== 'number') return false;
    if (session.endsAt !== null && typeof session.endsAt !== 'number') return false;
    return true;
  }

  return { MODES, createSession, remainingSecs, elapsedSecs, isComplete, pause, resume, isValid };
});
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --test tests/focus_timer_core.test.js`

Expected: PASS, 12 tests.

- [ ] **Step 5: Commit**

```bash
git add website/static/focus_timer_core.js tests/focus_timer_core.test.js
git commit -m "Add pure time-arithmetic core for the focus timer"
```

---

### Task 2: Session store on top of localStorage

**Files:**
- Create: `website/static/focus_timer_store.js`
- Test: `tests/focus_timer_store.test.js`

**Interfaces:**
- Consumes: `FocusTimerCore` from Task 1 (`isValid`).
- Produces: global `FocusTimerStore` / `module.exports` with:
  - `load() -> session | null`
  - `save(session) -> void`
  - `clear() -> void`
  - `setStorage(impl) -> void` — injection point for tests; `impl` needs `getItem`, `setItem`, `removeItem`.

  Corrupt or unparseable stored values are treated as "no session" and cleared.

- [ ] **Step 1: Write the failing test**

Create `tests/focus_timer_store.test.js`:

```js
const test = require('node:test');
const assert = require('node:assert');
const core = require('../website/static/focus_timer_core.js');
const store = require('../website/static/focus_timer_store.js');

const T0 = 1_700_000_000_000;

function fakeStorage() {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
    _map: map,
  };
}

test('load returns null when nothing is stored', () => {
  store.setStorage(fakeStorage());
  assert.strictEqual(store.load(), null);
});

test('save then load round-trips the session', () => {
  store.setStorage(fakeStorage());
  const s = core.createSession({ mode: 'timer', durationSecs: 120, label: 'chem', now: T0 });
  store.save(s);
  assert.deepStrictEqual(store.load(), s);
});

test('clear removes the session', () => {
  store.setStorage(fakeStorage());
  store.save(core.createSession({ mode: 'timer', durationSecs: 60, now: T0 }));
  store.clear();
  assert.strictEqual(store.load(), null);
});

test('corrupt JSON is treated as no session', () => {
  const fake = fakeStorage();
  fake.setItem('monad.focusSession', '{not json');
  store.setStorage(fake);
  assert.strictEqual(store.load(), null);
});

test('structurally invalid session is discarded, not returned', () => {
  const fake = fakeStorage();
  fake.setItem('monad.focusSession', JSON.stringify({ mode: 'bogus' }));
  store.setStorage(fake);
  assert.strictEqual(store.load(), null);
  assert.strictEqual(fake.getItem('monad.focusSession'), null);
});

test('a storage that throws does not take the page down', () => {
  store.setStorage({
    getItem: () => { throw new Error('denied'); },
    setItem: () => { throw new Error('denied'); },
    removeItem: () => { throw new Error('denied'); },
  });
  assert.strictEqual(store.load(), null);
  assert.doesNotThrow(() => store.save(core.createSession({ mode: 'timer', durationSecs: 60, now: T0 })));
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --test tests/focus_timer_store.test.js`

Expected: FAIL — `Cannot find module '../website/static/focus_timer_store.js'`.

- [ ] **Step 3: Write the implementation**

Create `website/static/focus_timer_store.js`:

```js
// Persistence for the active focus session. Deliberately tiny: one key, one
// JSON object, and every access defensive — Safari private mode and blocked
// third-party storage both make localStorage throw rather than return null.
(function (root, factory) {
  const core = typeof require === 'function' ? require('./focus_timer_core.js') : root.FocusTimerCore;
  const api = factory(core);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.FocusTimerStore = api;
})(typeof self !== 'undefined' ? self : this, function (core) {
  const KEY = 'monad.focusSession';

  let storage = null;
  try {
    storage = typeof localStorage !== 'undefined' ? localStorage : null;
  } catch (e) {
    storage = null;
  }

  function setStorage(impl) { storage = impl; }

  function clear() {
    if (!storage) return;
    try { storage.removeItem(KEY); } catch (e) { /* nothing useful to do */ }
  }

  function load() {
    if (!storage) return null;
    let raw;
    try { raw = storage.getItem(KEY); } catch (e) { return null; }
    if (!raw) return null;

    let parsed;
    try { parsed = JSON.parse(raw); } catch (e) { clear(); return null; }

    if (!core.isValid(parsed)) { clear(); return null; }
    return parsed;
  }

  function save(session) {
    if (!storage) return;
    try { storage.setItem(KEY, JSON.stringify(session)); } catch (e) { /* quota or denied */ }
  }

  return { KEY, load, save, clear, setStorage };
});
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `node --test "tests/*.test.js"`

Expected: PASS, 18 tests across both files.

- [ ] **Step 5: Commit**

```bash
git add website/static/focus_timer_store.js tests/focus_timer_store.test.js
git commit -m "Add localStorage-backed session store for the focus timer"
```

---

### Task 3: The floating pill, on every page

**Files:**
- Create: `website/static/focus_timer.js`
- Modify: `website/templates/base.html` — add three `<script>` tags immediately before the closing `</body>` (currently the last line of the file), after the existing inline scripts.

**Interfaces:**
- Consumes: `FocusTimerCore` (Task 1), `FocusTimerStore` (Task 2).
- Produces: global `FocusTimer` with:
  - `FocusTimer.get() -> session | null`
  - `FocusTimer.start(session) -> void` — persists and begins rendering
  - `FocusTimer.pause() / FocusTimer.resume() / FocusTimer.stop()`
  - `FocusTimer.onComplete(fn)` — registers a callback fired once when a session finishes
  - `FocusTimer.onTick(fn)` — fired each second with the current session
  - `FocusTimer.formatClock(secs) -> "MM:SS"`

  The pill is created lazily on first render and is hidden whenever `load()` returns null. Pages that want to own the display (the pomodoro fullscreen) call `FocusTimer.setPillHidden(true)`.

This task delivers a pill that appears and counts down; wiring the pomodoro page to create sessions is Task 4. To verify Task 3 alone, a session is seeded from the browser console.

- [ ] **Step 1: Write the module**

Create `website/static/focus_timer.js`:

```js
// Owns the live session: renders the floating pill on every page, ticks it,
// and fires completion wherever the user happens to be.
//
// The tick interval only drives repainting. All time comes from the clock via
// FocusTimerCore, so a throttled or suspended interval cannot desynchronise it.
(function () {
  const core = window.FocusTimerCore;
  const store = window.FocusTimerStore;
  if (!core || !store) return;

  let tickHandle = null;
  let pillEl = null;
  let pillHidden = false;
  let completeHandlers = [];
  let tickHandlers = [];
  let firedCompletionForRunId = null;

  function formatClock(secs) {
    const m = Math.floor(Math.abs(secs) / 60);
    const s = Math.abs(secs) % 60;
    return `${m}:${String(s).padStart(2, '0')}`;
  }

  function get() { return store.load(); }

  function ensurePill() {
    if (pillEl) return pillEl;
    pillEl = document.createElement('div');
    pillEl.id = 'focusPill';
    pillEl.innerHTML =
      '<button type="button" class="focus-pill-main" id="focusPillMain">' +
        '<i class="bi bi-hourglass-split"></i><span id="focusPillTime">0:00</span>' +
      '</button>' +
      '<button type="button" class="focus-pill-toggle" id="focusPillToggle" title="Pause/resume">⏸</button>';
    document.body.appendChild(pillEl);

    document.getElementById('focusPillMain').addEventListener('click', () => {
      window.location.href = '/pomodoro';
    });
    document.getElementById('focusPillToggle').addEventListener('click', (e) => {
      e.stopPropagation();
      const s = get();
      if (!s) return;
      if (s.pausedAt === null) pause(); else resume();
    });
    return pillEl;
  }

  function renderPill(session) {
    const el = ensurePill();
    if (!session || pillHidden) { el.style.display = 'none'; return; }
    el.style.display = 'flex';
    const secs = session.mode === 'stopwatch'
      ? core.elapsedSecs(session)
      : core.remainingSecs(session);
    document.getElementById('focusPillTime').textContent = formatClock(secs);
    document.getElementById('focusPillToggle').textContent = session.pausedAt === null ? '⏸' : '▶';
    el.classList.toggle('paused', session.pausedAt !== null);
  }

  function tick() {
    const session = get();
    if (!session) { renderPill(null); stopTicking(); return; }

    renderPill(session);
    tickHandlers.forEach((fn) => { try { fn(session); } catch (e) { console.error(e); } });

    if (core.isComplete(session) && firedCompletionForRunId !== session.runId) {
      firedCompletionForRunId = session.runId;
      completeHandlers.forEach((fn) => { try { fn(session); } catch (e) { console.error(e); } });
    }
  }

  function startTicking() {
    if (tickHandle) return;
    tickHandle = setInterval(tick, 1000);
    tick();
  }

  function stopTicking() {
    if (!tickHandle) return;
    clearInterval(tickHandle);
    tickHandle = null;
  }

  function start(session) { store.save(session); firedCompletionForRunId = null; startTicking(); }
  function stop() { store.clear(); renderPill(null); stopTicking(); }

  function pause() {
    const s = get();
    if (!s) return;
    store.save(core.pause(s, Date.now()));
    tick();
  }

  function resume() {
    const s = get();
    if (!s) return;
    store.save(core.resume(s, Date.now()));
    tick();
  }

  function update(session) { store.save(session); tick(); }

  function setPillHidden(hidden) { pillHidden = hidden; renderPill(get()); }

  // Coming back to a foregrounded tab should repaint immediately rather than
  // waiting up to a second for the next interval.
  document.addEventListener('visibilitychange', () => { if (!document.hidden) tick(); });

  window.FocusTimer = {
    get, start, stop, pause, resume, update,
    onComplete: (fn) => completeHandlers.push(fn),
    onTick: (fn) => tickHandlers.push(fn),
    setPillHidden, formatClock,
  };

  document.addEventListener('DOMContentLoaded', () => { if (get()) startTicking(); else renderPill(null); });
})();
```

- [ ] **Step 2: Add the pill's styles and script tags to `base.html`**

In `website/templates/base.html`, immediately before the final `</body>`, add:

```html
<style>
  #focusPill {
    position: fixed; bottom: 22px; right: 22px; z-index: 9998;
    display: none; align-items: center; gap: 2px;
    background: var(--input-bg); color: var(--text-primary);
    border: 2px solid var(--text-primary); border-radius: 999px;
    box-shadow: 3px 3px 0 var(--text-primary);
    font-family: 'Space Grotesk', sans-serif; font-weight: 700;
  }
  #focusPill.paused { opacity: 0.6; }
  #focusPill button {
    background: none; border: none; color: inherit; font: inherit;
    cursor: pointer; padding: 10px 14px; display: flex; align-items: center; gap: 8px;
  }
  #focusPill .focus-pill-toggle { padding-left: 6px; padding-right: 14px; }
  @media (max-width: 600px) { #focusPill { bottom: 76px; right: 14px; } }
</style>
<script src="{{ url_for('static', filename='focus_timer_core.js') }}"></script>
<script src="{{ url_for('static', filename='focus_timer_store.js') }}"></script>
<script src="{{ url_for('static', filename='focus_timer.js') }}"></script>
```

Order matters: core, then store, then the module that uses both.

- [ ] **Step 3: Verify the pill renders and counts down**

Start the app, open any page other than `/pomodoro` (the dashboard is fine), open the browser console and seed a session by hand:

```js
FocusTimer.start(FocusTimerCore.createSession({ mode: 'timer', durationSecs: 120 }));
```

Expected: a pill appears bottom-right showing `2:00` and counting down once per second.

Then, still in the console:

```js
location.href = '/daily';
```

Expected: after the new page loads, the pill is still there and shows roughly the correct remaining time — proving it survived a full page load.

Click the pill's ⏸ — the time freezes and the pill dims. Click ▶ — it resumes.

Click the pill body — it navigates to `/pomodoro`.

Finally: `FocusTimer.stop()` in the console. Expected: the pill disappears.

- [ ] **Step 4: Verify accuracy across a backgrounded tab**

Seed a 2-minute session as above, switch to a different browser tab for a full minute, then come back.

Expected: the remaining time reflects real elapsed time (roughly 1:00 left), not a lagging count. This is the behaviour the old tick-counting timer got wrong.

- [ ] **Step 5: Commit**

```bash
git add website/static/focus_timer.js website/templates/base.html
git commit -m "Render a floating focus-session pill on every page"
```

---

### Task 4: Pomodoro page creates sessions through the store

**Files:**
- Modify: `website/templates/pomodoro.html` — `startSession()`, `startTimer()`, `pauseTimer()`, `resetTimer()`, `updateDisplay()`, `completeSession()`, `endSession()`, and the module-level timer variables.

**Interfaces:**
- Consumes: `FocusTimer` (Task 3), `FocusTimerCore` (Task 1).
- Produces: no new exports. After this task the page holds no countdown state of its own — `timeLeft`, `totalTime` and the `setInterval` in `startTimer()` are gone, replaced by reads from `FocusTimer.get()`.

**Behaviour that must not regress** (all fixed earlier today):
- "▶ Start Session" begins the countdown immediately.
- Completion saves via `POST /pomodoro/save` and shows the existing toast.
- Timer and stopwatch return to the setup screen ~1.8s after saving; pomodoro goes to its break.
- `✕` ends the session and partial-saves anything past one minute.
- The session-name box still populates `label`.

- [ ] **Step 1: Replace the countdown state in `startSession()`**

The existing body computes `timeLeft` per mode and then opens the fullscreen. Keep the mode/duration/task/label reading exactly as it is, then replace the state assignment and the closing `startTimer()` call with a session handed to `FocusTimer`:

```js
    // durationSecs is whatever the existing per-mode branches computed
    const session = FocusTimerCore.createSession({
      mode: mode,
      durationSecs: durationSecs,
      taskId: selectedTask || null,
      label: sessionLabel || null,
    });
    FocusTimer.start(session);
    FocusTimer.setPillHidden(true); // fullscreen is showing; the pill would be redundant
```

Delete the `timer = setInterval(...)` block in `startTimer()` and the `timeLeft++/--` arithmetic. `startTimer()`/`pauseTimer()` become thin wrappers over `FocusTimer.resume()` / `FocusTimer.pause()`.

- [ ] **Step 2: Drive the fullscreen display from the shared tick**

Register a tick handler near the other initialisation at the bottom of the script:

```js
  FocusTimer.onTick((session) => {
    const secs = session.mode === 'stopwatch'
      ? FocusTimerCore.elapsedSecs(session)
      : FocusTimerCore.remainingSecs(session);
    document.getElementById('fsTimerDisplay').textContent = FocusTimer.formatClock(secs);
    updateRing(session, secs);
  });
```

`updateRing` keeps the existing stroke-dashoffset maths, taking the total from `session.endsAt - session.startedAt` instead of the removed `totalTime`.

- [ ] **Step 3: Move completion onto the shared event**

`completeSession()` stops being called from a local interval and becomes the `FocusTimer.onComplete` handler. Its body is unchanged apart from taking duration from the session:

```js
  FocusTimer.onComplete((session) => {
    if (session.isBreak) { /* existing break-finished branch, unchanged */ return; }
    const d = Math.floor(FocusTimerCore.elapsedSecs(session) / 60);
    saveSession(d).then(ok => showToast(ok
      ? `Session complete — ${d} min saved! <i class="bi bi-stars"></i>`
      : `Session complete — ${d} min, but ${saveFailedMsg().toLowerCase()}`));
    // ...remainder of the existing completeSession body, unchanged...
  });
```

`saveSession` reads `task_id` and `label` from the session rather than the page-level `selectedTask` / `sessionLabel`, so a completion firing on another page still sends the right values.

- [ ] **Step 4: Point `closeFocusMode` and `endSession` at the store**

`closeFocusMode()` gains `FocusTimer.setPillHidden(false)` so the pill reappears when the fullscreen is dismissed. `endSession()` keeps its partial-save, then calls `FocusTimer.stop()`.

- [ ] **Step 5: Re-verify every preserved behaviour**

Against the running app, in order. Each must pass before moving on:

1. Timer mode, 90 seconds, press Start Session once. Expected: counts down immediately without a second press.
2. Let it reach zero. Expected: confetti, "Session complete — 1 min saved!", back on the setup screen ~2s later, and the session listed in Recent Sessions.
3. Timer mode, 90 seconds, press `✕` after ~70 seconds. Expected: a partial session saved, shown dimmed with "ended early".
4. Timer mode, 90 seconds, press `✕` after ~10 seconds. Expected: nothing saved, no error toast.
5. Stopwatch, run past a minute, pause, press Save. Expected: saves, returns to the setup screen.
6. No task selected, type a session name, run 90 seconds. Expected: the name shows on the fullscreen timer and on the Recent Sessions card.
7. `POST /pomodoro/save` appears exactly once per saved session in the server log.

- [ ] **Step 6: Commit**

```bash
git add website/templates/pomodoro.html
git commit -m "Drive the pomodoro page from the shared focus session store"
```

---

### Task 5: Minimise button

**Files:**
- Modify: `website/templates/pomodoro.html` — the fullscreen header (the `<a class="fs-close">` at ~line 762) and its script.

**Interfaces:**
- Consumes: `FocusTimer.setPillHidden` (Task 3).
- Produces: `minimizeFocus()` on the page.

- [ ] **Step 1: Add the button beside the existing close control**

Immediately before the existing `<a href="#" class="fs-close" onclick="endSession(); return false;">`:

```html
    <a href="#" class="fs-close" onclick="minimizeFocus(); return false;" title="Minimise, keep running">–</a>
```

- [ ] **Step 2: Implement it**

```js
  // Hide the fullscreen view without touching the session — it keeps running
  // and reappears as the pill.
  function minimizeFocus() {
    document.getElementById('fullscreenTimer').classList.remove('show');
    document.getElementById('configPage').style.display = 'flex';
    timerOpen = false;
    FocusTimer.setPillHidden(false);
  }
```

Note it does **not** call `endSession()`, `FocusTimer.stop()`, or the partial save.

- [ ] **Step 3: Verify**

1. Start a 2-minute timer, press **–**. Expected: back on the setup screen, pill visible bottom-right, still counting.
2. Navigate to the notes page. Expected: pill still there, still counting.
3. Tap the pill. Expected: back on `/pomodoro`.
4. Press **✕** instead. Expected: session ends, pill disappears.

- [ ] **Step 4: Commit**

```bash
git add website/templates/pomodoro.html
git commit -m "Add minimise button to the focus screen"
```

---

### Task 6: Completion while on another page

**Files:**
- Modify: `website/static/focus_timer.js` — add a default completion handler.

**Interfaces:**
- Consumes: `FocusTimer.onComplete` (Task 3).
- Produces: nothing new. Pages that register their own `onComplete` (the pomodoro page) still receive it; this adds the fallback for every other page.

- [ ] **Step 1: Save from whichever page is open**

Append inside `focus_timer.js`, before the closing `})();`:

```js
  // Every page needs to be able to finish a session, not just /pomodoro —
  // the whole point is that the user wandered off to write notes.
  let pageOwnsCompletion = false;
  window.FocusTimer.claimCompletion = () => { pageOwnsCompletion = true; };

  window.FocusTimer.onComplete((session) => {
    if (pageOwnsCompletion) return; // the pomodoro page handles its own UI
    const mins = Math.floor(core.elapsedSecs(session) / 60);
    if (mins > 0) {
      fetch('/pomodoro/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: session.taskId, label: session.label,
          duration: mins, mode: session.mode, partial: false,
        }),
      }).catch((e) => console.error('[focus] background save failed', e));
    }
    stop();
  });
```

The pomodoro page calls `FocusTimer.claimCompletion()` during its own setup so a session finishing there is not saved twice.

- [ ] **Step 2: Verify**

1. Start a 2-minute timer on `/pomodoro`, press **–**, navigate to the notes page, and write something until it finishes.
2. Expected: the pill disappears when it completes, and the session appears in Recent Sessions on returning to `/pomodoro`.
3. Check the server log: exactly one `POST /pomodoro/save`, status 200.
4. Repeat with the fullscreen view open on `/pomodoro`. Expected: still exactly one save, with the usual confetti and toast.
5. Spec requirement — a session whose finish time passed while the app was closed: start a 2-minute timer, close the tab entirely, wait past two minutes, reopen the app. Expected: on load the session is recognised as finished, saved once, and the pill does not appear. This is the "abandoned sessions still count" decision from the spec; if it feels wrong in practice, that is the moment to say so.

- [ ] **Step 3: Run the full test suite and commit**

```bash
node --test "tests/*.test.js"
git add website/static/focus_timer.js website/templates/pomodoro.html
git commit -m "Complete and save a focus session from any page"
```

---

## Stage 2 (not this plan)

Deferred deliberately, to keep Stage 1 verifiable:

- OS notification on completion, reusing the existing service worker and the bell-icon permission flow.
- Duplicate-save guard for two open tabs, via a `completedRunId` claim written into the stored session before saving.

Write these as their own plan once Stage 1 is confirmed working in the deployed app.
