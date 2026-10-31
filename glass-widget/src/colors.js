(() => {
  const defaults = {
    glass: { color: '#081730', gradient: false, transparent: false },
    title: { color: '#f5eee8', gradient: false },
    artist: { color: '#f5eee8', gradient: false },
    lyric: { color: '#e8bb50', gradient: true }
  };
  const key = 'kugou-widget-colors-v1';
  let settings = structuredClone(defaults);
  try {
    const saved = JSON.parse(localStorage.getItem(key));
    for (const name of Object.keys(defaults)) {
      const item = saved?.[name];
      if (!item || !/^#[0-9a-f]{6}$/i.test(item.color) || typeof item.gradient !== 'boolean') continue;
      settings[name] = { ...defaults[name], color: item.color, gradient: item.gradient };
      if (name === 'glass') settings[name].transparent = item.transparent === true;
    }
  } catch (error) { console.warn('无法读取颜色设置', error); }

  const panel = document.querySelector('#color-panel');
  const glass = document.querySelector('.glass');
  const root = document.documentElement;
  let previousSize = 200;
  let editing = false;
  let switching = false;

  function paint(name) {
    const item = settings[name];
    if (name === 'glass') {
      if (item.transparent) return 'transparent';
      const rgb = item.color.match(/\w\w/g).map(value => parseInt(value, 16));
      const left = `rgba(${rgb.join(',')}, .72)`;
      const right = `rgba(${rgb.map(value => Math.round(value + (255 - value) * .55)).join(',')}, .72)`;
      return item.gradient ? `linear-gradient(to right, ${left}, ${right})` : left;
    }
    if (item.gradient) return `linear-gradient(to right, ${item.color}, #fff)`;
    if (name === 'artist' && item.color.toLowerCase() === defaults.artist.color) return 'rgba(245, 238, 232, .66)';
    return item.color;
  }

  function apply(save = true) {
    root.style.setProperty('--glass-tint', paint('glass'));
    for (const name of ['title', 'artist', 'lyric']) root.style.setProperty(`--${name}-paint`, paint(name));
    for (const row of panel.querySelectorAll('.color-row')) {
      const item = settings[row.dataset.target];
      const input = row.querySelector('input');
      input.value = item.color;
      input.parentElement.style.setProperty('--swatch', item.color);
      row.querySelector('.gradient-toggle').setAttribute('aria-pressed', String(item.gradient));
      const transparent = row.querySelector('.transparent-option');
      if (transparent) transparent.setAttribute('aria-pressed', String(item.transparent));
    }
    if (save) try { localStorage.setItem(key, JSON.stringify(settings)); } catch (error) { console.error('无法保存颜色设置', error); }
  }
  for (const row of panel.querySelectorAll('.color-row')) {
    const item = () => settings[row.dataset.target];
    row.querySelector('input').addEventListener('input', event => {
      item().color = event.target.value;
      if (row.dataset.target === 'glass') item().transparent = false;
      apply();
    });
    row.querySelector('.gradient-toggle').addEventListener('click', () => {
      item().gradient = !item().gradient;
      if (row.dataset.target === 'glass') item().transparent = false;
      apply();
    });
    row.querySelector('.transparent-option')?.addEventListener('click', () => { item().transparent = !item().transparent; apply(); });
  }
  document.querySelector('#color-reset').addEventListener('click', () => { settings = structuredClone(defaults); apply(); });

  async function setEditing(open) {
    if (switching || editing === open) return;
    switching = true;
    try {
      if (open) previousSize = innerWidth;
      if (window.__TAURI__) await window.__TAURI__.core.invoke('resize_square', { size: open ? 300 : previousSize });
      editing = open;
      glass.classList.toggle('editing', open);
      panel.hidden = !open;
      (open ? document.querySelector('#color-done') : document.querySelector('#cover')).focus();
    } catch (error) { console.error('无法调整颜色设置窗口', error); }
    finally { switching = false; }
  }
  window.addEventListener('open-color-panel', () => setEditing(true));
  document.querySelector('#color-done').addEventListener('click', () => setEditing(false));
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && editing) setEditing(false); });
  apply(false);
})();
