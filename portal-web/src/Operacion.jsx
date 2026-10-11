import React, { useCallback, useEffect, useState } from 'react'
import { api, descargar, post } from './api.js'
import { fdate, money } from './ui.jsx'

const PREP = { futura: 'Futura', por_preparar: 'Por preparar', preparada: 'Preparada', revisada: 'Revisada', atrasada: 'Atrasada', cerrada: 'Cerrada', cancelada: 'Cancelada' }
const SEV = { alta: 'Alta', media: 'Media', info: 'Info' }

// Calendario de salidas: preparación automática 3 días hábiles antes (lunes a sábado, feriados configurables).
export function Calendario({ rutas, user }) {
  const [d, setD] = useState(null)
  const [msg, setMsg] = useState(null)
  const [rid, setRid] = useState('')
  const [periodo, setPeriodo] = useState('')
  const [serie, setSerie] = useState({ ruta: rutas[0]?.route_id || '', fechas: '' })
  const [fer, setFer] = useState({ fecha: '', nombre: '' })
  const gestiona = ['admin', 'gerencia'].includes(user.rol)
  const cargar = useCallback(() => api(`/calendario?${new URLSearchParams({ ...(rid ? { ruta: rid } : {}), ...(periodo ? { periodo } : {}) })}`).then(setD).catch((e) => setMsg(e.message)), [rid, periodo])
  useEffect(() => { cargar() }, [cargar])
  const accion = async (fn, ok) => { setMsg(null); try { const r = await fn(); setMsg(typeof ok === 'function' ? ok(r) : ok); await cargar() } catch (e) { setMsg(e.message) } }

  return (
    <>
      <h2>Calendario de salidas</h2>
      <p className="muted small">La preparación se hace automáticamente {d?.anticipacion_dias_habiles ?? 3} días hábiles antes de cada salida ({d?.semana_laboral || 'lunes a sábado'}; los feriados registrados no cuentan). Hoy: {fdate(d?.hoy)}.</p>
      {msg && <p className="aviso">{msg}</p>}
      <div className="filtros">
        <select value={rid} onChange={(e) => setRid(e.target.value)} aria-label="Ruta"><option value="">Todas las rutas</option>{rutas.map((r) => <option key={r.route_id} value={r.route_id}>{r.nombre}</option>)}</select>
        <input type="month" value={periodo} onChange={(e) => setPeriodo(e.target.value)} aria-label="Período" />
        {gestiona && <button onClick={() => accion(() => post('/salidas/ciclo', {}), (r) => `Ciclo ejecutado: ${r.preparadas.length} preparada(s), ${r.atrasadas.length} atrasada(s), ${r.errores.length} error(es).`)}>Ejecutar ciclo de preparación</button>}
      </div>
      <div className="tablewrap"><table>
        <thead><tr><th>Salida</th><th>Ruta</th><th>Vendedor</th><th>Se prepara el</th><th>Estado</th><th>Excepciones</th></tr></thead>
        <tbody>{(d?.salidas || []).map((s) => (
          <tr key={s.id}><td><b>{fdate(s.fecha_salida)}</b></td><td>{s.ruta_nombre}</td><td>{s.vendedor}</td><td>{fdate(s.fecha_preparacion)}</td>
            <td>{PREP[s.preparacion] || s.preparacion}</td><td>{s.excepciones ?? '—'}{s.requiere_revision ? (s.revisada ? ' · revisada ✓' : ' · requiere revisión de gerencia') : ''}</td></tr>))}
          {d && d.salidas.length === 0 && <tr><td colSpan={6} className="muted">Sin salidas programadas.</td></tr>}</tbody>
      </table></div>
      {gestiona && (
        <>
          <form className="form" onSubmit={(e) => { e.preventDefault(); accion(() => post('/salidas/serie', { ruta: serie.ruta, fechas: serie.fechas.split(/[\s,;]+/).filter(Boolean) }),
            (r) => `${r.creadas.length} creada(s), ${r.existentes.length} ya existían, ${r.rechazadas.length} rechazada(s)${r.rechazadas.length ? ': ' + r.rechazadas.map((x) => `${x.fecha} (${x.motivo})`).join('; ') : ''}`) }}>
            <h4>Programar varias salidas</h4>
            <select value={serie.ruta} onChange={(e) => setSerie({ ...serie, ruta: e.target.value })}>{rutas.map((r) => <option key={r.route_id} value={r.route_id}>{r.nombre}</option>)}</select>
            <input placeholder="Fechas AAAA-MM-DD separadas por coma" value={serie.fechas} onChange={(e) => setSerie({ ...serie, fechas: e.target.value })} required />
            <button type="submit">Programar</button>
          </form>
          <form className="form" onSubmit={(e) => { e.preventDefault(); accion(() => post('/admin/feriados', fer), 'Feriado registrado.'); setFer({ fecha: '', nombre: '' }) }}>
            <h4>Feriados ({(d?.feriados || []).length})</h4>
            <ul className="notas">{(d?.feriados || []).map((f) => <li key={f.fecha}>{fdate(f.fecha)} · {f.nombre} <button className="sec" onClick={(ev) => { ev.preventDefault(); accion(() => api(`/admin/feriados/${f.fecha}`, { method: 'DELETE' }), 'Feriado retirado.') }}>Quitar</button></li>)}</ul>
            <input type="date" value={fer.fecha} onChange={(e) => setFer({ ...fer, fecha: e.target.value })} required />
            <input placeholder="Nombre del feriado" value={fer.nombre} onChange={(e) => setFer({ ...fer, nombre: e.target.value })} required />
            <button type="submit">Agregar feriado</button>
          </form>
        </>
      )}
    </>
  )
}

