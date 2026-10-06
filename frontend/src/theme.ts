export type ColorTheme = 'commerce' | 'forest' | 'ocean' | 'sunset' | 'violet' | 'tech' | 'pink';
export type ContrastTheme = 'standard' | 'high';

export const colorThemes: { id: ColorTheme; label: string; description: string }[] = [
  { id: 'commerce', label: '电商橙', description: '简洁明亮 · 默认' },
  { id: 'forest', label: '森林绿', description: '自然沉静' },
  { id: 'ocean', label: '海洋蓝', description: '清爽专注' },
  { id: 'sunset', label: '暖日橙', description: '温暖明亮' },
  { id: 'violet', label: '暮光紫', description: '柔和灵感' },
  { id: 'tech', label: '科技风', description: '深蓝电青' },
  { id: 'pink', label: '少女粉', description: '甜美柔粉' },
];

export function loadColorTheme(): ColorTheme {
  try {
    const value = localStorage.getItem('itp-color-theme');
    if (colorThemes.some((item) => item.id === value)) return value as ColorTheme;
  } catch { /* Storage may be unavailable in a private browser session. */ }
  return 'commerce';
}

export function loadContrastTheme(): ContrastTheme {
  try { return localStorage.getItem('itp-contrast-theme') === 'high' ? 'high' : 'standard'; }
  catch { return 'standard'; }
}

/** Put the saved theme back on a page that has no theme controls of its own. */
export function restoreTheme(): void {
  document.documentElement.dataset.theme = loadColorTheme();
  document.documentElement.dataset.contrast = loadContrastTheme();
}

export function applyTheme(color: ColorTheme, contrast: ContrastTheme): void {
  document.documentElement.dataset.theme = color;
  document.documentElement.dataset.contrast = contrast;
  try {
    localStorage.setItem('itp-color-theme', color);
    localStorage.setItem('itp-contrast-theme', contrast);
  } catch { /* The current page can still use the selected theme. */ }
}
