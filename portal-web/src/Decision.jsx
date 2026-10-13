import React, { useState } from 'react'
import { post } from './api.js'
import { fdate } from './ui.jsx'

const FINALES = [['A', 'A — Excelente (verde)'], ['B', 'B — Bueno (azul)'], ['C', 'C — Regular (amarilla)'], ['SOLO_CONTADO', 'Solo contado (roja)'], ['EN_PRUEBA', 'En prueba (blanca)']]

// Decisiones/excepciones de gerencia: solo el rol gerencia puede registrarlas o anularlas (también se valida en la API).
export function DecisionPanel({ cliente, rol, recargar }) {
  const [final, setFinal] = useState('C')
  const [motivo, setMotivo] = useState('')
  const [msg, setMsg] = useState(null)
  const hist = cliente.historial_decisiones || []
  const vigente = hist.find((h) => h.estado === 'vigente')
  const sem = cliente.semaforo || {}
  const sistemaDetermina = !!sem.calculado && !['REVISION_HUMANA', 'SIN_DATOS', 'CANDIDATO_SOLO_CONTADO'].includes(sem.calculado)
  const puede = rol === 'gerencia' && !sistemaDetermina

  const enviar = async (e) => {
    e.preventDefault()
    setMsg(null)
    try {
      await post('/decisiones', { codigo: cliente.codigo, clasificacion_final: final, motivo })
      setMotivo('')
      recargar()
    } catch (err) { setMsg(err.message) }
  }
  const anular = async () => {
    const m = window.prompt('Motivo de la anulación (mínimo 10 caracteres):')
    if (!m) return
    try { await post('/decisiones/anular', { codigo: cliente.codigo, motivo: m }); recargar() } catch (err) { setMsg(err.message) }
  }

  if (!['gerencia', 'admin'].includes(rol)) return null
  return (
    <section>
      <h3>Decisiones de gerencia</h3>
      <p className="muted small">El cálculo automático nunca se altera: la decisión se guarda aparte con autor, motivo y fecha.
        {sistemaDetermina ? ' El sistema determina la categoría de este cliente y prevalece sobre decisiones anteriores; solo se pide decisión cuando falta evidencia o hay errores de datos.' : ''}</p>
      {vigente && (
        <div className="decision">
          <b>Vigente:</b> {vigente.clasificacion_final.replaceAll('_', ' ')} — {vigente.actor} · {fdate(vigente.fecha)}
          <p>{vigente.motivo}</p>
          {puede && <button className="sec" onClick={anular}>Anular decisión</button>}
        </div>
      )}
      {puede ? (
        <form onSubmit={enviar} className="form">
          <label>Clasificación final
            <select value={final} onChange={(e) => setFinal(e.target.value)}>{FINALES.map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
          </label>
          <label>Motivo (obligatorio)
            <textarea value={motivo} onChange={(e) => setMotivo(e.target.value)} rows={3} minLength={10} required placeholder="Fundamento de la decisión" />
          </label>
          <button type="submit">Registrar decisión</button>
          {msg && <p className="error">{msg}</p>}
        </form>
      ) : <p className="muted small">{rol === 'gerencia' ? 'No se requiere decisión: el sistema determina la categoría.' : 'Solo el rol Gerencia puede registrar decisiones.'}</p>}
      {hist.length > 0 && (
        <details><summary>Historial ({hist.length})</summary>
          <ul className="notas">{hist.map((h) => <li key={h.id}>{fdate(h.fecha)} · {h.clasificacion_final} · {h.estado} · {h.actor}: {h.motivo}{h.anulada_motivo ? ` (anulada: ${h.anulada_motivo})` : ''}</li>)}</ul>
        </details>
      )}
    </section>
  )
}
