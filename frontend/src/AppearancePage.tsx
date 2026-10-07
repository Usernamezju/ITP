import { colorThemes, type ColorTheme, type ContrastTheme } from './theme';

/** Browser-only preferences. Provider credentials belong to server operators. */
export function AppearancePage({ colorTheme, contrastTheme, onColorTheme, onContrastTheme }: {
  colorTheme: ColorTheme; contrastTheme: ContrastTheme;
  onColorTheme: (value: ColorTheme) => void; onContrastTheme: (value: ContrastTheme) => void;
}) {
  return <section id="account-appearance" className="settings-section theme-section account-theme-section" aria-label="外观设置" tabIndex={-1}>
      <div className="settings-section-title"><span>外观</span><div><h3>工作台主题</h3>
        <p>选择适合你的配色与对比度，修改会立即生效并保存在浏览器中。</p></div></div>
      <div className="theme-controls"><div><strong>配色</strong>
        <div className="theme-options" role="radiogroup" aria-label="配色主题">{colorThemes.map((option) =>
          <button type="button" key={option.id} role="radio" aria-checked={colorTheme === option.id}
            className={`theme-choice ${colorTheme === option.id ? 'active' : ''}`} onClick={() => onColorTheme(option.id)}>
            <span className={`theme-swatch theme-swatch-${option.id}`} />
            <span><b>{option.label}</b><small>{option.description}</small></span></button>)}</div></div>
        <div><strong>对比度</strong><div className="contrast-options" role="radiogroup" aria-label="对比度主题">
          <button type="button" role="radio" aria-checked={contrastTheme === 'standard'} className={contrastTheme === 'standard' ? 'active' : ''}
            onClick={() => onContrastTheme('standard')}>标准对比度</button>
          <button type="button" role="radio" aria-checked={contrastTheme === 'high'} className={contrastTheme === 'high' ? 'active' : ''}
            onClick={() => onContrastTheme('high')}>高对比度</button>
        </div></div></div>
  </section>;
}
