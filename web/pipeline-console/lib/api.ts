export type Value = Record<string, any>;
let csrf = '';
export const commandId = () => crypto.randomUUID().replaceAll('-', '');
export async function api(path: string, body?: Value) {
  const response = await fetch('/api' + path, {
    method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
    headers: body ? {'Content-Type':'application/json', 'X-Console-CSRF':csrf} : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error?.message || JSON.stringify(value.detail || value.error));
  return value;
}
export async function establishSession() {
  const key = new URLSearchParams(location.hash.slice(1)).get('launch');
  if (key) {
    history.replaceState(null, '', location.pathname + location.search);
    const response = await fetch('/api/bootstrap', {method:'POST',
      headers:{'X-Launch-Key':key, 'Content-Type':'application/json'}, body:'{}'});
    if (!response.ok) throw new Error('Launcher session expired. Reopen the console using its launcher.');
    csrf = (await response.json()).csrf;
  } else csrf = (await api('/session')).csrf;
}
export const display = (value: unknown) => value === null || value === undefined || value === '' ? '—' : String(value);
