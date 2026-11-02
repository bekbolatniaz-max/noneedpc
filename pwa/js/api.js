// HTTP по contracts/openapi.yaml. Токен — только в localStorage и в заголовке, никогда в URL и логах.

const TOKEN_KEY = 'nitro.token';
// Копия в памяти: если localStorage недоступен (приватный режим), токен живёт до закрытия вкладки.
let memToken = null;

export function getToken() {
  if (memToken) return memToken;
  try { memToken = localStorage.getItem(TOKEN_KEY); } catch { memToken = null; }
  return memToken;
}

/** true — токен сохранён надолго; false — только до закрытия вкладки. */
export function setToken(token) {
  memToken = token;
  try { localStorage.setItem(TOKEN_KEY, token); return true; } catch { return false; }
}

export function clearToken() {
  memToken = null;
  try { localStorage.removeItem(TOKEN_KEY); } catch { /* хранилище недоступно — в памяти уже очищено */ }
}

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request(method, path, body, { auth = true, raw = false } = {}) {
  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (auth) {
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }
  const res = await fetch(`/api/v1${path}`, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store',
  });
  if (!res.ok) {
    let code = 'internal';
    let message = `HTTP ${res.status}`;
    try {
      const err = (await res.json()).error;
      code = err.code; message = err.message;
    } catch { /* не JSON */ }
    throw new ApiError(res.status, code, message);
  }
  return raw ? res : res.json();
}

export const api = {
  info: () => request('GET', '/info', undefined, { auth: false }),
  pair: (code, deviceName) => request('POST', '/pair', { code, device_name: deviceName }, { auth: false }),
  pairCode: () => request('GET', '/pair/code'),
  devices: () => request('GET', '/devices'),
  games: () => request('GET', '/games'),
  // <img src> не умеет заголовки, поэтому обложка идёт через fetch → blob URL.
  cover: async (appid) => URL.createObjectURL(await (await request('GET', `/games/${appid}/cover`, undefined, { raw: true })).blob()),
  launch: (appid) => request('POST', `/games/${appid}/launch`),
  consoleMode: () => request('GET', '/console-mode'),
  setConsoleMode: (on) => request('POST', '/console-mode', { state: on ? 'on' : 'off' }),
};
