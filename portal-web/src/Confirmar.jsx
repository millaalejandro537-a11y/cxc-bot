import React, { useCallback, useEffect, useState } from 'react'
import { api, post } from './api.js'

// Incorporación de clientes a una ruta con CONFIRMACIÓN HUMANA: se proponen (búsqueda, Excel o documento), una persona asigna zona y se
// confirma; el folder permanente (siguiente número de su zona, nunca reutilizado) se asigna al confirmar. No se inventan zonas.
export function ConfirmarClientes({ user }) {
  const [rutas, setRutas] = useState([])
  const [rid, setRid] = useState('')
  const [cands, setCands] = useState([])
  const [usados, setUsados] = useState({})
  const [sel, setSel] = useState({})
  const [zona, setZona] = useState('')
  const [nz, setNz] = useState({ prefijo: '', nombre: '' })
  const [bus, setBus] = useState({ palabras: '', res: [] })
  const [msg, setMsg] = useState(null)
  const ruta = rutas.find((r) => r.route_id === rid)

  const cargarRutas = useCallback(() => api('/admin/rutas').then((d) => { setRutas(d.rutas); setRid((x) => x || d.rutas[0]?.route_id || '') }), [])
  const cargar = useCallback(() => (rid ? api(`/admin/rutas/${rid}/candidatos?estado=propuesta`).then((d) => { setCands(d.candidatos); setUsados(d.folders_usados) }) : null), [rid])
  useEffect(() => { cargarRutas() }, [cargarRutas])
  useEffect(() => { cargar() }, [cargar])

  const seguro = async (fn, ok) => { setMsg(null); try { await fn(); if (ok) setMsg(ok); await cargarRutas(); await cargar() } catch (e) { setMsg(e.message) } }
  const agregarZona = (e) => { e.preventDefault(); seguro(() => post(`/admin/rutas/${rid}/zonas`, nz), `Zona ${nz.prefijo.toUpperCase()} agregada.`).then(() => setNz({ prefijo: '', nombre: '' })) }
  const confirmar = (ids) => seguro(async () => { for (const id of ids) await post(`/admin/rutas/${rid}/candidatos/${id}/confirmar`, { zona }) }, `${ids.length} cliente(s) confirmados en la zona ${zona}.`)
  const rechazar = (id) => { const m = window.prompt('Motivo del rechazo:'); if (m) seguro(() => post(`/admin/rutas/${rid}/candidatos/${id}/rechazar`, { motivo: m })) }
  const buscar = async (e) => {
    e.preventDefault(); setMsg(null)
    try { const d = await post(`/admin/rutas/${rid}/candidatos/buscar`, { palabras: bus.palabras.split(',').map((x) => x.trim()).filter(Boolean) }); setBus({ ...bus, res: d.sugerencias }) } catch (x) { setMsg(x.message) }
  }
  const proponer = (items) => seguro(() => post(`/admin/rutas/${rid}/candidatos`, { clientes: items, origen: 'busqueda' }), `${items.length} cliente(s) propuesto(s).`)
  const operativa = () => seguro(() => post(`/admin/rutas/${rid}/implantacion`, { estado: 'OPERATIVA' }), 'Ruta marcada operativa.')
  const ids = Object.keys(sel).filter((k) => sel[k]).map(Number)

  return (
    <>
      <h3>Clientes por confirmar</h3>
      <div className="filtros"><select value={rid} onChange={(e) => setRid(e.target.value)} aria-label="Ruta">{rutas.map((r) => <option key={r.route_id} value={r.route_id}>{r.nombre} · {r.implantacion.replaceAll('_', ' ').toLowerCase()} · {r.clientes.length} confirmados</option>)}</select></div>
      {msg && <p className="aviso">{msg}</p>}
      {ruta && (
        <>
          <p className="muted small">Zonas definidas: {ruta.zonas.length ? ruta.zonas.map((z) => `${z.prefijo} = ${z.nombre} (último folder ${usados[z.prefijo] || 0})`).join(' · ') : 'ninguna todavía'}</p>
          <form onSubmit={agregarZona} className="form"><h4>Definir zona (la define una persona)</h4>
            <input placeholder="Prefijo (1-4 letras, ej. SR)" value={nz.prefijo} onChange={(e) => setNz({ ...nz, prefijo: e.target.value })} required />
            <input placeholder="Nombre de la zona" value={nz.nombre} onChange={(e) => setNz({ ...nz, nombre: e.target.value })} required /><button type="submit">Agregar zona</button></form>
          <h4>Propuestos ({cands.length})</h4>
          {cands.length > 0 && (
            <div className="filtros">
              <select value={zona} onChange={(e) => setZona(e.target.value)} aria-label="Zona"><option value="">— zona para los marcados —</option>{ruta.zonas.map((z) => <option key={z.prefijo} value={z.prefijo}>{z.nombre} ({z.prefijo})</option>)}</select>
              <button disabled={!zona || !ids.length} onClick={() => confirmar(ids)}>Confirmar {ids.length || ''} marcado(s)</button>
              <button className="sec" onClick={() => setSel(Object.fromEntries(cands.map((c) => [c.id, true])))}>Marcar todos</button>
              <button className="sec" onClick={() => setSel({})}>Limpiar</button>
            </div>
          )}
          <div className="tablewrap"><table>
            <thead><tr><th /><th>Código</th><th>Cliente</th><th>Zona sugerida</th><th>Origen</th><th /></tr></thead>
            <tbody>{cands.map((c) => (
              <tr key={c.id}><td><input type="checkbox" checked={!!sel[c.id]} onChange={(e) => setSel({ ...sel, [c.id]: e.target.checked })} aria-label={`Marcar ${c.codigo}`} /></td>
                <td>{c.codigo}</td><td>{c.nombre}</td><td className="small">{c.zona_sugerida || '—'}</td><td className="small">{c.origen}</td><td><button className="sec" onClick={() => rechazar(c.id)}>Rechazar</button></td></tr>))}</tbody>
          </table></div>
          {user.rol === 'admin' && ruta.implantacion !== 'OPERATIVA' && cands.length === 0 && ruta.clientes.length > 0 && <p><button onClick={operativa}>Marcar ruta operativa</button></p>}
          <form onSubmit={buscar} className="form"><h4>Buscar clientes en el snapshot (solo sugiere)</h4>
            <input placeholder="Palabras separadas por coma (p. ej. nombre de la zona)" value={bus.palabras} onChange={(e) => setBus({ ...bus, palabras: e.target.value })} required /><button type="submit">Buscar</button></form>
          {bus.res.length > 0 && (
            <>
              <button className="sec" onClick={() => proponer(bus.res.filter((r) => !r.ya_en_ruta && !r.candidato).map((r) => ({ codigo: r.codigo, nombre: r.nombre })))}>Proponer los {bus.res.filter((r) => !r.ya_en_ruta && !r.candidato).length} disponibles</button>
              <div className="tablewrap"><table><thead><tr><th>Código</th><th>Cliente</th><th>Situación</th></tr></thead>
                <tbody>{bus.res.map((r) => <tr key={r.codigo}><td>{r.codigo}</td><td>{r.nombre}</td><td className="small">{r.ya_en_ruta ? `ya está en la ruta ${r.ya_en_ruta}` : r.candidato ? `candidato (${r.candidato})` : 'disponible'}</td></tr>)}</tbody></table></div>
            </>
          )}
        </>
      )}
    </>
  )
}
