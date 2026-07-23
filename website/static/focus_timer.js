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
  const completeHandlers = [];
  const tickHandlers = [];
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
