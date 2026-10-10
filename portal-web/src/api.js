// Cliente de la API del portal: cookie de sesión HttpOnly (la maneja el navegador) + cabecera CSRF en cada mutación.
let csrf = null
export const setCsrf = (t) => { csrf = t }

export async function api(path, { method = 'GET', body } = {}) {
  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET' && csrf) headers['X-CSRF-Token'] = csrf
  const r = await fetch(`/api${path}`, {
    method, headers, credentials: 'same-origin', body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (r.status === 401 && !path.startsWith('/auth/login')) window.dispatchEvent(new Event('cxc-sesion-expirada'))
  const ct = r.headers.get('content-type') || ''
  const data = ct.includes('json') ? await r.json().catch(() => ({})) : null
  if (!r.ok) {
    const e = new Error((data && data.error) || `HTTP ${r.status}`)
    e.status = r.status
    e.data = data
    throw e
  }
  return data
}

export const post = (path, body = {}) => api(path, { method: 'POST', body })
