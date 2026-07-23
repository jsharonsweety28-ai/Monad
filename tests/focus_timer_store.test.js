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
