/** One user session, shared with the legacy merchant console. Not a provider key. */
const TOKEN_KEY = 'itp.merchant.token';
export const SESSION_EVENT = 'itp:session-change';
let memoryToken = '';

export const sessionToken = {
  read(): string {
    try { return localStorage.getItem(TOKEN_KEY) || memoryToken; }
    catch { return memoryToken; }
  },
  write(token: string): void {
    memoryToken = token;
    try {
      if (token) localStorage.setItem(TOKEN_KEY, token);
      else localStorage.removeItem(TOKEN_KEY);
    } catch { /* The current session still works without browser persistence. */ }
    window.dispatchEvent(new Event(SESSION_EVENT));
  },
};
