import React, { useCallback, useEffect, useState } from 'react'
import { api, post } from './api.js'
import { fdate } from './ui.jsx'

// [fondo, texto]: texto blanco sobre los fondos oscuros (contraste >= 4.5:1 a 11 px); ROJO = solo contado (resultado del sistema, regla vigente)
const COLOR = {
  VERDE: ['#34a853', '#000000'], AMARILLA: ['#facc15', '#000000'], AZUL: ['#2563eb', '#ffffff'], BLANCA: ['#ffffff', '#000000'],
  CANDIDATO: ['#f4a6a6', '#000000'], REVISAR: ['#c4b5fd', '#000000'], ROJA: ['#dc2626', '#ffffff'], ROJO: ['#dc2626', '#ffffff'],
}

// Vista de IMPRESIÓN: reproduce el diseño aprobado (una página por hoja). Se imprime/guarda como PDF con el navegador (Windows, Mac, iPhone, Android).
export function HojasParaImprimir({ doc, ruta, vendedor, onCerrar }) {
  const c = doc.contenido
  return (
    <div className="print-root">
      <div className="no-print barra">
        <button onClick={() => window.print()}>Imprimir / guardar PDF</button> <button className="sec" onClick={onCerrar}>Cerrar</button>
        <p className="muted small">v{doc.version} · {doc.sha256.slice(0, 12)}… · colores preliminares: “CANDIDATO” no es una decisión de gerencia.</p>
      </div>
      {c.hojas.map((h) => (
        <section className="hoja" key={h.hoja}>
          <h4>DISTRIBUCIONES COSPROBELL</h4>
          <p className="sub">RUTA {ruta.toUpperCase()} · {vendedor.toUpperCase()} — {h.hoja.toUpperCase()}</p>
          <p className="campos">FECHA DE SALIDA: ____________ &nbsp; N.º DE SALIDA: ______ &nbsp; REVISÓ: ______________</p>
          <table>
            <thead><tr><th>COLOR</th><th>CÓDIGO SAP</th><th>SALÓN / CLIENTE</th><th>ENCARGADA / TELÉFONO</th><th>COBRO</th><th>VENTA (L)</th><th>OBSERVACIONES</th><th>FIRMA</th></tr></thead>
            <tbody>{h.clientes.map((x) => (
              <tr key={x.codigo}><td style={{ background: (COLOR[x.color_documento] || ['#ffffff'])[0], color: (COLOR[x.color_documento] || [0, '#000000'])[1] }}>{x.color_documento}</td><td>{x.codigo}</td><td>{x.nombre}</td><td /><td /><td /><td /><td /></tr>
            ))}</tbody>
          </table>
          <p className="campos pie">RESUMEN DE SALIDA: VISITADOS ___ &nbsp; NO VISITADOS ___ &nbsp; OBSERVACIONES GENERALES: ______________________</p>
          <p className="campos pie">FIRMA VENDEDOR: ____________ &nbsp; RECIBIDO POR: ____________</p>
        </section>
      ))}
    </div>
  )
}

