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

  // Desktop Chrome/Edge get a real interactive floating window (Document PiP).
  // Android has no such thing — the only way to float over other apps there is
  // a video, so the pill is drawn to a canvas and shown as a view-only video
  // in system Picture-in-Picture. iPhone supports neither; the button is absent.
  const DOC_PIP = 'documentPictureInPicture' in window;
  const VIDEO_PIP = typeof document !== 'undefined'
    && !!document.pictureInPictureEnabled
    && typeof HTMLVideoElement !== 'undefined'
    && 'requestPictureInPicture' in HTMLVideoElement.prototype;
  const PIP_SUPPORTED = DOC_PIP || VIDEO_PIP;
  let pipWindow = null;
  let pipTick = null;
  let pipVideo = null;
  let pipCanvas = null;
  let pipDraw = null;

  // The pomodoro page registers this so tapping the pill re-opens the
  // fullscreen in place instead of reloading /pomodoro — a reload would
  // destroy the page-local <audio> and stop the focus sound.
  let reopenHandler = null;

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
    // The pop-out button only exists where Document Picture-in-Picture does —
    // desktop Chrome/Edge. Everywhere else it is simply absent.
    const popBtn = PIP_SUPPORTED
      ? '<button type="button" class="focus-pill-pop" id="focusPillPop" title="Float over other apps">⧉</button>'
      : '';
    pillEl.innerHTML =
      '<button type="button" class="focus-pill-main" id="focusPillMain">' +
        '<i class="bi bi-hourglass-split"></i><span id="focusPillTime">0:00</span>' +
      '</button>' +
      '<button type="button" class="focus-pill-toggle" id="focusPillToggle" title="Pause/resume">⏸</button>' +
      popBtn;
    document.body.appendChild(pillEl);

    document.getElementById('focusPillMain').addEventListener('click', () => {
      // In-page reopen when the pomodoro page is what's showing the pill;
      // otherwise navigate there.
      if (reopenHandler) { reopenHandler(); return; }
      window.location.href = '/pomodoro';
    });
    document.getElementById('focusPillToggle').addEventListener('click', (e) => {
      e.stopPropagation();
      const s = get();
      if (!s) return;
      if (s.pausedAt === null) pause(); else resume();
    });
    if (PIP_SUPPORTED) {
      document.getElementById('focusPillPop').addEventListener('click', (e) => {
        e.stopPropagation();
        popOut();
      });
    }

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

  // ── Float over other apps (desktop Chrome/Edge only) ──
  // A Document Picture-in-Picture window is a real always-on-top OS window
  // holding live HTML, so the pill can float over other applications and stay
  // interactive. Styles are inlined because the app stylesheet and the icon
  // font are not loaded in the PiP document.
  // Pull the live theme colours from the page so the floating window matches
  // whichever theme the user is on, rather than a hardcoded look.
  function readTheme() {
    const cs = getComputedStyle(document.documentElement);
    const v = (n, fb) => ((cs.getPropertyValue(n) || '').trim() || fb);
    return {
      accent: v('--accent-1', '#E07B39'),
      text: v('--text-primary', '#2D2A26'),
      bg: v('--input-bg', '#FDF6F0'),
    };
  }

  // The pill itself, filling the window edge-to-edge. The window background is
  // set to the pill fill colour so the residual square corners (an OS window is
  // always rectangular) blend in and the whole thing reads as the pill.
  function pipStyle(t) {
    return `
      :root { color-scheme: light dark; }
      * { box-sizing: border-box; }
      html, body { margin: 0; height: 100%; }
      body { background: ${t.bg}; display: flex; align-items: center;
        justify-content: center; padding: 7px;
        font-family: 'Space Grotesk', 'Segoe UI', system-ui, sans-serif;
        color: ${t.text}; user-select: none; }
      .pill { display: flex; align-items: center; gap: 8px;
        width: 100%; height: 100%; padding: 6px 16px;
        background: ${t.bg}; border: 2px solid ${t.text}; border-radius: 999px;
        box-shadow: 3px 3px 0 ${t.text}; }
      .ic { display: flex; flex: 0 0 auto; opacity: 0.85; }
      .t { font-size: 26px; font-weight: 700; font-variant-numeric: tabular-nums;
        letter-spacing: 1px; line-height: 1; flex: 1; }
      button { background: none; border: none; color: inherit; cursor: pointer;
        font-size: 18px; padding: 2px 5px; border-radius: 999px; line-height: 1;
        flex: 0 0 auto; }
      button:hover { background: ${t.accent}33; }
    `;
  }

  const PIP_HOURGLASS =
    '<svg class="ic" width="16" height="16" viewBox="0 0 16 16" fill="currentColor">' +
    '<path d="M3 1h10a.5.5 0 0 1 0 1H12v1.5A4 4 0 0 1 9.2 8 4 4 0 0 1 12 12.5V14h1a.5.5 0 0 1 0 1H3a.5.5 0 0 1 0-1h1v-1.5A4 4 0 0 1 6.8 8 4 4 0 0 1 4 3.5V2H3a.5.5 0 0 1 0-1Z"/></svg>';

  function renderPipWidget(win) {
    const s = get();
    if (!s) { closePipWidget(); return; }
    const secs = s.mode === 'stopwatch' ? core.elapsedSecs(s) : core.remainingSecs(s);
    const timeEl = win.document.getElementById('pipTime');
    const toggleEl = win.document.getElementById('pipToggle');
    if (timeEl) timeEl.textContent = formatClock(secs);
    if (toggleEl) toggleEl.textContent = s.pausedAt === null ? '⏸' : '▶';
  }

  function closePipWidget() {
    if (pipTick && pipWindow) { pipWindow.clearInterval(pipTick); }
    pipTick = null;
    if (pipWindow && !pipWindow.closed) pipWindow.close();
    pipWindow = null;
    pillHidden = false;
    renderPill(get());
  }

  // Desktop gets the interactive window; Android gets the view-only video.
  function popOut() {
    if (DOC_PIP) return popOutDoc();
    if (VIDEO_PIP) return popOutVideo();
  }

  async function popOutDoc() {
    const s = get();
    if (!s) return;
    if (pipWindow && !pipWindow.closed) { pipWindow.focus(); return; }

    try {
      pipWindow = await window.documentPictureInPicture.requestWindow({ width: 210, height: 74 });
    } catch (e) {
      console.error('[focus] could not open floating window', e);
      pipWindow = null;
      return;
    }

    const style = pipWindow.document.createElement('style');
    style.textContent = pipStyle(readTheme());
    pipWindow.document.head.appendChild(style);

    const label = (s.label || 'Focus session').replace(/</g, '&lt;');
    pipWindow.document.body.innerHTML =
      '<div class="pill" title="' + label + '">' +
        PIP_HOURGLASS +
        '<span class="t" id="pipTime">0:00</span>' +
        '<button id="pipToggle" title="Pause/resume">⏸</button>' +
        '<button id="pipStop" title="End session">✕</button>' +
      '</div>';

    pipWindow.document.getElementById('pipToggle').addEventListener('click', () => {
      const cur = get();
      if (!cur) return;
      if (cur.pausedAt === null) pause(); else resume();
      renderPipWidget(pipWindow);
    });
    pipWindow.document.getElementById('pipStop').addEventListener('click', () => {
      stop();
      closePipWidget();
    });

    // The interval is scheduled on the PiP window's own event loop, which stays
    // visible (and unthrottled) even when the main Monad tab is backgrounded.
    pipTick = pipWindow.setInterval(() => renderPipWidget(pipWindow), 500);
    renderPipWidget(pipWindow);

    // User closed it, the opener navigated, or the session ended.
    pipWindow.addEventListener('pagehide', () => { pipTick = null; pipWindow = null; pillHidden = false; renderPill(get()); });

    // While it floats, the in-app pill would be redundant.
    pillHidden = true;
    renderPill(get());
  }

  // ── Android: view-only floating video ──
  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function drawPipFrame(ctx, W, H, t) {
    const cur = get();
    if (!cur) { closePipVideo(); return; }
    const secs = cur.mode === 'stopwatch' ? core.elapsedSecs(cur) : core.remainingSecs(cur);
    const label = secs === 0 && cur.mode !== 'stopwatch' ? 'Done' : formatClock(secs);
    ctx.clearRect(0, 0, W, H);
    const x = 10, y = 10, w = W - 20, h = H - 20, r = h / 2;
    ctx.fillStyle = t.bg;
    roundRect(ctx, x, y, w, h, r); ctx.fill();
    ctx.lineWidth = 4; ctx.strokeStyle = t.text;
    roundRect(ctx, x, y, w, h, r); ctx.stroke();
    ctx.fillStyle = t.text;
    ctx.font = '700 56px "Space Grotesk", system-ui, sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText((cur.pausedAt !== null ? '⏸ ' : '') + label, W / 2, H / 2 + 2);
  }

  async function popOutVideo() {
    const s = get();
    if (!s) return;
    if (document.pictureInPictureElement) { try { await document.exitPictureInPicture(); } catch (e) {} }

    const t = readTheme();
    const W = 340, H = 150, dpr = 2;
    pipCanvas = document.createElement('canvas');
    pipCanvas.width = W * dpr; pipCanvas.height = H * dpr;
    const ctx = pipCanvas.getContext('2d');
    ctx.scale(dpr, dpr);
    drawPipFrame(ctx, W, H, t);
    // Redraw as the clock advances. On Android this may slow while the tab is
    // backgrounded; because the time is clock-derived, each frame it does draw
    // is still correct.
    pipDraw = setInterval(() => drawPipFrame(ctx, W, H, t), 500);

    pipVideo = document.createElement('video');
    pipVideo.muted = true;
    pipVideo.setAttribute('playsinline', '');
    pipVideo.style.cssText = 'position:fixed; left:-9999px; width:2px; height:2px; opacity:0;';
    pipVideo.srcObject = pipCanvas.captureStream(2);
    document.body.appendChild(pipVideo);

    try {
      await pipVideo.play();
      await pipVideo.requestPictureInPicture();
    } catch (e) {
      console.error('[focus] could not open floating video', e);
      closePipVideo();
      return;
    }

    pipVideo.addEventListener('leavepictureinpicture', () => closePipVideo());
    pillHidden = true;
    renderPill(get());
  }

  function closePipVideo() {
    if (pipDraw) { clearInterval(pipDraw); pipDraw = null; }
    if (pipVideo) {
      try {
        if (document.pictureInPictureElement === pipVideo) document.exitPictureInPicture();
      } catch (e) { /* already gone */ }
      if (pipVideo.srcObject) pipVideo.srcObject.getTracks().forEach((tr) => tr.stop());
      pipVideo.remove();
      pipVideo = null;
    }
    pipCanvas = null;
    pillHidden = false;
    renderPill(get());
  }

  // Coming back to a foregrounded tab should repaint immediately rather than
  // waiting up to a second for the next interval.
  document.addEventListener('visibilitychange', () => { if (!document.hidden) tick(); });

  window.FocusTimer = {
    get, start, stop, pause, resume, update,
    onComplete: (fn) => completeHandlers.push(fn),
    onTick: (fn) => tickHandlers.push(fn),
    setPillHidden, formatClock,
    popOut, pipSupported: PIP_SUPPORTED,
    onReopen: (fn) => { reopenHandler = fn; },
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

  // Buzz the user when a focus session ends, so they hear it in another app.
  // Runs on every page (independent of the save handler above), reads the
  // passed session rather than the store (which stop() may have cleared), and
  // only fires when they have already enabled notifications via the bell —
  // never a permission prompt at the moment a session completes.
  function notifyComplete(session) {
    if (session.isBreak) return;
    if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
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

  document.addEventListener('DOMContentLoaded', () => { if (get()) startTicking(); else renderPill(null); });
})();
