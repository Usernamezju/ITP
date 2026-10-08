import { useEffect, useState } from 'react';
import type { GarmentImage } from './merchantApi';

/** The colour a garment falls back to when it has no picture left to show. */
export const COVER_SWATCH = '#cccccc';

/**
 * The first picture in a gallery this browser has not already failed to load,
 * starting where the caller asked and wrapping once.
 *
 * This is the step that keeps one dead file from standing in for the product:
 * a gallery whose first picture 404s still shows its second, instead of
 * dropping straight to the colour sketch.
 */
export function workingImage(urls: string[], preferred: number, broken: string[]): string | undefined {
  if (!urls.length) return undefined;
  const start = ((preferred % urls.length) + urls.length) % urls.length;
  for (let step = 0; step < urls.length; step += 1) {
    const candidate = urls[(start + step) % urls.length];
    if (!broken.includes(candidate)) return candidate;
  }
  return undefined;
}

/** Forget which pictures failed when the gallery itself is replaced. */
export function useBrokenImages(urls: string[]): [string[], (url: string) => void] {
  const [broken, setBroken] = useState<string[]>([]);
  const identity = urls.join('\u0000');
  useEffect(() => { setBroken([]); }, [identity]);
  return [broken, (url: string) => setBroken((list) => (list.includes(url) ? list : [...list, url]))];
}

/**
 * The one place a shop's product picture is chosen, so every card agrees on the
 * order and on what happens when a picture will not load.
 *
 * The order is: the shop's own first picture, then the next one, and so on,
 * down to the colour the garment carries.  A picture the server has already
 * said it cannot serve is skipped without a request; one that fails in the
 * browser moves the chain along.  Either way a single dead file costs the card
 * one picture, never the product.
 *
 * It renders the picture or the swatch directly, with no wrapper, because the
 * thumbnails are sized by the class the caller puts on it.
 */
export function GarmentCover({ images, alt, color, className }: {
  images?: GarmentImage[]; alt?: string; color?: string | null; className?: string;
}) {
  const usable = (images || []).filter((image) => image.available !== false);
  const [index, setIndex] = useState(0);
  // A different product reuses this component: start its chain from the top.
  const identity = usable.map((image) => image.id).join(',');
  useEffect(() => { setIndex(0); }, [identity]);

  const current = usable[index];
  if (!current) {
    return <i className={className} style={{ background: color || COVER_SWATCH }} />;
  }
  return <img className={className} src={current.url} alt={alt || ''} loading="lazy"
    onError={() => setIndex((value) => value + 1)} />;
}
