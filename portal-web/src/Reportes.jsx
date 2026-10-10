import React, { useEffect, useState } from 'react'
import { api, post } from './api.js'
import { money } from './ui.jsx'

const pct = (x) => (x == null ? '—' : `${(Number(x) * 100).toFixed(1)} %`)

// Reportes gerenciales con cxc-reporting-core. El corte del snapshot es NO VALIDADO (sin conciliación contable SAP): se muestra siempre.
export function Reportes() {
  const [rep, setRep] = useState(null)
  const [msg, setMsg] = useState(null)
  const [cargando, setCargando] = useState(false)

  const cargar = () => api('/reportes/gerencial').then(setRep).catch((e) => { setRep(null); setMsg(e.message) })
  useEffect(() => { cargar() }, [])

  const importar = async () => {
    setCargando(true); setMsg(null)
    try { const r = await post('/reportes/importar-corte'); setMsg(`Corte ${r.id_corte}: ${r.status} · ${r.estado_validacion}`); await cargar() } catch (e) { setMsg(e.message) }
    setCargando(false)
  }

  return (
    <>
      <h2>Reportes gerenciales</h2>
      <p className="aviso"><b>Corte NO VALIDADO.</b> Se construye con <code>cxc-reporting-core</code> desde el snapshot SAP; sin validación contable no es cartera oficial ni en vivo.</p>
      <div className="filtros">
        <button onClick={importar} disabled={cargando}>{cargando ? 'Procesando…' : 'Importar snapshot como corte'}</button>
        {rep && <a className="btn" href="/api/reportes/gerencial.xlsx">Descargar Excel gerencial</a>}
      </div>
      {msg && <p className="muted">{msg}</p>}
      {rep && (
        <>
          <p className="muted small">{rep.aviso_portal}</p>
          <div className="grid">
            <article className="card"><h3>Cartera (facturas abiertas)</h3><p className="big">{money(rep.cartera.cartera_positiva)}</p>
              <p className="muted small">{rep.cartera.n_clientes_con_saldo} clientes · {rep.cartera.n_documentos_abiertos} documentos · mora máx. {rep.cartera.dias_max_mora} días</p></article>
            <article className="card"><h3>Vencido</h3><p className="big">{money(rep.cartera.saldo_vencido)}</p>
              <p className="muted small">{pct(rep.cartera.pct_mora)} de la cartera · corriente {money(rep.cartera.saldo_corriente)}</p></article>
            <article className="card"><h3>Concentración</h3><p className="big">{pct(rep.concentracion?.pct_top_n)}</p>
              <p className="muted small">en los {rep.concentracion?.top_n} mayores clientes · clases A/B/C: {rep.concentracion?.clase_a}/{rep.concentracion?.clase_b}/{rep.concentracion?.clase_c}</p></article>
          </div>
          <p className="muted small">“SIN VENDEDOR / SIN ZONA” son clientes de la cartera que aún no pertenecen a una ruta cargada en el portal: el snapshot no trae vendedor ni zona.</p>
          <h3>Antigüedad</h3>
          <div className="tablewrap"><table><thead><tr><th>Tramo</th><th className="num">Saldo</th><th className="num">Documentos</th><th className="num">%</th></tr></thead>
            <tbody>{(rep.aging || []).map((t) => <tr key={t.tramo}><td>{t.tramo}</td><td className="num">{money(t.saldo)}</td><td className="num">{t.n_documentos}</td><td className="num">{pct(t.pct)}</td></tr>)}</tbody></table></div>
          <h3>Por vendedor</h3>
          <div className="tablewrap"><table><thead><tr><th>Vendedor</th><th className="num">Clientes</th><th className="num">Saldo</th><th className="num">Vencido</th><th className="num">% mora</th></tr></thead>
            <tbody>{(rep.por_vendedor || []).map((g) => <tr key={g.nombre}><td>{g.nombre}</td><td className="num">{g.n_clientes}</td><td className="num">{money(g.saldo_positivo)}</td><td className="num">{money(g.saldo_vencido)}</td><td className="num">{pct(g.pct_mora)}</td></tr>)}</tbody></table></div>
          <h3>Clientes prioritarios (mayor saldo vencido)</h3>
          <div className="tablewrap"><table><thead><tr><th>Cliente</th><th>Vendedor</th><th className="num">Saldo</th><th className="num">Vencido</th><th className="num">Mora máx.</th></tr></thead>
            <tbody>{(rep.clientes_prioritarios || []).map((c) => <tr key={c.codigo_cliente}><td>{c.nombre_cliente}<br /><small className="muted">{c.codigo_cliente}</small></td><td>{c.vendedor}</td>
              <td className="num">{money(c.saldo_positivo)}</td><td className="num">{money(c.saldo_vencido)}</td><td className="num">{c.dias_max_mora} d</td></tr>)}</tbody></table></div>
        </>
      )}
    </>
  )
}