export function Documentos({ rutas, user }) {
  const conHojas = rutas.filter((r) => r.requiere_hojas_visita)
  const [rid, setRid] = useState(conHojas[0]?.route_id || '')
  const [docs, setDocs] = useState([])
  const [ver, setVer] = useState(null)
  const [msg, setMsg] = useState(null)
  const [f, setF] = useState({ archivo: '', motivo: '', confirmar: false })
  const ruta = rutas.find((r) => r.route_id === rid)

  const cargar = useCallback(() => (rid ? api(`/rutas/${rid}/documentos`).then((d) => setDocs(d.documentos)).catch((e) => setMsg(e.message)) : null), [rid])
  useEffect(() => { cargar() }, [cargar])

  const abrir = async (id) => setVer(await api(`/documentos/${id}`))
  const registrar = async (e) => {
    e.preventDefault(); setMsg(null)
    try {
      const nueva = docs.length > 0
      const d = await post(`/admin/rutas/${rid}/documentos`, { archivo: f.archivo, motivo: f.motivo, nueva_version: nueva, confirmar: f.confirmar })
      setMsg(`Registrada la versión ${d.version} (${d.contenido.total_clientes} clientes, ${d.contenido.hojas.length} hojas).`); setF({ archivo: '', motivo: '', confirmar: false }); cargar()
    } catch (x) { setMsg(x.message) }
  }
  const proponer = async (id) => {
    try { const r = await post(`/admin/rutas/${rid}/documentos/${id}/proponer-clientes`); setMsg(`${r.propuestos.length} cliente(s) propuestos: confirme en Administración → Clientes por confirmar.`) } catch (x) { setMsg(x.message) }
  }
  const imprimir = async (d) => {
    try { await post('/impresion', { tipo: 'HOJA_VISITA', ruta: rid, documento_id: d.id, copias: 1 }); setMsg('Solicitud registrada en la cola de impresión (no se imprime nada automáticamente todavía).') } catch (x) { setMsg(x.message) }
  }

  if (ver) return <HojasParaImprimir doc={ver} ruta={ruta?.nombre || rid} vendedor={ruta?.vendedor || ''} onCerrar={() => setVer(null)} />
  if (!conHojas.length) return <p className="muted">Ninguna de sus rutas usa hojas de visita.</p>
  return (
    <>
      <h2>Documentos y hojas de visita</h2>
      <p className="muted small">Las hojas son documentos <b>reutilizables</b>: no se regeneran en cada salida. Solo cambian con una nueva versión confirmada (clientes, zonas, orden o diseño). Cada salida conserva la versión con que se creó.</p>
      <div className="filtros"><select value={rid} onChange={(e) => setRid(e.target.value)} aria-label="Ruta">{conHojas.map((r) => <option key={r.route_id} value={r.route_id}>{r.nombre} · {r.vendedor}</option>)}</select></div>
      {msg && <p className="aviso">{msg}</p>}
      {docs.length === 0 ? <p className="muted">Esta ruta aún no tiene documento de hojas de visita registrado.</p> : (
        <div className="tablewrap"><table>
          <thead><tr><th>Versión</th><th>Estado</th><th>Registrado</th><th>Hash</th><th>Salidas</th><th /></tr></thead>
          <tbody>{docs.map((d) => (
            <tr key={d.id}>
              <td><b>v{d.version}</b><br /><small className="muted">{d.nombre_original}</small></td>
              <td>{d.estado === 'VIGENTE' ? <span className="badge live">VIGENTE</span> : <span className="badge maqueta">HISTÓRICA</span>}</td>
              <td className="small">{fdate((d.creado_en || '').slice(0, 10))}<br />{d.creado_por}{d.motivo ? ` — ${d.motivo}` : ''}</td>
              <td className="small"><code>{d.sha256.slice(0, 12)}…</code></td>
              <td className="small">{d.salidas.length ? d.salidas.map((s) => `#${s}`).join(', ') : '—'}</td>
              <td className="reg">
                <button onClick={() => abrir(d.id)}>Ver / imprimir</button>
                <a className="btn sec" href={`/api/documentos/${d.id}/descargar`}>Descargar Excel</a>
                <button className="sec" onClick={() => imprimir(d)}>A cola de impresión</button>
                {user.rol !== 'vendedor' && d.estado === 'VIGENTE' && <button className="sec" onClick={() => proponer(d.id)}>Proponer clientes</button>}
              </td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {user.rol === 'admin' && (
        <form onSubmit={registrar} className="form">
          <h4>{docs.length ? 'Nueva versión (cambio de clientes, zonas, orden o diseño)' : 'Registrar el documento aprobado'}</h4>
          <input placeholder="Archivo .xlsx en 01_Rutas del servidor" value={f.archivo} onChange={(e) => setF({ ...f, archivo: e.target.value })} required />
          {docs.length > 0 && <>
            <textarea rows={2} placeholder="Motivo del cambio (obligatorio)" value={f.motivo} onChange={(e) => setF({ ...f, motivo: e.target.value })} required />
            <label className="check"><input type="checkbox" checked={f.confirmar} onChange={(e) => setF({ ...f, confirmar: e.target.checked })} /> Confirmo crear una nueva versión (la anterior queda como histórica y no se borra)</label>
          </>}
          <button type="submit">Registrar</button>
        </form>
      )}
    </>
  )
}
