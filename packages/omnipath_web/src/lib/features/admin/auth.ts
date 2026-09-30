const STORAGE_KEY = 'omnipath-admin-password';

export function getAdminPassword(): string {
  if (typeof localStorage === 'undefined') return '';
  const local = localStorage.getItem(STORAGE_KEY);
  if (local) return local;
  if (typeof sessionStorage !== 'undefined') {
    const session = sessionStorage.getItem(STORAGE_KEY);
    if (session) {
      localStorage.setItem(STORAGE_KEY, session);
      sessionStorage.removeItem(STORAGE_KEY);
      return session;
    }
  }
  return '';
}

export function setAdminPassword(value: string): void {
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(STORAGE_KEY, value);
  }
  if (typeof sessionStorage !== 'undefined') {
    sessionStorage.removeItem(STORAGE_KEY);
  }
}

export function clearAdminPassword(): void {
  if (typeof localStorage !== 'undefined') {
    localStorage.removeItem(STORAGE_KEY);
  }
  if (typeof sessionStorage !== 'undefined') {
    sessionStorage.removeItem(STORAGE_KEY);
  }
}

export function adminHeaders(init?: HeadersInit): Headers {
  const headers = new Headers(init);
  const password = getAdminPassword();
  if (password) headers.set('x-admin-secret', password);
  return headers;
}

export function adminFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  return fetch(input, { ...init, headers: adminHeaders(init?.headers) });
}
