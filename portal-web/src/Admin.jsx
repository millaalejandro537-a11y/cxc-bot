import React, { useCallback, useEffect, useState } from 'react'
import { api, post } from './api.js'

// Administración (rol admin): usuarios, vendedores, rutas/zonas/clientes y orden de folders. Todo se valida y audita en la API.
function Usuarios({ vendedores }) {
  const [us, setUs] = useState([])
  const [f, setF] = useState({ usuario: '', nombre: '', rol: 'vendedor', vendedor: '', password: '' })
  const [msg, setMsg] = useState(null)
  const cargar = useCallback(() => api('/admin/usuarios').then((d) => setUs(d.usuarios)), [])
  useEffect(() => { cargar() }, [cargar])
  const crear = async (e) => {
    e.preventDefault(); setMsg(null)
    try { await post('/admin/usuarios', f); setF({ ...f, usuario: '', nombre: '', password: '' }); cargar(); setMsg('Usuario creado: deberá cambiar su contraseña en el primer acceso.') } catch (err) { setMsg(err.message) }
  }
  const alternar = async (u) => { try { await api(`/admin/usuarios/${u.id}`, { method: 'PATCH', body: { activo: !u.activo } }); cargar() } catch (err) { setMsg(err.message) } }
  return (
    <>
      <h3>Usuarios</h3>
      <div className="tablewrap"><table>
        <thead><tr><th>Usuario</th><th>Rol</th><th>Vendedor</th><th>Último acceso</th><th>Estado</th></tr></thead>
        <tbody>{us.map((u) => <tr key={u.id}><td>{u.usuario}<br /><small className="muted">{u.nombre}</small></td><td>{u.rol}</td><td>{u.vendedor || '—'}</td><td>{u.ultimo_acceso || '—'}</td>
          <td><button className="sec" onClick={() => alternar(u)}>{u.activo ? 'Desactivar' : 'Activar'}</button></td></tr>)}</tbody>
      </table></div>
      <form onSubmit={crear} className="form">
        <h4>Nuevo usuario</h4>
        <input placeholder="Usuario" value={f.usuario} onChange={(e) => setF({ ...f, usuario: e.target.value })} required />
        <input placeholder="Nombre" value={f.nombre} onChange={(e) => setF({ ...f, nombre: e.target.value })} />
        <select value={f.rol} onChange={(e) => setF({ ...f, rol: e.target.value })}><option value="vendedor">Vendedor</option><option value="gerencia">Gerencia</option><option value="admin">Administrador</option></select>
        {f.rol === 'vendedor' && <select value={f.vendedor} onChange={(e) => setF({ ...f, vendedor: e.target.value })} required><option value="">— vendedor —</option>{vendedores.map((v) => <option key={v.id}>{v.nombre}</option>)}</select>}
        <input type="password" autoComplete="new-password" placeholder="Contraseña inicial (≥12, 3 de 4 clases)" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} required />
        <button type="submit">Crear</button>
        {msg && <p className="muted">{msg}</p>}
      </form>
    </>
  )
}

