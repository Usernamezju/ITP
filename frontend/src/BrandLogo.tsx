import brandMark from './assets/brand-mark.svg?raw';
import brandWordmark from './assets/brand-wordmark.svg?raw';

/**
 * The 衣想国 ClothiNation artwork lives in `assets/` as ordinary SVG files and
 * is drawn inline here.  Inlining is what lets one file serve both display
 * modes: the mark's ink is `currentColor`, so it follows `--brand-ink` into
 * the night ground, and the gold is `--brand-gold`.  An `<img>` would freeze
 * both colours and need a second copy of every file.
 */
const MARK = strip(brandMark);
const WORDMARK = strip(brandWordmark);

/** Drop the source file's leading comment; it is documentation, not drawing. */
function strip(source: string): string {
  return source.replace(/^<!--[\s\S]*?-->\s*/, '').trim();
}

/**
 * The mark on its own: the hanger, the garment, the crescent and the gold
 * orbit.  Decorative by default, because it usually sits next to the name it
 * stands for; pass `label` when it is the only thing naming the brand.
 */
export function BrandMark({ className, label }: { className?: string; label?: string }) {
  return <span className={className ? `brand-art ${className}` : 'brand-art'}
    aria-hidden={label ? undefined : true} role={label ? 'img' : undefined}
    aria-label={label}
    dangerouslySetInnerHTML={{ __html: MARK }} />;
}

/** The mark above the 衣想国 · clothination wordmark, for the sign-in card. */
export function BrandLockup({ className, label = '衣想国 ClothiNation' }: {
  className?: string; label?: string;
}) {
  return <span className={className ? `brand-art brand-lockup ${className}` : 'brand-art brand-lockup'}
    role="img" aria-label={label}>
    <span className="brand-lockup-mark" aria-hidden="true" dangerouslySetInnerHTML={{ __html: MARK }} />
    <span className="brand-lockup-word" aria-hidden="true" dangerouslySetInnerHTML={{ __html: WORDMARK }} />
  </span>;
}

/**
 * The brand's loading mark: the gold orbit sweeps around the planet while the
 * ink breathes, so a wait looks like the logo rather than a generic spinner.
 * The motion is dropped under `prefers-reduced-motion` (see styles.css).
 */
export function BrandSpinner({ size = 44, className, label = '正在加载' }: {
  size?: number; className?: string; label?: string;
}) {
  return <span className={className ? `brand-art brand-spinner ${className}` : 'brand-art brand-spinner'}
    style={{ width: size, height: size }} role="img" aria-label={label}
    dangerouslySetInnerHTML={{ __html: MARK }} />;
}
