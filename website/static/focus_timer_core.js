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
