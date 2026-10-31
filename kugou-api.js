// Shared client for the local KuGou bridge. Browser use requires the same origin.
export function createKugouApi({baseUrl = '', fetchImpl = globalThis.fetch} = {}) {
  const base = baseUrl.replace(/\/$/, '');
  async function request(path, body) {
    const options = body === undefined
      ? {cache: 'no-store'}
      : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)};
    const response = await fetchImpl(base + path, options);
    if (!response.ok) {
      let message = `本地服务请求失败 (${response.status})`;
      try { message = (await response.json()).error || message; } catch (_) {}
      throw new Error(message);
    }
    return response.json();
  }
  return {
    getState: () => request('/api/state'),
    refresh: () => request('/api/refresh', {}),
    play: () => request('/api/control', {action: 'play'}),
    pause: () => request('/api/control', {action: 'pause'}),
  };
}

export function parseLyrics(lrc) {
  const entries = [];
  for (const line of (lrc || '').split(/\r?\n/)) {
    const stamps = [...line.matchAll(/\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]/g)];
    const content = line.replace(/\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]/g, '').trim();
    if (!content) continue;
    for (const stamp of stamps) {
      const fraction = Number((stamp[3] || '').padEnd(3, '0'));
      entries.push({time: (Number(stamp[1]) * 60 + Number(stamp[2])) * 1000 + fraction, text: content});
    }
  }
  return entries.sort((a, b) => a.time - b.time);
}

// Returns null for an unavailable or stale position; -1 denotes the intro.
export function currentLyricIndex(state, lines, now = Date.now()) {
  if (!state?.connected || state.position_ms == null || !state.position_sampled_at) return null;
  const age = now - state.position_sampled_at;
  if (age < 0 || age > 6000) return null;
  const position = state.position_ms + (state.song?.playback === 'playing' ? age : 0);
  let index = -1;
  for (let i = 0; i < lines.length && lines[i].time <= position; i++) index = i;
  return index;
}
