/**
 * Two display modes, and nothing else: 日间模式 and 夜间模式.  Each value is a
 * token set in `styles.css` (`:root` and `:root[data-theme=night]`), so every
 * page — customer, shop and operator — follows the same switch without
 * knowing which mode is active.
 */
export type Theme = 'day' | 'night';

export const themes: { id: Theme; label: string; description: string }[] = [
  { id: 'day', label: '日间模式', description: '纯白背景 · 黑字 · 红色强调' },
  { id: 'night', label: '夜间模式', description: '纯黑背景 · 白字 · 红色强调' },
];

/**
 * The key an old build wrote its palette into.  It is kept — not renamed — so
 * a browser that still holds `forest`, `pink` or `tech` migrates silently.
 */
const THEME_KEY = 'itp-color-theme';
/** The contrast choice was removed with the multi-theme palette. */
const LEGACY_CONTRAST_KEY = 'itp-contrast-theme';

export function loadTheme(): Theme {
  try {
    const value = localStorage.getItem(THEME_KEY);
    if (value === 'night' || value === 'dark') return 'night';
  } catch { /* Storage may be unavailable in a private browser session. */ }
  // Everything else — 'day', an old palette name, a high-contrast flag or no
  // stored value at all — starts in the day mode.
  return 'day';
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(THEME_KEY, theme);
    localStorage.removeItem(LEGACY_CONTRAST_KEY);
  } catch { /* The current page can still use the selected mode. */ }
}

/** Put the saved mode back on a page that has no theme controls of its own. */
export function restoreTheme(): void {
  applyTheme(loadTheme());
}
