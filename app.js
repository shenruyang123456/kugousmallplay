import {createKugouApi, parseLyrics, currentLyricIndex} from './kugou-api.js';

const api = createKugouApi();
const $ = id => document.getElementById(id);
let current = null;
let busy = false;
let lastCover = '';
let lastLyrics = null;
let noticeUntil = 0;
let latestPoll = 0;
let lyricLines = [];
let lastLyricIndex = null;
const text = (id, value) => { $(id).textContent = value; };

function updateLyric() {
  if (!current?.connected) { text('lyricCurrent', '等待连接酷狗'); lastLyricIndex = null; return; }
  if (!lyricLines.length) { text('lyricCurrent', current.lyrics_status || '暂无同步歌词'); return; }
  const index = currentLyricIndex(current, lyricLines);
  if (index === null) {
    text('lyricCurrent', '等待播放进度'); lastLyricIndex = null; return;
  }
  if (index !== lastLyricIndex) {
    lastLyricIndex = index;
    text('lyricCurrent', index < 0 ? '前奏' : lyricLines[index].text);
  }
}

function display(data) {
  current = data;
  const song = data.song || {};
  const parts = (song.title || '').split(' - ');
  const title = parts.length > 1 ? parts.slice(1).join(' - ') : (song.title || '等待音乐');
  const artist = parts.length > 1 ? parts[0] : '连接酷狗后显示歌曲信息';
  text('title', title);
  text('artist', artist);
  $('connection').classList.toggle('online', !!data.connected);
  $('connection').lastChild.textContent = data.connected ? ' 酷狗已连接' : ' 连接中断';
  $('retry').hidden = !!data.connected;
  if (!busy && Date.now() >= noticeUntil) text('notice', data.connected ? '' : data.message || '无法连接酷狗');
  const playing = song.playback === 'playing';
  $('play').disabled = !data.connected || busy || data.control_busy;
  $('play').setAttribute('aria-label', playing ? '暂停' : '播放');
  $('play').setAttribute('title', playing ? '暂停' : '播放');
  text('playState', !data.connected ? '等待连接' : playing ? '播放中' : song.playback === 'stopped' ? '已停止' : '已暂停');
  $('playIcon').toggleAttribute('hidden', playing);
  $('pauseIcon').toggleAttribute('hidden', !playing);
  const lyricsKey = JSON.stringify([song.hash, data.lyrics, data.lyrics_status]);
  if (lyricsKey !== lastLyrics) {
    lastLyrics = lyricsKey;
    lyricLines = parseLyrics(data.lyrics);
    lastLyricIndex = null;
  }
  updateLyric();
  if ((data.cover || '') !== lastCover) {
    lastCover = data.cover || '';
    const image = $('coverImage');
    image.hidden = true;
    $('coverFallback').hidden = false;
    if (lastCover) {
      image.onload = () => { image.hidden = false; $('coverFallback').hidden = true; };
      image.onerror = () => { image.hidden = true; $('coverFallback').hidden = false; };
      image.src = lastCover;
    }
  }

}
async function poll() {
  if (document.hidden) return;
  const request = ++latestPoll;
  try { const data = await api.getState(); if (request === latestPoll) display(data); }
  catch { if (request === latestPoll) display({connected:false, message:'网页服务连接中断'}); }
}
async function runAction(action) {
  if (busy || current?.control_busy || !current?.connected) return;
  busy = true; $('play').disabled = true; text('notice', '正在操作酷狗…');
  try {
    await action();
    text('notice', '');
  } catch (error) { text('notice', error.message); noticeUntil = Date.now() + 4000; }
  finally { busy = false; await poll(); }
}
$('play').addEventListener('click', () => runAction(() => current?.song?.playback === 'playing' ? api.pause() : api.play()));
async function refreshConnection() {
  try { await api.refresh(); }
  finally { await poll(); }
}
$('retry').addEventListener('click', () => { text('notice', '正在重新读取酷狗…'); refreshConnection().catch(() => poll()); });
refreshConnection().catch(() => poll());
setInterval(poll, 1500); setInterval(updateLyric, 250);
document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
