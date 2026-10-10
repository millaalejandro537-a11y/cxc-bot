import React from 'react'

export const money = (n) => `L ${Number(n || 0).toLocaleString('es-HN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
export const fdate = (s) => (s ? s.split('-').reverse().join('/') : '—')

// Color de la viñeta por clasificación final (el cálculo lo hace el backend; aquí solo se presenta)
export const DOT = {
  VERDE: 'var(--verde)', AZUL: 'var(--azul)', AMARILLO: 'var(--amarillo)', ROJO: 'var(--rojo)',
  BLANCO: 'var(--blanco)', PENDIENTE_GERENCIA: 'var(--pend)', SIN_DATOS: 'var(--sin)',
}
export const FUENTE_TXT = {
  SNAPSHOT_SAP_HISTORICO: 'SNAPSHOT SAP HISTÓRICO',
  HISTORICO_REFERENCIA: 'HISTÓRICO · REFERENCIA',
  SINTETICO_DEMO: 'SINTÉTICO · DEMO',
  EN_VIVO: 'EN VIVO',
}
export const ESTADO = { PAGADA: 'Pagada', ABIERTA: 'Abierta', CERRADA_NO_ACREDITADA: 'Cerrada sin cobro acreditado' }
export const CLASES = [
  ['A', 'Excelente (A)'], ['B', 'Bueno (B)'], ['C', 'Regular (C)'], ['SOLO_CONTADO', 'Solo contado'],
  ['CANDIDATO_SOLO_CONTADO', 'Candidato a contado'], ['REVISION_HUMANA', 'Revisión humana'],
  ['EN_PRUEBA', 'En prueba'], ['SIN_DATOS', 'Sin datos'],
]

export function Dot({ color, title }) {
  return <span className="dot" style={{ background: DOT[color] || DOT.SIN_DATOS }} title={title} aria-label={title} />
}

export function FuenteBadge({ fuente }) {
  const live = fuente.tipo === 'EN_VIVO'
  return (
    <span className={`badge ${live ? 'live' : 'hist'}`} title={fuente.etiqueta}>
      {FUENTE_TXT[fuente.tipo] || fuente.tipo} · corte {fdate(fuente.fecha_corte)}
    </span>
  )
}

export function Semaforo({ s }) {
  const cambia = s.calculado !== s.final
  return (
    <span className="sem">
      <Dot color={s.color_final} title={s.etiqueta_final} />
      <span>{s.etiqueta_final}</span>
      {cambia && <small className="muted"> (automático: {s.etiqueta_calculada})</small>}
    </span>
  )
}

