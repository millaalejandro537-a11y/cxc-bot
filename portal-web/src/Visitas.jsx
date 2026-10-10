import React, { useCallback, useEffect, useState } from 'react'
import { api, post } from './api.js'

const ESTADOS = [['prevista', 'Prevista'], ['visitada_firmada', 'Visitada y firmada'], ['visitada_sin_firma', 'Visitada sin firma'], ['no_visitada', 'No visitada']]
const hoy = () => new Date().toISOString().slice(0, 10)

// Registro de hojas de visita por salida. Falta de firma ≠ no visita: son estados distintos y la no visita exige motivo.
export function Visitas({ rutas }) {
  const [rid, setRid] = useState(rutas[0]?.route_id || '')
  const [fecha, setFecha] = useState(hoy())
  const [clientes, setClientes] = useState([])
  const [visitas, setVisitas] = useState([])
  const [cob, setCob] = useState(null)
  const [msg, setMsg] = useState(null)
  const [borr, setBorr] = useState({})

  const cargar = useCallback(async () => {
    if (!rid) return
    const [c, v, k] = await Promise.all([api(`/rutas/${rid}/clientes`), api(`/visitas?ruta=${rid}&fecha=${fecha}`), api(`/visitas/cobertura?ruta=${rid}&fecha=${fecha}`)])
    setClientes(c.clientes); setVisitas(v.visitas); setCob(k)
  }, [rid, fecha])
  useEffect(() => { cargar().catch((e) => setMsg(e.message)) }, [cargar])

  const guardar = async (codigo) => {
    const b = borr[codigo] || {}
    if (!b.estado) return
    setMsg(null)
    try {
      await post('/visitas', { ruta: rid, fecha_salida: fecha, codigo, estado: b.estado, motivo: b.motivo || '', observacion: b.observacion || '' })
      setBorr((x) => ({ ...x, [codigo]: {} }))
      await cargar()
    } catch (e) { setMsg(`${codigo}: ${e.message}`) }
  }
  const actual = (cod) => visitas.find((v) => v.codigo_cliente === cod)
  const set = (cod, k, v) => setBorr((x) => ({ ...x, [cod]: { ...x[cod], [k]: v } }))

  return (
    <>
      <h2>Hojas de visita</h2>
      <div className="filtros">
        <select value={rid} onChange={(e) => setRid(e.target.value)} aria-label="Ruta">{rutas.map((r) => <option key={r.route_id} value={r.route_id}>{r.nombre}</option>)}</select>
        <input type="date" value={fecha} onChange={(e) => setFecha(e.target.value)} aria-label="Fecha de salida" />
      </div>
      {cob && (
        <p className="muted">{cob.clientes_en_ruta} clientes · prevista {cob.prevista} · firmada {cob.visitada_firmada} · sin firma {cob.visitada_sin_firma} · no visitada {cob.no_visitada} · sin registro {cob.sin_registro}</p>
      )}
      <p className="muted small">La falta de firma no es evidencia de no visita. Para “no visitada” o “sin firma” indique el motivo. Cada cambio queda registrado con usuario y hora.</p>
      {msg && <p className="error">{msg}</p>}
      <div className="tablewrap"><table>
        <thead><tr><th>Folder</th><th>Cliente</th><th>Estado actual</th><th>Registrar</th></tr></thead>
        <tbody>{clientes.map((c) => {
          const a = actual(c.codigo)
          const b = borr[c.codigo] || {}
          return (
            <tr key={c.codigo}>
              <td><b>{c.folder}</b></td>
              <td>{c.nombre}<br /><small className="muted">{c.codigo}</small></td>
              <td>{a ? <>{ESTADOS.find(([k]) => k === a.estado)?.[1]}{a.motivo && <small className="muted"> — {a.motivo}</small>}<br /><small className="muted">{a.registrada_por_usuario}</small></> : <span className="muted">sin registro</span>}</td>
              <td className="reg">
                <select value={b.estado || ''} onChange={(e) => set(c.codigo, 'estado', e.target.value)} aria-label="Estado">
                  <option value="">—</option>{ESTADOS.map(([k, t]) => <option key={k} value={k}>{t}</option>)}
                </select>
                {['no_visitada', 'visitada_sin_firma'].includes(b.estado) && <input placeholder="Motivo" value={b.motivo || ''} onChange={(e) => set(c.codigo, 'motivo', e.target.value)} />}
                <button onClick={() => guardar(c.codigo)} disabled={!b.estado}>Guardar</button>
              </td>
            </tr>
          )
        })}</tbody>
      </table></div>
    </>
  )
}
