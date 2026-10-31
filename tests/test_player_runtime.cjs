const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function player(file) {
  let writes = 0, now = 100000;
  const elements = new Map();
  const counted = object => new Proxy(object, {set(target, key, value) {
    writes++; target[key] = value; return true;
  }});
  const context = vm.createContext({
    Date: {now: () => now}, console, clearInterval() {}, setInterval() {}, setTimeout() {},
    document: {hidden: false, addEventListener() {}, getElementById(id) {
      if (!elements.has(id)) elements.set(id, counted({style: counted({}), addEventListener() {}}));
      return elements.get(id);
    }},
    window: {glassPlayer: {setPlaying() {}}, cover3D: {setCover: async () => {}}, addEventListener() {}}
  });
  vm.runInContext(fs.readFileSync(file, 'utf8'), context);
  return {context, elements, reset() { writes = 0; }, writes: () => writes, setTime(value) { now = value; }};
}

function pausedWrites(file) {
  const p = player(file);
  vm.runInContext(`current = {connected:true, song:{duration_ms:180000,playback:'paused'},
    position_ms:10000, position_sampled_at:100000, lyrics_status:'暂无歌词'}; tick();`, p.context);
  p.reset();
  // Simulate 60 seconds of 250ms UI ticks, with the regular native sample refreshing age.
  for (let i = 0; i < 240; i++) {
    p.setTime(100000 + i * 250);
    if (i % 16 === 0) vm.runInContext(`current.position_sampled_at = ${100000 + i * 250}`, p.context);
    vm.runInContext('tick()', p.context);
  }
  return p.writes();
}

function animation(file, hz, seconds = 10) {
  const source = fs.readFileSync(file, 'utf8');
  // Exercise the real animation scheduler without needing a GPU in the unit test.
  const code = source.slice(source.indexOf('function render()'), source.indexOf('function resize()'));
  let serial = 0, draws = 0, rotation = 0;
  const frames = new Map(), listeners = {};
  const document = {hidden:false, addEventListener(name, fn) { listeners[name] = fn; }};
  const context = vm.createContext({document, Math, scene:{}, camera:{},
    THREE: {Vector3: class {}}, renderer: {render() { draws++; }},
    disc: {rotateZ(angle) { rotation += angle; }}, orbit: {rotateOnWorldAxis() {}},
    requestAnimationFrame(fn) { frames.set(++serial, fn); return serial; },
    cancelAnimationFrame(id) { frames.delete(id); }
  });
  vm.runInContext(code, context);
  for (let i = 0; i <= seconds * hz; i++) {
    const callbacks = [...frames.values()]; frames.clear();
    callbacks.forEach(fn => fn(i * 1000 / hz));
  }
  const visibleDraws = draws, visibleRotation = rotation;
  document.hidden = true;
  listeners.visibilitychange?.();
  const hiddenPending = frames.size;
  document.hidden = false;
  listeners.visibilitychange?.();
  for (const fn of [...frames.values()]) fn((seconds + 60) * 1000);
  return {visibleDraws, visibleRotation, hiddenPending, resumeRotation: rotation - visibleRotation};
}

const file = 'glass-widget/src/player.js', cover = 'glass-widget/src/cover3d.js';
assert.equal(pausedWrites(file), 0, 'unchanged paused UI must not repeat DOM writes');
const p = player(file);
p.context.lrc = '[00:01]one\n[00:03]first\n[00:03]last\n[00:05]end';
vm.runInContext('lyrics = parseLyrics(lrc)', p.context);
for (const [position, expected] of [[0,-1],[1000,0],[2999,0],[3000,2],[9999,3],[1000,0]]) {
  assert.equal(vm.runInContext(`lyricIndex(${position})`, p.context), expected);
}
vm.runInContext('lyrics = []', p.context);
assert.equal(vm.runInContext('lyricIndex(10)', p.context), -1);
// A long lyric file must require logarithmic comparisons, including a seek backwards.
let comparisons = 0;
p.context.longLyrics = Array.from({length: 16384}, (_, i) => ({get time() { comparisons++; return i * 1000; }}));
vm.runInContext('lyrics = longLyrics', p.context);
assert.equal(vm.runInContext('lyricIndex(16383000)', p.context), 16383);
assert.ok(comparisons <= 15);
for (const hz of [60, 120, 144]) {
  const result = animation(cover, hz);
  assert.ok(result.visibleDraws >= 100 && result.visibleDraws <= 102);
  assert.ok(Math.abs(result.visibleRotation + Math.PI * 2 / 3) < .001, 'frame cap must preserve rotation speed');
  assert.equal(result.hiddenPending, 0, 'hidden animation must have no queued frame');
  assert.equal(result.resumeRotation, 0, 'restoring visibility must not jump through hidden time');
}
console.log('lyric boundaries, bounded lookup, unchanged DOM and animation visibility passed');
if (process.argv[2]) {
  console.log(JSON.stringify({pausedDOMWritesPerMinute: {before: pausedWrites(process.argv[2]), after: pausedWrites(file)},
    drawCallsPer10Seconds: {before60Hz: animation(process.argv[3],60).visibleDraws, after60Hz: animation(cover,60).visibleDraws,
      before144Hz: animation(process.argv[3],144).visibleDraws, after144Hz: animation(cover,144).visibleDraws}}, null, 2));
}