// Avisos persistentes (informan, no deciden) y notificaciones SIMULADAS.
export function Avisos({ rutas, user }) {
  const [av, setAv] = useState([])
  const [no, setNo] = useState([])
  const [filtro, setFiltro] = useState('nueva')
  const [msg, setMsg] = useState(null)
  const gestiona = ['admin', 'gerencia'].includes(user.rol)
  const cargar = useCallback(() => {
    api(`/avisos${filtro ? `?estado=${filtro}` : ''}`).then((d) => setAv(d.avisos)).catch((e) => setMsg(e.message))
    api('/notificaciones').then((d) => setNo(d.notificaciones)).catch(() => {})
  }, [filtro])
  useEffect(() => { cargar() }, [cargar])
  const atender = async (a, estado) => {
    const nota = estado === 'cerrada' ? window.prompt('Nota para cerrar el aviso') : ''
    if (estado === 'cerrada' && !nota) return
    try { await post(`/avisos/${a.id}/atender`, { estado, nota }); cargar() } catch (e) { setMsg(e.message) }
  }
  return (
    <>
      <h2>Avisos</h2>
      <p className="muted small">Los avisos informan; no cambian clasificaciones. Los candidatos a solo contado siempre requieren la decisión de gerencia.</p>
      {msg && <p className="aviso">{msg}</p>}
      <div className="filtros"><select value={filtro} onChange={(e) => setFiltro(e.target.value)} aria-label="Estado"><option value="nueva">Nuevos</option><option value="vista">Vistos</option><option value="cerrada">Cerrados</option><option value="">Todos</option></select></div>
      <div className="tablewrap"><table>
        <thead><tr><th>Severidad</th><th>Aviso</th><th>Ruta</th><th>Estado</th>{gestiona && <th />}</tr></thead>
        <tbody>{av.map((a) => (
          <tr key={a.id}><td>{SEV[a.severidad]}</td><td>{a.titulo}</td><td>{rutas.find((r) => r.route_id === a.route_id)?.nombre || a.route_id}</td><td>{a.estado}{a.nota ? ` — ${a.nota}` : ''}</td>
            {gestiona && <td>{a.estado === 'nueva' && <button className="sec" onClick={() => atender(a, 'vista')}>Visto</button>} {a.estado !== 'cerrada' && <button className="sec" onClick={() => atender(a, 'cerrada')}>Cerrar</button>}</td>}</tr>))}
          {av.length === 0 && <tr><td colSpan={5} className="muted">Sin avisos.</td></tr>}</tbody>
      </table></div>
      {gestiona && <Parametros />}
      <h3>Notificaciones (simuladas)</h3>
      <p className="muted small">No se envía ningún mensaje fuera del sistema; aquí se ve lo que se enviaría y a quién.</p>
      <div className="tablewrap"><table>
        <thead><tr><th>Para</th><th>Asunto</th><th>Estado</th></tr></thead>
        <tbody>{no.slice(0, 50).map((n) => <tr key={n.id}><td>{n.destinatario} <small className="muted">({n.rol})</small></td><td title={n.cuerpo}>{n.asunto}</td><td>{n.estado}</td></tr>)}
          {no.length === 0 && <tr><td colSpan={3} className="muted">Sin notificaciones.</td></tr>}</tbody>
      </table></div>
    </>
  )
}

