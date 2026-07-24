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

  // ── Draggable position, remembered across pages ──
  const POS_KEY = 'monad.focusPill.pos';
  let posApplied = false;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(v, hi));

  function loadPos() {
    try { const r = localStorage.getItem(POS_KEY); return r ? JSON.parse(r) : null; }
    catch (e) { return null; }
  }
  function savePos(pos) {
    try { localStorage.setItem(POS_KEY, JSON.stringify(pos)); } catch (e) { /* ignore */ }
  }

  // Move to a stored {left, top}, re-clamped to the current viewport so a
  // window resized smaller since last time can't strand the pill off-screen.
  function applyPos(el) {
    const p = loadPos();
    if (!p) return;
    const left = clamp(p.left, 4, window.innerWidth - el.offsetWidth - 4);
    const top = clamp(p.top, 4, window.innerHeight - el.offsetHeight - 4);
    el.style.left = left + 'px';
    el.style.top = top + 'px';
    el.style.right = 'auto';
    el.style.bottom = 'auto';
  }

  // Pointer events cover mouse and touch in one path. A press that moves less
  // than a few px is a tap (button fires); more than that is a drag, and the
  // trailing click is swallowed so a drag never navigates or pauses.
  function makeDraggable(el) {
    let drag = null;
    const onMove = (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 5) return;
      drag.moved = true;
      el.style.left = clamp(drag.left + dx, 4, window.innerWidth - el.offsetWidth - 4) + 'px';
      el.style.top = clamp(drag.top + dy, 4, window.innerHeight - el.offsetHeight - 4) + 'px';
      el.style.right = 'auto';
      el.style.bottom = 'auto';
      e.preventDefault();
    };
    const onUp = () => {
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
      if (drag && drag.moved) {
        savePos({ left: el.offsetLeft, top: el.offsetTop });
        el._justDragged = true;
        setTimeout(() => { el._justDragged = false; }, 50);
      }
      drag = null;
    };
    el.addEventListener('pointerdown', (e) => {
      if (e.button != null && e.button !== 0) return; // ignore right/middle click
      drag = { x: e.clientX, y: e.clientY, left: el.offsetLeft, top: el.offsetTop, moved: false };
      document.addEventListener('pointermove', onMove);
      document.addEventListener('pointerup', onUp);
    });
    // Capture phase, so a drag's click is killed before the buttons see it.
    el.addEventListener('click', (e) => {
      if (el._justDragged) { e.stopPropagation(); e.preventDefault(); }
    }, true);
  }

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

    makeDraggable(pillEl);
    // A resize can shrink the viewport out from under a stored position.
    window.addEventListener('resize', () => { if (pillEl.style.display !== 'none') applyPos(pillEl); });
    return pillEl;
  }

  function renderPill(session) {
    const el = ensurePill();
    if (!session || pillHidden) { el.style.display = 'none'; return; }
    el.style.display = 'flex';
    // offsetWidth is only real once shown, so restore the saved spot on the
    // first visible frame of this page load.
    if (!posApplied) { applyPos(el); posApplied = true; }
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

  // Every page needs to be able to finish a session, not just /pomodoro —
  // the whole point is that the user wandered off to write notes.
  let pageOwnsCompletion = false;
  window.FocusTimer.claimCompletion = () => { pageOwnsCompletion = true; };

  window.FocusTimer.onComplete((session) => {
    if (pageOwnsCompletion) return; // /pomodoro draws its own completion UI
    if (session.isBreak) { stop(); return; } // a finished break is not focus time

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

  document.addEventListener('DOMContentLoaded', () => { if (get()) startTicking(); else renderPill(null); });
})();
