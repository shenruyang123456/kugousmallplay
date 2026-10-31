const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

(async () => {
  const calls = [], listeners = {}, timers = new Map(), elements = new Map();
  let serial = 0;
  const document = {
    hidden: true,
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, {style: {}, addEventListener() {}, value: 0});
      return elements.get(id);
    },
    addEventListener(name, fn) { listeners[name] = fn; }
  };
  const window = {
    __TAURI__: {core: {invoke: async (_, data) => { calls.push(data.operation); return {connected: false}; }}},
    glassPlayer: {setPlaying() {}}, cover3D: {setCover: async () => {}}, addEventListener() {}
  };
  vm.runInNewContext(fs.readFileSync('glass-widget/src/player.js', 'utf8'), {
    document, window, console,
    setInterval(fn, ms) { timers.set(++serial, {fn, ms}); return serial; },
    clearInterval(id) { timers.delete(id); }, setTimeout() {}
  });
  await Promise.resolve();
  assert.equal(calls.length, 0, 'hidden widget must not keep the native service active');
  assert.equal(timers.size, 0, 'hidden widget must not keep waking timer callbacks');
  document.hidden = false;
  listeners.visibilitychange();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls, ['state']);
  assert.equal(timers.size, 2);
  const oldPoll = [...timers.values()].find(t => t.ms === 1500).fn;
  document.hidden = true;
  listeners.visibilitychange();
  assert.equal(timers.size, 0);
  await oldPoll();
  assert.equal(calls.length, 1, 'background polling must remain paused');
  document.hidden = false;
  listeners.visibilitychange();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(timers.size, 2, 'restore must not accumulate duplicate timers');
  assert.equal(calls.length, 2);
  console.log('visibility polling regression passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