function Parametros() {
  const [c, setC] = useState(null)
  const [msg, setMsg] = useState(null)
  const cargar = useCallback(() => api('/configuracion').then(setC).catch((e) => setMsg(e.message)), [])
  useEffect(() => { cargar() }, [cargar])
  const guardar = async (clave, valor) => { setMsg(null); try { await post('/admin/configuracion', { clave, valor: Number(valor) }); setMsg('Parámetro guardado (queda auditado).'); cargar() } catch (e) { setMsg(e.message) } }
  if (!c) return null
  return (
    <details><summary><b>Parámetros de avisos y oportunidades</b></summary>
      {msg && <p className="aviso">{msg}</p>}
      {Object.keys(c.valores).map((k) => (
        <form key={k} className="form" onSubmit={(e) => { e.preventDefault(); guardar(k, e.target.elements.v.value) }}>
          <label>{c.descripcion[k]}<input name="v" type="number" min={c.rangos[k][0]} max={c.rangos[k][1]} defaultValue={c.valores[k]} key={c.valores[k]} /></label>
          <small className="muted">Por defecto {c.por_defecto[k]} · rango {c.rangos[k][0]}–{c.rangos[k][1]}</small>
          <button type="submit">Guardar</button>
        </form>))}
    </details>
  )
}

const pct = (x) => (x == null ? '—' : `${(Number(x) * 100).toFixed(0)} %`)