function Rutas({ vendedores, recargarVendedores }) {
  const [rutas, setRutas] = useState([])
  const [f, setF] = useState({ route_id: '', nombre: '', vendedor: '', tipo_ruta: 'FORANEA', plazo_dias: '', zonas: '', clientes: '' })
  const [imp, setImp] = useState({ archivo: '', route_id: '', nombre: '', vendedor: '', tipo_ruta: 'FORANEA' })
  const [add, setAdd] = useState({ route_id: '', codigo: '', nombre: '', zona: '' })
  const [msg, setMsg] = useState(null)
  const cargar = useCallback(() => api('/admin/rutas').then((d) => setRutas(d.rutas)), [])
  useEffect(() => { cargar() }, [cargar])

  const parseZonas = (t) => Object.fromEntries(t.split('\n').map((l) => l.split(',').map((x) => x.trim())).filter((p) => p[0] && p[1]))
  const parseClientes = (t) => t.split('\n').map((l) => l.split(',').map((x) => x.trim())).filter((p) => p[0]).map(([folder, codigo, ...n]) => ({ folder, codigo, nombre: n.join(', ') }))
  const crear = async (e) => {
    e.preventDefault(); setMsg(null)
    try {
      const body = { ...f, plazo_dias: f.plazo_dias ? Number(f.plazo_dias) : undefined, zonas: parseZonas(f.zonas), clientes: parseClientes(f.clientes) }
      const r = await post('/admin/rutas', body); setMsg(`Ruta ${r.route_id} guardada con ${r.clientes.length} clientes.`); cargar(); recargarVendedores()
    } catch (err) { setMsg(err.message) }
  }
  const importar = async (e) => {
    e.preventDefault(); setMsg(null)
    try { const r = await post('/admin/rutas/importar', imp); setMsg(`Ruta ${r.route_id} importada con ${r.clientes.length} clientes.`); cargar(); recargarVendedores() } catch (err) { setMsg(err.message) }
  }
  const agregar = async (e) => {
    e.preventDefault(); setMsg(null)
    try { const r = await post(`/admin/rutas/${add.route_id}/clientes`, add); setMsg(`Agregado como ${r.folder}.`); cargar() } catch (err) { setMsg(err.message) }
  }
  const quitar = async (rid, cod) => {
    if (!window.confirm(`¿Retirar ${cod} de la ruta? No se renumeran los folders.`)) return
    try { await api(`/admin/rutas/${rid}/clientes/${cod}`, { method: 'DELETE' }); cargar() } catch (err) { setMsg(err.message) }
  }
  return (
    <>
      <h3>Rutas, zonas y clientes</h3>
      {msg && <p className="aviso">{msg}</p>}
      {rutas.map((r) => (
        <details key={r.route_id} className="card">
          <summary><b>{r.nombre}</b> <small className="muted">({r.route_id}) · {r.vendedor} · {r.tipo_ruta.toLowerCase()} {r.plazo_dias} d · {r.clientes.length} clientes{r.activa ? '' : ' · INACTIVA'}</small></summary>
          <p className="muted small">{r.estado_fisico} {r.notas}</p>
          <div className="tablewrap"><table><thead><tr><th>Folder</th><th>Código</th><th>Cliente</th><th /></tr></thead>
            <tbody>{r.clientes.map((c) => <tr key={c.codigo}><td><b>{c.folder}</b></td><td>{c.codigo}</td><td>{c.nombre}</td><td><button className="sec" onClick={() => quitar(r.route_id, c.codigo)}>Retirar</button></td></tr>)}</tbody></table></div>
        </details>
      ))}
      <form onSubmit={agregar} className="form"><h4>Agregar cliente a una ruta (toma el siguiente número de su zona)</h4>
        <select value={add.route_id} onChange={(e) => setAdd({ ...add, route_id: e.target.value })} required><option value="">— ruta —</option>{rutas.map((r) => <option key={r.route_id} value={r.route_id}>{r.nombre}</option>)}</select>
        <input placeholder="Código (C10001)" value={add.codigo} onChange={(e) => setAdd({ ...add, codigo: e.target.value })} required />
        <input placeholder="Nombre" value={add.nombre} onChange={(e) => setAdd({ ...add, nombre: e.target.value })} />
        <input placeholder="Zona (prefijo, ej. LV)" value={add.zona} onChange={(e) => setAdd({ ...add, zona: e.target.value })} required />
        <button type="submit">Agregar</button></form>
      <form onSubmit={importar} className="form"><h4>Importar ruta desde un Excel (carpeta 01_Rutas del servidor)</h4>
        <input placeholder="Archivo .xlsx" value={imp.archivo} onChange={(e) => setImp({ ...imp, archivo: e.target.value })} required />
        <input placeholder="ID de ruta (ej. OCCIDENTE)" value={imp.route_id} onChange={(e) => setImp({ ...imp, route_id: e.target.value })} required />
        <input placeholder="Nombre" value={imp.nombre} onChange={(e) => setImp({ ...imp, nombre: e.target.value })} required />
        <input placeholder="Vendedor" list="vend" value={imp.vendedor} onChange={(e) => setImp({ ...imp, vendedor: e.target.value })} required />
        <select value={imp.tipo_ruta} onChange={(e) => setImp({ ...imp, tipo_ruta: e.target.value })}><option value="FORANEA">Foránea (60 d)</option><option value="LOCAL">Local (30 d)</option></select>
        <button type="submit">Importar</button></form>
      <form onSubmit={crear} className="form"><h4>Nueva ruta / actualizar ruta</h4>
        <input placeholder="ID de ruta (ej. OCCIDENTE)" value={f.route_id} onChange={(e) => setF({ ...f, route_id: e.target.value })} required />
        <input placeholder="Nombre" value={f.nombre} onChange={(e) => setF({ ...f, nombre: e.target.value })} required />
        <input placeholder="Vendedor" list="vend" value={f.vendedor} onChange={(e) => setF({ ...f, vendedor: e.target.value })} required />
        <datalist id="vend">{vendedores.map((v) => <option key={v.id} value={v.nombre} />)}</datalist>
        <select value={f.tipo_ruta} onChange={(e) => setF({ ...f, tipo_ruta: e.target.value })}><option value="FORANEA">Foránea (60 d)</option><option value="LOCAL">Local (30 d)</option></select>
        <input placeholder="Plazo en días (opcional)" value={f.plazo_dias} onChange={(e) => setF({ ...f, plazo_dias: e.target.value })} />
        <textarea rows={3} placeholder={'Zonas, una por línea: prefijo, nombre\nSR, Santa Rosa'} value={f.zonas} onChange={(e) => setF({ ...f, zonas: e.target.value })} />
        <textarea rows={6} placeholder={'Clientes en orden de folder, uno por línea: folder, código, nombre\n1 SR, C10001, SALON UNO'} value={f.clientes} onChange={(e) => setF({ ...f, clientes: e.target.value })} />
        <button type="submit">Guardar ruta</button></form>
    </>
  )
}

