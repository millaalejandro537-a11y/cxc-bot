import React, { useCallback, useEffect, useState } from 'react'
import { api, post } from './api.js'
import { fdate, money } from './ui.jsx'
import { ExcelSalida } from './Operacion.jsx'

const EST = { programada: 'Programada', preparada: 'Preparada', en_curso: 'En curso', cerrada: 'Cerrada', cancelada: 'Cancelada' }
const SEG = { pendiente: 'Pendiente', prevista: 'Prevista', visitada_firmada: 'Visitada y firmada', visitada_sin_firma: 'Visitada sin firma', no_visitada: 'No visitada' }

function Excepciones({ r }) {
  const bloques = [
    ['pendientes_gerencia', 'Pendientes de decisión de gerencia', (x) => `${x.folder} ${x.nombre} · ${x.calculado}`],
    ['alertas_cobro', 'Alertas de cobro (abiertas > 60 d)', (x) => `${x.folder} ${x.nombre} · factura ${x.factura} · ${x.mora} d · ${money(x.saldo_pendiente)}`],
    ['cambios_de_clasificacion', 'Cambios de clasificación desde la salida anterior', (x) => `${x.folder} ${x.nombre}: ${x.antes} → ${x.ahora}`],
    ['clientes_nuevos', 'Clientes nuevos', (x) => `${x.folder} ${x.nombre}`],
    ['clientes_retirados', 'Clientes retirados', (x) => `${x.folder} (${x.codigo})`],
    ['sin_datos', 'Sin datos en la fuente', (x) => `${x.folder} ${x.nombre}`],
    ['candidatos_pendientes', 'Clientes propuestos sin confirmar', (x) => `${x.codigo} ${x.nombre}`],
  ]
  return (
    <>
      <p className={r.requiere_revision ? 'aviso' : 'okmsg'}>{r.total_excepciones} excepción(es) a revisar · datos del corte {fdate(r.fecha_corte_datos)}</p>
      {(r.advertencias || []).map((a) => <p key={a} className="muted small">⚠ {a}</p>)}
      {bloques.filter(([k]) => (r[k] || []).length > 0).map(([k, t, fmt]) => (
        <details key={k} open><summary><b>{t}</b> ({r[k].length})</summary><ul className="notas">{r[k].map((x, i) => <li key={i}>{fmt(x)}</li>)}</ul></details>
      ))}
      {r.omisiones && (r.omisiones.omitidas_confirmadas.length + r.omisiones.sin_registro_en_salidas.length > 0) && (
        <details open><summary><b>Omisiones consecutivas</b></summary>
          <ul className="notas">
            {r.omisiones.omitidas_confirmadas.map((x) => <li key={x.codigo}>{x.folder} {x.nombre}: no visitado en {x.salidas.length} salidas seguidas (confirmado)</li>)}
            {r.omisiones.sin_registro_en_salidas.map((x) => <li key={x.codigo}>{x.folder} {x.nombre}: sin registro en alguna de las últimas salidas (revisar; falta de firma no es no visita)</li>)}
          </ul></details>
      )}
    </>
  )
}

