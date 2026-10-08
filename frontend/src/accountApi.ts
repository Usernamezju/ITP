import { useEffect, useState } from 'react';
import { api, ApiError } from './api';
import { SESSION_EVENT, sessionToken } from './session';

export type Account = {
  id: string; name: string; display_name: string; contact: string;
  role: 'customer' | 'merchant' | 'admin'; created: number;
  /** Unguessable file key of the uploaded avatar, or null for the default. */
  avatar_key?: string | null;
  phone?: string | null;
  phone_verified?: boolean;
  sms_available?: boolean;
};

/** The uploaded picture's URL, or null when the account uses the default one. */
export function avatarUrl(user: Pick<Account, 'avatar_key'> | null | undefined): string | null {
  return user?.avatar_key ? `/api/avatars/${user.avatar_key}` : null;
}

/** Avatar pictures travel as multipart; a JSON body would break the file upload. */
export function uploadAvatar(file: File): Promise<Account> {
  const body = new FormData();
  body.append('file', file);
  return api<Account>('/api/account/avatar', { method: 'POST', body });
}

export function removeAvatar(): Promise<Account> {
  return accountApi<Account>('/api/account/avatar', 'DELETE');
}

export function accountApi<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const token = sessionToken.read();
  return api<T>(path, { method, headers: {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
  }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
}

export async function logoutAccount(): Promise<void> {
  if (sessionToken.read()) {
    try { await accountApi('/api/auth/logout', 'POST', {}); }
    catch (error) {
      // Already-expired sessions have no usable authority to revoke.
      if (!(error instanceof ApiError) || error.status !== 401) throw error;
    }
  }
  sessionToken.write('');
}

export function useAccountSession() {
  const [user, setUser] = useState<Account | null>(null);
  const [checking, setChecking] = useState(Boolean(sessionToken.read()));
  useEffect(() => {
    let mounted = true;
    async function refresh() {
      const token = sessionToken.read();
      if (!token) { setUser(null); setChecking(false); return; }
      setChecking(true);
      try {
        const account = await accountApi<Account>('/api/account/me');
        if (mounted && token === sessionToken.read()) setUser(account);
      } catch (error) {
        if (mounted && token === sessionToken.read()) {
          setUser(null);
          if (error instanceof ApiError && error.status === 401) sessionToken.write('');
        }
      } finally {
        if (mounted && token === sessionToken.read()) setChecking(false);
      }
    }
    void refresh();
    const onSession = () => { void refresh(); };
    window.addEventListener(SESSION_EVENT, onSession);
    window.addEventListener('storage', onSession);
    return () => {
      mounted = false;
      window.removeEventListener(SESSION_EVENT, onSession);
      window.removeEventListener('storage', onSession);
    };
  }, []);
  return { user, checking, refresh: () => window.dispatchEvent(new Event(SESSION_EVENT)) };
}
