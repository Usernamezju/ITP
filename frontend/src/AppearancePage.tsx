import { themes, type Theme } from './theme';

/** Browser-only preferences. Provider credentials belong to server operators. */
export function AppearancePage({ theme, onTheme }: {
  theme: Theme; onTheme: (value: Theme) => void;
}) {
  return <section id="account-appearance" className="settings-section theme-section account-theme-section" aria-label="外观设置" tabIndex={-1}>
      <div className="settings-section-title"><span>外观</span><div><h3>工作台主题</h3>
        <p>日间与夜间两种模式，修改会立即生效并保存在这台浏览器中。</p></div></div>
      <div className="theme-controls"><div><strong>显示模式</strong>
        <div className="theme-options" role="radiogroup" aria-label="显示模式">{themes.map((option) =>
          <button type="button" key={option.id} role="radio" aria-checked={theme === option.id}
            className={`theme-choice ${theme === option.id ? 'active' : ''}`} onClick={() => onTheme(option.id)}>
            <span className={`theme-swatch theme-swatch-${option.id}`} />
            <span><b>{option.label}</b><small>{option.description}</small></span></button>)}</div></div>
      </div>
  </section>;
}
