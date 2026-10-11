import React, { useCallback, useEffect, useState } from 'react'
import { api, post } from './api.js'

const EST = { pendiente: 'Pendiente', tomado: 'Tomado por el agente', impreso: 'Impreso', error: 'Error', cancelado: 'Cancelado' }

// Cola de impresión: SOLO registro de solicitudes. No hay agente conectado ni se imprime nada automáticamente (fase posterior).
export function Impresion({ user }) {
  const [filas, setFilas] = useState([])
  const [nota, setNota] = useState('')
  const [msg, setMsg] = useState(null)
  const cargar = useCallback(() => api('/impresion').then((d) => { setFilas(d.solicitudes); setNota(d.nota) }).catch((e) => setMsg(e.message)), [])
  useEffect(() => { cargar() }, [cargar])
  const cancelar = async (id) => { try { await post(`/impresion/${id}/cancelar`); cargar() } catch (e) { setMsg(e.message) } }
  return (
    <>
      <h2>Cola de impresión</h2>
      <p className="aviso">{nota}</p>
      <p className="muted small">Desde las vistas de ruta y de documentos se agregan Excel de ruta y hojas de visita. Cuando exista un agente de impresión autorizado en la empresa tomará los pendientes; el historial queda aquí.</p>
      {msg && <p className="error">{msg}</p>}
      <div className="tablewrap"><table>
        <thead><tr><th>#</th><th>Tipo</th><th>Ruta</th><th>Detalle</th><th>Copias</th><th>Estado</th><th>Solicitó</th><th /></tr></thead>
        <tbody>{filas.map((f) => (
          <tr key={f.id}><td>{f.id}</td><td>{f.tipo.replaceAll('_', ' ')}</td><td>{f.route_id}</td><td className="small">{f.referencia || (f.documento_id ? `documento #${f.documento_id}` : '')}</td><td>{f.copias}</td>
            <td>{EST[f.estado]}{f.detalle ? <small className="muted"> — {f.detalle}</small> : ''}</td><td className="small">{f.solicitado_por}<br />{(f.solicitado_en || '').slice(0, 16).replace('T', ' ')}</td>
            <td>{['admin', 'gerencia'].includes(user.rol) && f.estado === 'pendiente' && <button className="sec" onClick={() => cancelar(f.id)}>Cancelar</button>}</td></tr>))}
          {filas.length === 0 && <tr><td colSpan="8" className="muted">Sin solicitudes.</td></tr>}</tbody>
      </table></div>
    </>
  )
}