export function Salidas({ rutas, user, abrirDocumentos }) {
  const [rid, setRid] = useState(rutas[0]?.route_id || '')
  const [lista, setLista] = useState([])
  const [sel, setSel] = useState(null)
  const [det, setDet] = useState(null)
  const [msg, setMsg] = useState(null)
  const [f, setF] = useState({ fecha_salida: new Date().toISOString().slice(0, 10), fecha_regreso: '', notas: '' })
  const [nota, setNota] = useState('')
  const gestiona = ['admin', 'gerencia'].includes(user.rol)
  const ruta = rutas.find((r) => r.route_id === rid)

  const cargar = useCallback(() => (rid ? api(`/salidas?ruta=${rid}`).then((d) => setLista(d.salidas)).catch((e) => setMsg(e.message)) : null), [rid])
  const cargarDet = useCallback(() => (sel ? api(`/salidas/${sel}`).then(setDet).catch((e) => setMsg(e.message)) : setDet(null)), [sel])
  useEffect(() => { cargar() }, [cargar])
  useEffect(() => { cargarDet() }, [cargarDet])

  const accion = async (fn, ok) => { setMsg(null); try { await fn(); if (ok) setMsg(ok); await cargar(); await cargarDet() } catch (e) { setMsg(e.message) } }
  const crear = (e) => { e.preventDefault(); accion(async () => { const s = await post('/salidas', { ruta: rid, ...f, fecha_regreso: f.fecha_regreso || undefined }); setSel(s.id) }, 'Salida creada.') }

  return (
    <>
      <h2>Salidas</h2>
      <div className="filtros"><select value={rid} onChange={(e) => { setRid(e.target.value); setSel(null) }} aria-label="Ruta">{rutas.map((r) => <option key={r.route_id} value={r.route_id}>{r.nombre} · {r.implantacion === 'OPERATIVA' ? 'operativa' : r.implantacion.replaceAll('_', ' ').toLowerCase()}</option>)}</select></div>
      {ruta && ruta.implantacion !== 'OPERATIVA' && <p className="aviso">La ruta aún no está operativa ({ruta.implantacion.replaceAll('_', ' ').toLowerCase()}): confirme sus clientes y márquela operativa antes de programar salidas.</p>}
      {msg && <p className="aviso">{msg}</p>}
      <div className="tablewrap"><table>
        <thead><tr><th>Fecha de salida</th><th>Estado</th><th>Revisión</th><th>Hojas de visita</th></tr></thead>
        <tbody>{lista.map((s) => (
          <tr key={s.id} className="click" onClick={() => setSel(s.id)}>
            <td><b>{fdate(s.fecha_salida)}</b>{s.fecha_regreso ? ` → ${fdate(s.fecha_regreso)}` : ''}</td><td>{EST[s.estado]}</td>
            <td className="small">{s.revisada_por ? `✓ ${s.revisada_por}` : s.reporte_excepciones ? 'pendiente de revisar' : '—'}</td>
            <td className="small">{ruta?.requiere_hojas_visita ? 'documento vinculado' : 'no aplica'}</td>
          </tr>))}</tbody>
      </table></div>
      {gestiona && ruta?.implantacion === 'OPERATIVA' && (
        <form onSubmit={crear} className="form"><h4>Programar salida</h4>
          <label>Fecha de salida<input type="date" value={f.fecha_salida} onChange={(e) => setF({ ...f, fecha_salida: e.target.value })} required /></label>
          <label>Fecha de regreso (opcional)<input type="date" value={f.fecha_regreso} onChange={(e) => setF({ ...f, fecha_regreso: e.target.value })} /></label>
          <input placeholder="Notas" value={f.notas} onChange={(e) => setF({ ...f, notas: e.target.value })} />
          <button type="submit">Crear salida</button></form>
      )}
      {det && det.salida && (
        <section className="card">
          <h3>Salida del {fdate(det.salida.fecha_salida)} · {EST[det.salida.estado]}</h3>
          {gestiona && (
            <div className="filtros">
              <button onClick={() => accion(() => post(`/salidas/${sel}/preparar`), 'Salida preparada: revise las excepciones.')}>Preparar / actualizar</button>
              {['en_curso', 'cerrada', 'cancelada'].map((e) => det.salida.estado !== e && <button key={e} className="sec" onClick={() => accion(() => post(`/salidas/${sel}/estado`, { estado: e }))}>Marcar {EST[e].toLowerCase()}</button>)}
            </div>
          )}
          {det.salida.reporte_excepciones && <Excepciones r={det.salida.reporte_excepciones} />}
          {gestiona && det.salida.reporte_excepciones && !det.salida.revisada_por && (
            <div className="form"><textarea rows={2} placeholder="Nota de revisión de las excepciones" value={nota} onChange={(e) => setNota(e.target.value)} />
              <button onClick={() => accion(() => post(`/salidas/${sel}/revisar`, { nota }), 'Revisión registrada.')}>Registrar revisión</button></div>
          )}
          {det.salida.revisada_por && <p className="okmsg small">Revisada por {det.salida.revisada_por}: {det.salida.revision_nota}</p>}
          {ruta?.requiere_hojas_visita && <p><button className="sec" onClick={abrirDocumentos}>Ver hoja de visita vinculada</button></p>}
          {det.salida.reporte_excepciones && <ExcelSalida salida={det.salida} ruta={rid} gestiona={gestiona} todas={lista} />}
          <h4>Seguimiento de visitas</h4>
          {ruta?.requiere_hojas_visita ? (
            <>
              <p className="muted small">{Object.entries(det.seguimiento.resumen).map(([k, v]) => `${SEG[k]} ${v}`).join(' · ')}</p>
              <div className="tablewrap"><table><thead><tr><th>Folder</th><th>Cliente</th><th>Estado</th><th>Cobro</th><th>Venta</th></tr></thead>
                <tbody>{det.seguimiento.clientes.map((c) => <tr key={c.codigo}><td>{c.folder}</td><td>{c.nombre}</td><td>{SEG[c.estado]}{c.motivo ? ` — ${c.motivo}` : ''}</td><td>{c.cobro != null ? money(c.cobro) : '—'}</td><td>{c.venta != null ? money(c.venta) : '—'}</td></tr>)}</tbody></table></div>
              {det.seguimiento.totales && <p className="muted small">Visitados {det.seguimiento.totales.visitados}/{det.seguimiento.totales.clientes} · cobro declarado {money(det.seguimiento.totales.cobro_declarado)} · venta declarada {money(det.seguimiento.totales.venta_declarada)} (montos de campo, no de SAP)</p>}
            </>
          ) : <p className="muted">Esta ruta no usa hojas de visita.</p>}
        </section>
      )}
    </>
  )
}
