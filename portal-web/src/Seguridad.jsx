import React, { useState } from 'react'
import { api, post } from './api.js'

// Activación del segundo factor (TOTP) y entrega ÚNICA de los códigos de recuperación.
export function Seguridad({ user, onCambio, obligatorio = false }) {
  const [paso, setPaso] = useState(user.totp_activo ? 'activo' : 'inicio')
  const [datos, setDatos] = useState(null)
  const [codigo, setCodigo] = useState('')
  const [recuperacion, setRecuperacion] = useState(null)
  const [err, setErr] = useState(null)

  const iniciar = async () => {
    setErr(null)
    try { setDatos(await post('/auth/2fa/iniciar')); setPaso('confirmar') } catch (e) { setErr(e.message) }
  }
  const activar = async (e) => {
    e.preventDefault(); setErr(null)
    try { const r = await post('/auth/2fa/activar', { codigo }); setRecuperacion(r.codigos_recuperacion); setPaso('recuperacion') } catch (x) { setErr(x.message) }
  }
  const copiar = () => navigator.clipboard?.writeText((recuperacion || []).join('\n'))

  return (
    <div className={obligatorio ? 'login' : ''}>
      <h2>Seguridad de la cuenta</h2>
      {obligatorio && <p className="aviso">Su rol exige <b>segundo factor de autenticación</b>. Actívelo para continuar.</p>}
      {paso === 'activo' && <p>✓ El segundo factor está <b>activo</b>. Si pierde su dispositivo use un código de recuperación al iniciar sesión y pida a un administrador restablecerlo.</p>}
      {paso === 'inicio' && (
        <>
          <p className="muted">Necesita una app de autenticación (Google Authenticator, Microsoft Authenticator, Authy, 1Password…).</p>
          <button onClick={iniciar}>Comenzar</button>
        </>
      )}
      {paso === 'confirmar' && datos && (
        <form onSubmit={activar} className="form">
          <p>1. En su app agregue una cuenta <b>manualmente</b> con esta clave (o abra el enlace desde el móvil):</p>
          <code className="clave">{datos.secreto}</code>
          <p className="small"><a href={datos.uri}>Abrir en la app de autenticación</a></p>
          <p>2. Escriba el código de 6 dígitos que muestra la app:</p>
          <input inputMode="numeric" autoComplete="one-time-code" maxLength={7} value={codigo} onChange={(e) => setCodigo(e.target.value)} required />
          <button type="submit">Activar</button>
        </form>
      )}
      {paso === 'recuperacion' && recuperacion && (
        <div className="decision">
          <p><b>Códigos de recuperación.</b> Guárdelos ahora en un lugar seguro: cada uno sirve <b>una sola vez</b> y no se volverán a mostrar.</p>
          <pre className="rec">{recuperacion.join('\n')}</pre>
          <button onClick={copiar}>Copiar</button>{' '}
          <button className="sec" onClick={() => { setRecuperacion(null); setPaso('activo'); onCambio && onCambio() }}>Ya los guardé</button>
        </div>
      )}
      {err && <p className="error">{err}</p>}
    </div>
  )
}
