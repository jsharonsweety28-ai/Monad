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
