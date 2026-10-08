import { useEffect, useState } from 'react';
import { avatarUrl, type Account } from './accountApi';
import defaultAvatar from './assets/default-avatar.svg';

/**
 * The picture the account uploaded, or the brand's own avatar.
 *
 * An account that never uploaded one — and everyone who is not signed in, and
 * the moment a picture is removed — shows the 衣想国 mark cropped into a disc.
 * A picture that fails to load falls back to the same disc instead of leaving
 * a broken image behind, and only one fallback is ever attempted, so a failing
 * default cannot loop.
 */
export function AvatarImage({ user, className, alt, decorative = false }: {
  user: Pick<Account, 'display_name' | 'avatar_key'> | null | undefined;
  className?: string; alt?: string; decorative?: boolean;
}) {
  const uploaded = avatarUrl(user);
  const [failed, setFailed] = useState(false);
  useEffect(() => { setFailed(false); }, [uploaded]);

  return <img className={className} src={uploaded && !failed ? uploaded : defaultAvatar}
    alt={decorative ? '' : alt || `${user?.display_name || '用户'}的头像`}
    aria-hidden={decorative || undefined} loading="lazy"
    onError={() => { if (uploaded && !failed) setFailed(true); }} />;
}

/** The brand avatar on its own, for the visitor who has no account yet. */
export function DefaultAvatar({ className, alt }: { className?: string; alt?: string }) {
  return <img className={className} src={defaultAvatar} alt={alt || ''}
    aria-hidden={alt ? undefined : true} />;
}