// Dashboard gerencial: cartera (cxc-reporting-core, NO VALIDADO), clasificaciones, alertas, decisiones, oportunidades y cumplimiento.
export function Dashboard({ user }) {
  const [d, setD] = useState(null)
  const [msg, setMsg] = useState(null)
  const glob = ['admin', 'gerencia'].includes(user.rol)
  useEffect(() => { api(glob ? '/dashboard' : '/dashboard/mio').then(setD).catch((e) => setMsg(e.message)) }, [glob])
  if (msg) return <p className="aviso">{msg}</p>
  if (!d) return <p className="muted">Cargando…</p>
  return (
    <>
      <h2>Dashboard</h2>
      <p className="aviso">{d.aviso}</p>
      <p className="muted small">Fuente: {d.fuente.etiqueta}</p>
      <h3>Clasificaciones</h3>
      <p>{Object.entries(d.clasificaciones).map(([k, v]) => `${k}: ${v}`).join(' · ')}</p>
      <h3>Rutas</h3>
      <div className="tablewrap"><table>
        <thead><tr><th>Ruta</th><th>Vendedor</th><th>Clientes</th><th>Saldo</th><th>Pend. gerencia</th><th>Avisos (alta/media)</th><th>Última salida</th></tr></thead>
        <tbody>{d.por_ruta.map((r) => <tr key={r.ruta}><td>{r.nombre}</td><td>{r.vendedor}</td><td>{r.clientes}</td><td>{money(r.saldo)}</td><td>{r.pendientes_gerencia}</td>
          <td>{r.alertas_abiertas.alta}/{r.alertas_abiertas.media}</td><td>{r.ultima_salida_preparada ? `${fdate(r.ultima_salida_preparada.fecha)} · ${r.ultima_salida_preparada.excepciones} exc.` : '—'}</td></tr>)}</tbody>
      </table></div>
      <h3>Mayores saldos</h3>
      <div className="tablewrap"><table><thead><tr><th>Cliente</th><th>Ruta</th><th>Saldo</th><th>Clase</th></tr></thead>
        <tbody>{d.top_saldos.map((c) => <tr key={c.codigo}><td>{c.folder} {c.cliente}</td><td>{c.ruta}</td><td>{money(c.saldo)}</td><td>{c.clasificacion}</td></tr>)}</tbody></table></div>
      {d.core && (d.core.disponible ? (
        <>
          <h3>Cartera (cxc-reporting-core · {d.core.estado_validacion})</h3>
          <p>Saldo vencido {money(d.core.cartera.saldo_vencido)} · mora {pct(d.core.cartera.pct_mora)} · {d.core.cartera.n_documentos_abiertos} documento(s) abiertos · máx. {d.core.cartera.dias_max_mora} días</p>
          <div className="tablewrap"><table><thead><tr><th>Tramo</th><th>Saldo</th><th>Docs</th></tr></thead><tbody>{d.core.aging.map((t) => <tr key={t.tramo}><td>{t.tramo}</td><td>{money(t.saldo)}</td><td>{t.n_documentos}</td></tr>)}</tbody></table></div>
          <div className="tablewrap"><table><thead><tr><th>Vendedor</th><th>Clientes</th><th>Saldo</th><th>Mora</th></tr></thead><tbody>{d.core.por_vendedor.map((v) => <tr key={v.nombre}><td>{v.nombre}</td><td>{v.n_clientes}</td><td>{money(v.saldo_positivo)}</td><td>{pct(v.pct_mora)}</td></tr>)}</tbody></table></div>
        </>
      ) : <p className="muted small">Cartera del core no disponible: {d.core.motivo}</p>)}
      <h3>Avisos abiertos ({d.alertas.abiertas})</h3>
      <p>{Object.entries(d.alertas.por_severidad).map(([k, v]) => `${SEV[k]}: ${v}`).join(' · ')}</p>
      <h3>Pendientes de decisión de gerencia ({d.decisiones.pendientes_de_gerencia.length})</h3>
      <ul className="notas">{d.decisiones.pendientes_de_gerencia.map((c) => <li key={c.codigo}>{c.codigo} {c.cliente} · {c.calculado}</li>)}</ul>
      <h3>Oportunidades comerciales (solo informativas)</h3>
      <p className="muted small">{d.oportunidades.criterio}</p>
      <ul className="notas">{d.oportunidades.oportunidades.map((o) => <li key={o.codigo}>{o.folder} {o.cliente} · última compra {fdate(o.ultima_compra)} ({o.dias_desde_ultima ?? '—'} d) · {o.facturas_ventana} factura(s) en la ventana</li>)}</ul>
      <h3>Cumplimiento de rutas</h3>
      <div className="tablewrap"><table><thead><tr><th>Ruta</th><th>Salida</th><th>Visitados</th><th>Cumplimiento</th><th>Cobro decl.</th><th>Venta decl.</th></tr></thead>
        <tbody>{d.cumplimiento.map((c) => <tr key={c.salida}><td>{c.ruta_nombre}</td><td>{fdate(c.fecha_salida)}</td><td>{c.visitados}/{c.clientes}</td><td>{pct(c.cumplimiento)}</td><td>{money(c.cobro_declarado)}</td><td>{money(c.venta_declarada)}</td></tr>)}</tbody></table></div>
      <p className="muted small">Cobros y ventas son montos declarados en campo; no provienen de SAP.</p>
    </>
  )
}

