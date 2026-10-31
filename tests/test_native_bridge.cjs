const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let now = 1000, serial = 0;
const tasks = new Map(), calls = [], results = [];
const context = {Date: {now: () => now},
  setTimeout(fn, delay) { tasks.set(++serial, {fn, delay}); return serial; },
  clearTimeout(id) { tasks.delete(id); },
  external: {SuperCall(command, args) { calls.push([command, JSON.parse(args)]); }}
};
context.window = context;
context.__kugoWidgetResult = data => results.push(JSON.parse(data));
vm.runInNewContext(fs.readFileSync('native_bridge.js', 'utf8'), context);
function flush() { for (const [id, t] of [...tasks]) if (t.delay === 0) {tasks.delete(id); t.fn();} }
context.__kugoWidgetBridge.begin('control', 'next', 10000);
assert.equal(calls.length, 0);
flush();
assert.equal(calls.length, 1);
assert.equal(calls[0][0], 841);
assert.equal(tasks.size, 0, 'control must not start extra polling timers');
assert.throws(() => context.__kugoWidgetBridge.begin('early', 'snapshot', 10000));
now += 600;
context.__kugoWidgetBridge.begin('readback', 'snapshot', 10000);
flush();
assert.equal(calls.length, 2);
assert.equal(calls[1][0], 864);
context[calls[1][1].callback]({status: 1, hash: 'new-song', filename: 'Artist - Song', progress: '10000'});
assert.equal(results.at(-1).result.hash, 'new-song');
assert.equal(tasks.size, 0);
now += 1000;
context.__kugoWidgetBridge.begin('expired', 'next', now - 1);
flush();
assert.equal(calls.length, 2, 'expired action must not execute later');
console.log('single-action and one early readback regression passed');
