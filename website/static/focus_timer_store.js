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
