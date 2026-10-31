const glass = document.querySelector('.glass');
const close = document.querySelector('#close');
const playToggle = document.querySelector('#play-toggle');
let playing = false;
let dragStart = null;

if (window.__TAURI__) {
  const corners = ['--light-tl', '--light-tr', '--light-bl', '--light-br'];
  window.__TAURI__.event.listen('glass-corner-light', ({ payload }) => {
    const [index, ...rgb] = payload;
    if (!corners[index] || rgb.length !== 3 || !rgb.every(Number.isFinite)) return;
    const peak = Math.max(...rgb, 0);
    const gain = peak > 5 ? 255 / peak : 0;
    glass.style.setProperty(corners[index], rgb.map(value => Math.round(Math.min(255, Math.max(0, value * gain)))).join(' '));
  }).then(() => window.__TAURI__.core.invoke('refresh_corner_lights'))
    .catch(error => console.error('角部感光初始化失败', error));
}

function setPlaying(value, source = 'api') {
  if (typeof value !== 'boolean') throw new TypeError('播放状态必须是布尔值');
  const changed = playing !== value;
  playing = value;
  playToggle.setAttribute('aria-pressed', String(playing));
  playToggle.setAttribute('aria-label', playing ? '暂停' : '播放');
  if (changed) window.dispatchEvent(new CustomEvent('glass-player-change', { detail: { playing, source } }));
  return playing;
}

function togglePlaying(source = 'api') { return setPlaying(!playing, source); }
window.glassPlayer = Object.freeze({ getPlaying: () => playing, setPlaying, togglePlaying });
playToggle.addEventListener('click', () => window.dispatchEvent(new CustomEvent('glass-player-change', { detail: { playing: !playing, source: 'button' } })));
playToggle.disabled = true;

glass.addEventListener('mousedown', (event) => {
  if (event.button !== 0 || event.target !== glass) return;
  event.preventDefault();
  dragStart = { x: event.screenX, y: event.screenY };
});

window.addEventListener('mousemove', (event) => {
  if (!dragStart) return;
  if (!(event.buttons & 1)) { dragStart = null; return; }
  if (Math.hypot(event.screenX - dragStart.x, event.screenY - dragStart.y) < 4) return;
  dragStart = null;
  if (window.__TAURI__) window.__TAURI__.window.getCurrentWindow().startDragging().catch(console.error);
});

window.addEventListener('mouseup', () => { dragStart = null; });
window.addEventListener('blur', () => { dragStart = null; });

close.addEventListener('click', () => {
  if (window.__TAURI__) window.__TAURI__.window.getCurrentWindow().close().catch(console.error);
});

const resizeHandle = document.querySelector('#resize');
let resizing = null;
resizeHandle.addEventListener('pointerdown', event => {
  if (event.button !== 0 || !window.__TAURI__) return;
  event.preventDefault();
  resizing = { id: event.pointerId, x: event.screenX, y: event.screenY, size: innerWidth };
  resizeHandle.setPointerCapture(event.pointerId);
});
resizeHandle.addEventListener('pointermove', event => {
  if (!resizing || resizing.id !== event.pointerId) return;
  const delta = Math.max(event.screenX - resizing.x, event.screenY - resizing.y);
  window.__TAURI__.core.invoke('resize_square', { size: resizing.size + delta / (window.devicePixelRatio || 1) }).catch(console.error);
});
for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) resizeHandle.addEventListener(type, () => { resizing = null; });
