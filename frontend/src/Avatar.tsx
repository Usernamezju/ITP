import { avatarUrl, type Account } from './accountApi';
import defaultAvatar from './assets/default-avatar.svg';

/**
 * The picture the account uploaded, or the built-in default one.
 *
 * Uploaded avatars live on the server under a random key; anything that fails
 * to load falls back to the same default the rest of the platform draws, so a
 * removed or rotated picture never leaves a broken image behind.
 */
export function AvatarImage({ user, className, alt, decorative = false }: {
  user: Pick<Account, 'display_name' | 'avatar_key'> | null | undefined;
  className?: string; alt?: string; decorative?: boolean;
}) {
  const uploaded = avatarUrl(user);
  return <img className={className} src={uploaded || defaultAvatar}
    alt={decorative ? '' : alt || `${user?.display_name || '用户'}的头像`}
    aria-hidden={decorative || undefined} loading="lazy"
    onError={(event) => { event.currentTarget.src = defaultAvatar; }} />;
}
