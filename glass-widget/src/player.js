const invoke = window.__TAURI__?.core.invoke;
const $ = id => document.getElementById(id);
let current = null, busy = false, pollRunning = false, coverKey = '', coverGeneration = 0, coverRetryAt = 0;
let lyricSource = null, lyrics = [], notice = '', noticeUntil = 0;
let progressValue = null, pollTimer = null, tickTimer = null;

function setText(id, value, tooltip = false) {
  const element = $(id);
  if (element.textContent !== value) element.textContent = value;
  if (tooltip && element.title !== value) element.title = value;
}

function parseLyrics(lrc) {
  const lines = [];
  for (const line of (lrc || '').split(/\r?\n/)) {
    const text = line.replace(/\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]/g, '').trim();
    if (!text) continue;
    for (const stamp of line.matchAll(/\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]/g))
      lines.push({time: (Number(stamp[1]) * 60 + Number(stamp[2])) * 1000 + Number((stamp[3] || '').padEnd(3, '0')), text});
  }
  return lines.sort((a,b) => a.time-b.time);
}
function lyricIndex(position) {
  let low = 0, high = lyrics.length;
  while (low < high) {
    const mid = (low + high) >>> 1;
    if (lyrics[mid].time <= position) low = mid + 1;
    else high = mid;
  }
  return low - 1;
}
function position(state) {
  if (!state?.connected || state.position_ms == null || !state.position_sampled_at) return null;
  const age = Date.now() - state.position_sampled_at;
  if (age < 0 || age > 6000) return null;
  return Math.min(state.song?.duration_ms || Infinity, state.position_ms + (state.song?.playback === 'playing' ? age : 0));
}
function time(ms) { if (ms == null || !Number.isFinite(ms)) return '--:--'; const sec = Math.floor(ms / 1000); return `${String(Math.floor(sec / 60)).padStart(2,'0')}:${String(sec % 60).padStart(2,'0')}`; }
function tick() {
  if (document.hidden) return;
  const pos = position(current), duration = current?.song?.duration_ms || null;
  setText('elapsed', time(pos)); setText('duration', time(duration));
  const value = pos != null && duration ? Math.min(1000, 1000 * pos / duration) : 0;
  if (value !== progressValue) {
    progressValue = value;
    $('progress').value = value;
    $('progress').style.background = `linear-gradient(to right, #e8d2bc ${$('progress').value / 10}%, rgba(255,255,255,.2) 0)`;
  }
  let line = current?.connected ? (current.lyrics_status || '暂无同步歌词') : (current?.message || '正在连接酷狗');
  if (current?.connected && lyrics.length) {
    if (pos == null) line = '等待播放进度';
    else { const index = lyricIndex(pos); line = index < 0 ? '前奏' : lyrics[index].text; }
  }
  if (Date.now() < noticeUntil) line = notice;
  setText('lyric', line, true);
}
async function showCover(data) {
  const key = `${data.song?.hash || ''}|${data.cover || ''}`;
  if (key === coverKey && Date.now() < coverRetryAt) return;
  if (key === coverKey && coverRetryAt === Infinity) return;
  coverKey = key; coverRetryAt = Infinity; const generation = ++coverGeneration;
  if (!window.cover3D) await new Promise(resolve => window.addEventListener('cover3d-ready', resolve, {once:true}));
  if (generation !== coverGeneration) return;
  await window.cover3D.setCover('');
  if (!data.cover || !data.song?.hash) return;
  try {
    const image = await invoke('bridge_request', {operation:'cover'});
    if (generation !== coverGeneration || image.hash !== data.song.hash || image.url !== data.cover) return;
    await window.cover3D.setCover(image.data);
  } catch (e) { if (generation === coverGeneration) coverRetryAt = Date.now() + 30000; console.error('封面加载失败',e); }
}
function display(data) {
  current = data; const song = data.song || {}, parts = (song.title || '').split(' - ');
  setText('song-title', parts.length > 1 ? parts.slice(1).join(' - ') : (song.title || '等待音乐'), true);
  setText('song-artist', parts.length > 1 ? parts[0] : (data.connected ? '未知歌手' : '正在连接酷狗'), true);
  $('play-toggle').disabled = !data.connected || busy || data.control_busy;
  $('previous').disabled = $('next').disabled = !data.connected || busy || data.control_busy;
  window.glassPlayer.setPlaying(data.connected && song.playback === 'playing');
  if (data.lyrics !== lyricSource) { lyricSource = data.lyrics; lyrics = parseLyrics(data.lyrics); }
  showCover(data).catch(console.error); tick();
}
async function poll() {
  if (pollRunning || document.hidden || !invoke) return; pollRunning = true;
  try { display(await invoke('bridge_request', {operation:'state'})); }
  catch (e) { display({connected:false, message:String(e)}); }
  finally { pollRunning = false; }
}
async function control(operation) {
  if (busy || current?.control_busy || !current?.connected) return;
  busy = true;
  for (const id of ['play-toggle', 'previous', 'next']) $(id).disabled = true;
  try { await invoke('bridge_request', {operation}); }
  catch (error) { notice = `操作未确认：${error}`; noticeUntil = Date.now() + 5000; tick(); }
  finally {
    busy = false;
    await poll();
    setTimeout(poll, 700);
  }
}
window.addEventListener('glass-player-change', event => {
  if (event.detail.source === 'button') control(event.detail.playing ? 'play' : 'pause');
});
$('previous').addEventListener('click', () => control('prev'));
$('next').addEventListener('click', () => control('next'));
function updateVisibility() {
  clearInterval(pollTimer); clearInterval(tickTimer);
  pollTimer = tickTimer = null;
  if (document.hidden || !invoke) return;
  poll(); tick();
  pollTimer = setInterval(poll,1500);
  tickTimer = setInterval(tick,250);
}
document.addEventListener('visibilitychange', updateVisibility);
if (invoke) updateVisibility();
else display({connected:false, message:'请在桌面组件中打开'});