export function Admin() {
  const [vend, setVend] = useState([])
  const [tab, setTab] = useState('rutas')
  const [nv, setNv] = useState('')
  const cargarVend = useCallback(() => api('/admin/vendedores').then((d) => setVend(d.vendedores)), [])
  useEffect(() => { cargarVend() }, [cargarVend])
  return (
    <>
      <h2>Administración</h2>
      <div className="filtros">
        <button className={tab === 'rutas' ? '' : 'sec'} onClick={() => setTab('rutas')}>Rutas y clientes</button>
        <button className={tab === 'usuarios' ? '' : 'sec'} onClick={() => setTab('usuarios')}>Usuarios</button>
        <button className={tab === 'vendedores' ? '' : 'sec'} onClick={() => setTab('vendedores')}>Vendedores</button>
      </div>
      {tab === 'rutas' && <Rutas vendedores={vend} recargarVendedores={cargarVend} />}
      {tab === 'usuarios' && <Usuarios vendedores={vend} />}
      {tab === 'vendedores' && (
        <>
          <h3>Vendedores</h3>
          <ul className="lista">{vend.map((v) => <li key={v.id}>{v.nombre}</li>)}</ul>
          <form className="form" onSubmit={async (e) => { e.preventDefault(); await post('/admin/vendedores', { nombre: nv }); setNv(''); cargarVend() }}>
            <input placeholder="Nombre del vendedor" value={nv} onChange={(e) => setNv(e.target.value)} required /><button type="submit">Agregar</button>
          </form>
        </>
      )}
    </>
  )
}

export function Auditoria() {
  const [ev, setEv] = useState([])
  useEffect(() => { api('/admin/auditoria?n=200').then((d) => setEv(d.eventos)) }, [])
  return (
    <>
      <h2>Auditoría</h2>
      <div className="tablewrap"><table><thead><tr><th>Fecha (UTC)</th><th>Usuario</th><th>Acción</th><th>Referencia</th><th>Detalle</th></tr></thead>
        <tbody>{ev.map((e) => <tr key={e.id}><td className="small">{e.ts}</td><td>{e.usuario || '—'}<br /><small className="muted">{e.rol}</small></td><td>{e.accion}</td><td>{e.referencia}</td><td className="small">{e.detalle}</td></tr>)}</tbody></table></div>
    </>
  )
}
