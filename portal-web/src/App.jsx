import React, { useEffect, useMemo, useState } from 'react'

const api = async (path) => {
  const r = await fetch(`/api${path}`)
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`)
  return r.json()
}

const money = (n) => `L ${Number(n || 0).toLocaleString('es-HN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
const fdate = (s) => (s ? s.split('-').reverse().join('/') : '—')

// Color de la viñeta por clasificación final (el cálculo lo hace el backend; aquí solo se presenta)
const DOT = {
  VERDE: 'var(--verde)', AZUL: 'var(--azul)', AMARILLO: 'var(--amarillo)', ROJO: 'var(--rojo)',
  BLANCO: 'var(--blanco)', PENDIENTE_GERENCIA: 'var(--pend)', SIN_DATOS: 'var(--sin)',
}
const FUENTE_TXT = {
  SNAPSHOT_SAP_HISTORICO: 'SNAPSHOT SAP HISTÓRICO',
  HISTORICO_REFERENCIA: 'HISTÓRICO · REFERENCIA',
  SINTETICO_DEMO: 'SINTÉTICO · DEMO',
  EN_VIVO: 'EN VIVO',
}
const ESTADO = { PAGADA: 'Pagada', ABIERTA: 'Abierta', CERRADA_NO_ACREDITADA: 'Cerrada sin cobro acreditado' }
const CLASES = [
  ['A', 'Excelente (A)'], ['B', 'Bueno (B)'], ['C', 'Regular (C)'], ['SOLO_CONTADO', 'Solo contado'],
  ['CANDIDATO_SOLO_CONTADO', 'Candidato a contado'], ['REVISION_HUMANA', 'Revisión humana'],
  ['EN_PRUEBA', 'En prueba'], ['SIN_DATOS', 'Sin datos'],
]

function Dot({ color, title }) {
  return <span className="dot" style={{ background: DOT[color] || DOT.SIN_DATOS }} title={title} aria-label={title} />
}

function FuenteBadge({ fuente }) {
  const live = fuente.tipo === 'EN_VIVO'
  return (
    <span className={`badge ${live ? 'live' : 'hist'}`} title={fuente.etiqueta}>
      {FUENTE_TXT[fuente.tipo] || fuente.tipo} · corte {fdate(fuente.fecha_corte)}
    </span>
  )
}

function Semaforo({ s }) {
  const cambia = s.calculado !== s.final
  return (
    <span className="sem">
      <Dot color={s.color_final} title={s.etiqueta_final} />
      <span>{s.etiqueta_final}</span>
      {cambia && <small className="muted"> (automático: {s.etiqueta_calculada})</small>}
    </span>
  )
}

function Panel({ rutas, enVivo, prioridades, abrirRuta, abrirCliente }) {
  return (
    <>
      <section className="aviso">
        <strong>Datos históricos, no en vivo.</strong> {enVivo.motivo} Cada ruta indica su fuente y fecha de corte.
      </section>
      <div className="grid">
        {rutas.map((r) => (
          <article key={r.route_id} className="card">
            <header>
              <h3>{r.nombre}</h3>
              <FuenteBadge fuente={r.fuente} />
            </header>
            <p className="muted">{r.vendedor} · {r.tipo_ruta.toLowerCase()} · plazo {r.plazo_dias} días</p>
            <p className="big">{money(r.saldo_total)}</p>
            <p className="muted">{r.clientes} clientes · saldo = facturas abiertas al corte {fdate(r.fuente.fecha_corte)}</p>
            <div className="chips">
              {CLASES.filter(([k]) => r.clasificacion[k]).map(([k, t]) => (
                <span key={k} className="chip">{t}: {r.clasificacion[k]}</span>
              ))}
            </div>
            <p className="muted small">
              {r.overrides_gerencia > 0 && `${r.overrides_gerencia} decisión(es) de gerencia sobre el cálculo automático · `}
              {r.alertas_mayor_60} alerta(s) de factura con mora &gt; 60 días
            </p>
            {r.avisos.length > 0 && <p className="error small">⚠ {r.avisos.length} diferencia(s) de conciliación con el Excel aprobado (ver detalle del cliente)</p>}
            <button onClick={() => abrirRuta(r.route_id)}>Ver ruta</button>
          </article>
        ))}
      </div>
      <h3>Mayores saldos del snapshot (top 10)</h3>
      <p className="muted small">Referencia sobre el snapshot cargado; no es cartera en vivo ni prioridad de cobro validada.</p>
      <div className="tablewrap">
        <table>
          <thead><tr><th>Cliente</th><th>Ruta</th><th>Folder</th><th className="num">Saldo</th><th>Fuente</th></tr></thead>
          <tbody>
            {prioridades.map((p) => (
              <tr key={p.codigo} onClick={() => abrirCliente(p.codigo)} className="click">
                <td>{p.nombre}<br /><small className="muted">{p.codigo}</small></td>
                <td>{p.ruta_nombre}</td><td>{p.folder}</td><td className="num">{money(p.saldo)}</td>
                <td><FuenteBadge fuente={p.fuente} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function Ruta({ id, abrirCliente, volver }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [zona, setZona] = useState('')
  const [clas, setClas] = useState('')
  const [q, setQ] = useState('')

  useEffect(() => {
    const p = new URLSearchParams()
    if (zona) p.set('zona', zona)
    if (clas) p.set('clasificacion', clas)
    if (q) p.set('q', q)
    api(`/rutas/${id}/clientes?${p}`).then(setData).catch((e) => setErr(e.message))
  }, [id, zona, clas, q])

  if (err) return <p className="error">{err}</p>
  if (!data) return <p>Cargando…</p>
  const { ruta, zonas, clientes } = data
  const total = clientes.reduce((a, c) => a + c.saldo, 0)
  return (
    <>
      <button className="link" onClick={volver}>← Panel</button>
      <header className="rutahead">
        <div>
          <h2>{ruta.nombre}</h2>
          <p className="muted">{ruta.vendedor} · {ruta.tipo_ruta.toLowerCase()} · plazo {ruta.plazo_dias} días</p>
        </div>
        <FuenteBadge fuente={ruta.fuente} />
      </header>
      <ul className="notas">{(ruta.fuente.notas || []).map((n) => <li key={n}>{n}</li>)}</ul>
      <div className="filtros">
        <input placeholder="Buscar cliente, código o folder" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={zona} onChange={(e) => setZona(e.target.value)}>
          <option value="">Todas las zonas</option>
          {[...new Set([...zonas.map((z) => z.prefijo), ...clientes.map((c) => c.zona)])].filter(Boolean).map((z) => (
            <option key={z} value={z}>{zonas.find((x) => x.prefijo === z)?.nombre || z} ({z})</option>
          ))}
        </select>
        <select value={clas} onChange={(e) => setClas(e.target.value)}>
          <option value="">Todas las clasificaciones</option>
          {CLASES.map(([k, t]) => <option key={k} value={k}>{t}</option>)}
        </select>
        <a className="btn" href={`/api/rutas/${id}/reporte.csv`}>Descargar CSV</a>
      </div>
      <div className="tablewrap">
        <table>
          <thead><tr><th>Folder</th><th>Cliente</th><th>Clasificación final</th><th className="num">Mora media</th><th className="num">Alertas &gt;60</th><th className="num">Saldo</th></tr></thead>
          <tbody>
            {clientes.map((c) => (
              <tr key={c.codigo} className="click" onClick={() => abrirCliente(c.codigo)}>
                <td><b>{c.folder}</b></td>
                <td>{c.nombre}<br /><small className="muted">{c.codigo}</small></td>
                <td><Semaforo s={c.semaforo} /></td>
                <td className="num">{c.semaforo.promedio_mora != null ? `${c.semaforo.promedio_mora} d` : '—'}</td>
                <td className="num">{c.semaforo.alertas.length || '—'}</td>
                <td className="num">{money(c.saldo)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot><tr><td colSpan="5">Total ({clientes.length} clientes mostrados)</td><td className="num">{money(total)}</td></tr></tfoot>
        </table>
      </div>
    </>
  )
}

function Cliente({ codigo, volver }) {
  const [c, setC] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => { api(`/clientes/${codigo}`).then(setC).catch((e) => setErr(e.message)) }, [codigo])
  if (err) return <p className="error">{err}</p>
  if (!c) return <p>Cargando…</p>
  const s = c.semaforo
  return (
    <>
      <button className="link" onClick={volver}>← Volver</button>
      <header className="rutahead">
        <div>
          <h2>{c.nombre}</h2>
          <p className="muted">{c.codigo} · folder {c.folder} · {c.ruta} · {c.vendedor}</p>
        </div>
        <FuenteBadge fuente={c.fuente} />
      </header>
      <div className="grid two">
        <article className="card">
          <h3>Clasificación</h3>
          <p className="big"><Semaforo s={s} /></p>
          {s.decision_gerencia && (
            <div className="decision">
              <b>Decisión de gerencia</b> — {s.decision_gerencia.actor}{s.decision_gerencia.fecha ? ` · ${fdate(s.decision_gerencia.fecha)}` : ''}
              <p>{s.decision_gerencia.motivo}</p>
              <p className="muted small">Cálculo automático conservado: {s.etiqueta_calculada}{s.promedio_mora != null ? ` (${s.promedio_mora} días de mora media)` : ''}.</p>
            </div>
          )}
          <p className="muted small">Calculada automáticamente por el motor Python a partir de las consultas SAP del {fdate(c.fuente.fecha_corte)}.</p>
          {c.conciliacion_gerencia && (c.conciliacion_gerencia.coincide
            ? <p className="small okmsg">✓ Coincide con el Excel aprobado por gerencia.</p>
            : <p className="error small">⚠ Difiere del Excel aprobado: {c.conciliacion_gerencia.diferencias.join('; ')}</p>)}
        </article>
        <article className="card">
          <h3>Saldo</h3>
          <p className="big">{money(c.saldo)}</p>
          <p className="muted small">Saldo del snapshot ({fdate(c.fuente.fecha_corte)}); no es saldo en vivo.</p>
        </article>
      </div>
      <h3>¿Por qué este semáforo?</h3>
      <ul className="notas">{s.explicacion.map((e) => <li key={e}>{e}</li>)}</ul>
      {s.alertas.length > 0 && (<><h3>Alertas</h3><ul className="alertas">{s.alertas.map((a) => <li key={a}>{a}</li>)}</ul></>)}
      <h3>Últimas {s.facturas.length} facturas (evidencia)</h3>
      <p className="muted small">{s.facturas_historial} factura(s) en el historial · {s.pagadas} pagada(s) · {s.abiertas} abierta(s) · {s.no_acreditadas} cierre(s) sin cobro acreditado · {s.facturas_en_media} evaluable(s) · mora = max(0, último recibo contable − emisión − {c.plazo_dias ?? 60} días); el vencimiento SAP se muestra solo para cotejo.</p>
      {s.facturas.length === 0 ? (
        <p className="muted">Sin facturas en el snapshot.</p>
      ) : (
        <div className="tablewrap">
          <table>
            <thead><tr><th>Factura</th><th>Emisión</th><th>Vence ruta</th><th>Vence SAP</th><th>Estado</th><th className="num">Total</th><th className="num">Aplicado SAP</th><th>Recibos</th><th className="num">Mora</th><th>En media</th></tr></thead>
            <tbody>
              {s.facturas.map((f) => (
                <React.Fragment key={f.id_factura}>
                  <tr className={f.alerta_mayor_60 ? 'warn' : ''}>
                    <td>{f.numero}</td><td>{fdate(f.fecha_factura)}</td><td>{fdate(f.vencimiento_ruta)}</td><td>{fdate(f.vencimiento_sap)}</td>
                    <td>{ESTADO[f.estado] || f.estado}</td>
                    <td className="num">{money(f.total)}</td><td className="num">{money(f.aplicado_sap)}</td>
                    <td className="small">{f.recibos.length ? `${f.recibos.length} · último ${fdate(f.ultimo_recibo)}` : '—'}</td>
                    <td className="num">{f.mora ?? '—'}{f.alerta_mayor_60 ? ' ⚠' : ''}</td>
                    <td>{f.en_media ? 'Sí' : 'No'}</td>
                  </tr>
                  <tr className="detalle"><td colSpan="10" className="small muted">
                    {f.motivo}
                    {f.recibos.length > 0 && <> Recibos: {f.recibos.map((r) => `#${r.numero} ${fdate(r.fecha)} ${money(r.monto)} (${r.via})`).join(' · ')}.</>}
                    {f.excepciones.length > 0 && <> Notas: {f.excepciones.join(', ').replaceAll('_', ' ').toLowerCase()}.</>}
                  </td></tr>
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

function Alertas({ rutas, abrirCliente }) {
  const [items, setItems] = useState(null)
  useEffect(() => {
    Promise.all(rutas.map((r) => api(`/rutas/${r.route_id}/clientes`))).then((all) =>
      setItems(all.flatMap((d) => d.clientes.map((c) => ({ ...c, ruta_nombre: d.ruta.nombre })))))
  }, [rutas])
  if (!items) return <p>Cargando…</p>
  const gerencia = items.filter((c) => c.semaforo.requiere_gerencia)
  const f60 = items.filter((c) => c.semaforo.alertas.length)
  return (
    <>
      <h3>Pendientes de decisión de gerencia</h3>
      {gerencia.length === 0 ? <p className="muted">Ninguno en los datos cargados.</p> : (
        <ul className="lista">{gerencia.map((c) => (
          <li key={c.codigo} onClick={() => abrirCliente(c.codigo)} className="click"><Dot color={c.semaforo.color_final} title="pendiente" /> {c.nombre} <small className="muted">· {c.ruta_nombre} · {c.semaforo.etiqueta_calculada}</small></li>
        ))}</ul>
      )}
      <h3>Facturas con mora &gt; 60 días (alerta por factura)</h3>
      {f60.length === 0 ? <p className="muted">Ninguna en los datos con detalle de facturas.</p> : (
        <ul className="lista">{f60.map((c) => (
          <li key={c.codigo} onClick={() => abrirCliente(c.codigo)} className="click">{c.nombre} <small className="muted">· {c.semaforo.alertas.join(' ')}</small></li>
        ))}</ul>
      )}
      <h3>Hojas de visita y salidas sin visita <span className="badge maqueta">MAQUETA</span></h3>
      <p className="muted">Pendiente: cobertura de hojas de visita (previstas / firmadas / no firmadas / motivos), 3 salidas consecutivas sin visitar y oportunidades de clientes A. Requiere un registro propio de visitas (BD separada de Cosprobell BD); aún no hay datos reales ni escritura.</p>
    </>
  )
}

export default function App() {
  const [rol, setRol] = useState('GERENCIA')
  const [vista, setVista] = useState({ t: 'panel' })
  const [atras, setAtras] = useState([])
  const [rutas, setRutas] = useState([])
  const [enVivo, setEnVivo] = useState({ motivo: '' })
  const [prior, setPrior] = useState([])
  const [err, setErr] = useState(null)

  useEffect(() => {
    api('/rutas').then((d) => { setRutas(d.rutas); setEnVivo(d.en_vivo) }).catch((e) => setErr(e.message))
    api('/prioridades?n=10').then((d) => setPrior(d.prioridades)).catch(() => {})
  }, [])

  const vendedores = useMemo(() => [...new Set(rutas.map((r) => r.vendedor))], [rutas])
  const [vend, setVend] = useState('')
  const visibles = rol === 'GERENCIA' ? rutas : rutas.filter((r) => r.vendedor === (vend || vendedores[0]))
  const ir = (v) => { setAtras((a) => [...a, vista]); setVista(v) }
  const volver = () => { setVista(atras.at(-1) || { t: 'panel' }); setAtras((a) => a.slice(0, -1)) }

  return (
    <div className="app">
      <header className="top">
        <div className="brand" onClick={() => { setAtras([]); setVista({ t: 'panel' }) }}>Portal CxC <small>MVP</small></div>
        <nav>
          <button className={vista.t === 'panel' ? 'on' : ''} onClick={() => { setAtras([]); setVista({ t: 'panel' }) }}>Panel</button>
          <button className={vista.t === 'alertas' ? 'on' : ''} onClick={() => ir({ t: 'alertas' })}>Alertas</button>
        </nav>
        <div className="rol">
          <span className="badge maqueta" title="Selector de rol solo para demostración local; no es autenticación">DEMO</span>
          <select value={rol} onChange={(e) => setRol(e.target.value)} aria-label="Rol (demo)">
            <option value="GERENCIA">Gerencia</option><option value="VENDEDOR">Vendedor</option>
          </select>
          {rol === 'VENDEDOR' && (
            <select value={vend || vendedores[0] || ''} onChange={(e) => setVend(e.target.value)} aria-label="Vendedor (demo)">
              {vendedores.map((v) => <option key={v}>{v}</option>)}
            </select>
          )}
        </div>
      </header>
      <main>
        {err && <p className="error">No se pudo contactar la API: {err}. Inicia el backend: <code>python -m cxc_portal.server</code></p>}
        {vista.t === 'panel' && <Panel rutas={visibles} enVivo={enVivo} prioridades={prior.filter((p) => visibles.some((r) => r.route_id === p.ruta))} abrirRuta={(id) => ir({ t: 'ruta', id })} abrirCliente={(c) => ir({ t: 'cliente', c })} />}
        {vista.t === 'ruta' && <Ruta id={vista.id} abrirCliente={(c) => ir({ t: 'cliente', c })} volver={volver} />}
        {vista.t === 'cliente' && <Cliente codigo={vista.c} volver={volver} />}
        {vista.t === 'alertas' && <Alertas rutas={visibles} abrirCliente={(c) => ir({ t: 'cliente', c })} />}
      </main>
      <footer>Solo lectura · sin conexión a SAP ni a Cosprobell BD · las decisiones de gerencia se muestran junto al cálculo base.</footer>
    </div>
  )
}