// Excel de ruta versionado (Actual / Histórico), preparación de impresión y comparación con la salida anterior.
export function ExcelSalida({ salida, ruta, gestiona, todas }) {
  const [vers, setVers] = useState(null)
  const [bases, setBases] = useState([])
  const [base, setBase] = useState('')
  const [msg, setMsg] = useState(null)
  const [cmp, setCmp] = useState(null)
  const cargar = useCallback(() => api(`/rutas/${ruta}/excel`).then(setVers).catch((e) => setMsg(e.message)), [ruta])
  useEffect(() => { cargar(); if (gestiona) api('/excel/bases').then((d) => { setBases(d.bases); setBase(d.bases[0] || '') }).catch(() => {}) }, [cargar, gestiona])
  const accion = async (fn, ok) => { setMsg(null); try { const r = await fn(); setMsg(typeof ok === 'function' ? ok(r) : ok); await cargar() } catch (e) { setMsg(e.message) } }
  const anterior = (todas || []).filter((s) => s.fecha_salida < salida.fecha_salida && s.reporte_excepciones).sort((a, b) => b.fecha_salida.localeCompare(a.fecha_salida))[0]
  return (
    <>
      <h4>Excel de ruta y documentos</h4>
      {msg && <p className="aviso">{msg}</p>}
      {vers && vers.integridad.length > 0 && <p className="aviso">Integridad: {vers.integridad.join('; ')}</p>}
      {gestiona && (
        <div className="filtros">
          <select value={base} onChange={(e) => setBase(e.target.value)} aria-label="Archivo base">{bases.map((b) => <option key={b}>{b}</option>)}</select>
          <button disabled={!base} onClick={() => accion(() => post(`/salidas/${salida.id}/excel`, { base }), (r) => (r.publicable ? (r.promovida ? `Versión ${r.version} publicada en Actual.` : r.creada ? `Versión ${r.version} publicada en Actual.` : 'Sin cambios: ya está publicada.') : `Borrador v${r.version}${r.creada ? ' generado' : ' (sin cambios)'}; no se publica: ${r.motivos.join(' ')}`))}>Generar Excel</button>
          <button className="sec" onClick={() => accion(() => post(`/salidas/${salida.id}/impresion`), (r) => `Impresión: ${r.creadas.length} solicitud(es) nueva(s), ${r.existentes.length} existente(s)${r.omitidas.length ? ' · ' + r.omitidas.map((o) => o.motivo).join(' ') : ''}`)}>Preparar impresión</button>
          {anterior && <button className="sec" onClick={() => accion(async () => setCmp(await api(`/comparar-salidas?a=${anterior.id}&b=${salida.id}`)), 'Comparación lista.')}>Comparar con {fdate(anterior.fecha_salida)}</button>}
        </div>
      )}
      <div className="tablewrap"><table><thead><tr><th>Versión</th><th>Carpeta</th><th>Salida</th><th>Fuente</th><th /></tr></thead>
        <tbody>{(vers?.versiones || []).map((v) => <tr key={v.id}><td>v{v.version}{v.borrador ? ' · BORRADOR' : ''}</td><td>{v.estado === 'ACTUAL' ? 'Actual' : v.estado === 'BORRADOR' ? 'Borrador' : 'Histórico'}</td><td>{fdate(v.fecha_salida)}</td><td className="small">{v.fuente_tipo} · {fdate(v.fuente_corte)}{v.motivos_borrador && JSON.parse(v.motivos_borrador).length > 0 ? <><br />{JSON.parse(v.motivos_borrador).join(' ')}</> : null}</td>
          <td><a href={`/api/excel/${v.id}/descargar`}>Descargar</a></td></tr>)}
          {vers && vers.versiones.length === 0 && <tr><td colSpan={5} className="muted">Aún no hay Excel publicado para esta ruta.</td></tr>}</tbody></table></div>
      {cmp && (
        <>
          <p className="muted small">{cmp.cambios.length} cambio(s) entre {fdate(cmp.salida_anterior.fecha)} y {fdate(cmp.salida_posterior.fecha)}; {cmp.sin_cambio} sin cambio.</p>
          <ul className="notas">{cmp.cambios.map((c) => <li key={c.codigo}>{c.codigo}: {c.tipo === 'CAMBIO' ? `${c.antes} → ${c.ahora}${c.delta_mora != null ? ` · mora ${c.delta_mora > 0 ? '+' : ''}${c.delta_mora} d` : ''}` : c.tipo}</li>)}</ul>
        </>
      )}
    </>
  )
}
