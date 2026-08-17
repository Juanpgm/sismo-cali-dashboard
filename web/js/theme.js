// Light/dark theme controller. Default follows Colombia time-of-day (day =
// light, night = dark); an explicit user toggle is persisted and wins on reload.
// The initial <html data-theme> attribute is set by a tiny inline <head> script
// (no flash of the wrong theme); this module only wires the toggle and notifies
// listeners via a `themechange` CustomEvent on document.
const KEY = 'sismo-theme';

/** America/Bogota local hour 0-23 (UTC-5, no DST). */
export function bogotaHour() {
  const h = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/Bogota', hour: '2-digit', hourCycle: 'h23',
  }).format(new Date());
  return Number(h);
}

/** Time-of-day default: light 06:00–17:59 in Colombia, dark otherwise. */
export function defaultTheme() {
  const h = bogotaHour();
  return h >= 6 && h < 18 ? 'light' : 'dark';
}

export function currentTheme() {
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
}

function apply(theme) {
  document.documentElement.dataset.theme = theme;
  document.dispatchEvent(new CustomEvent('themechange', { detail: { theme } }));
}

/** Wire the header toggle button. Initial attribute already set inline. */
export function initTheme() {
  const btn = document.getElementById('theme-toggle');
  if (!btn) return;
  btn.addEventListener('click', () => {
    const next = currentTheme() === 'light' ? 'dark' : 'light';
    try { localStorage.setItem(KEY, next); } catch { /* storage blocked */ }
    apply(next);
  });
}
