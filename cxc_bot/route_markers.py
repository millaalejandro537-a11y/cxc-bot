"""Viñetas digitales de ruta: traduce el resultado del MOTOR UNICO de semaforo (`payment_semaphore`) a un color de viñeta trazable.

No calcula ni reinterpreta nada: el color sale de `ResultadoCliente.color_final`, que ya aplica las reglas vigentes (contado = ROJO automatico,
el sistema prevalece, decisiones de gerencia solo donde el sistema no puede determinar). Este modulo solo decide si ese color puede mostrarse
como DEFINITIVO o debe quedar como estado de revision:

  DEFINITIVA  la fuente esta validada/vigente (`apta_para_publicar`) y el motor dio una clase con color (A, B, C, SOLO_CONTADO, EN_PRUEBA).
  PROVISIONAL la clase tiene color, pero la fuente NO esta validada (snapshot historico, simulada, desactualizada): ningun color definitivo.
  REVISION    el motor dejo al cliente pendiente de gerencia (revision humana / candidato): nunca se inventa un color de credito.
  SIN_DATOS   sin facturas/datos cargados: sin color.

Los hex son los de la leyenda del Excel aprobado de Las Vegas (docs/PLAN-VINETAS-EXCEL.md). Pura: sin E/S."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional

from . import payment_semaphore as ps

HEX = {"BLANCO": "64748B", "VERDE": "34A853", "AZUL": "2563EB", "AMARILLO": "FACC15", "ROJO": "DC2626"}

DEFINITIVA, PROVISIONAL, REVISION, SIN_DATOS = "DEFINITIVA", "PROVISIONAL", "REVISION", "SIN_DATOS"


@dataclass(frozen=True)
class Vineta:
    codigo: str
    estado: str
    color: Optional[str]          # nombre del color del motor (VERDE...) si la clase tiene color; None si pendiente/sin datos
    hex: Optional[str]            # SOLO si estado == DEFINITIVA: unico caso en que se pinta en el Excel
    motivo: str
    clase_final: str
    clase_calculada: str
    decision_aplicada: bool

    def to_dict(self) -> dict:
        return {"estado": self.estado, "color": self.color, "hex": self.hex, "motivo": self.motivo, "clase_final": self.clase_final,
                "clase_calculada": self.clase_calculada, "decision_aplicada": self.decision_aplicada}


def vineta_de(resultado: "ps.ResultadoCliente", fuente_apta: bool) -> Vineta:
    color = ps.COLOR[resultado.final]
    base = dict(codigo=resultado.codigo, clase_final=resultado.final, clase_calculada=resultado.calculado, decision_aplicada=resultado.decision_aplicada)
    if resultado.final == ps.SIN_DATOS:
        return Vineta(estado=SIN_DATOS, color=None, hex=None, motivo="Sin datos cargados para el cliente: sin viñeta.", **base)
    if color not in HEX:   # PENDIENTE_GERENCIA (revision humana / candidato): el motor no determina clase
        return Vineta(estado=REVISION, color=None, hex=None, motivo=f"{ps.ETIQUETA[resultado.final]}: pendiente de gerencia; no se asigna color de crédito.", **base)
    if not fuente_apta:
        return Vineta(estado=PROVISIONAL, color=color, hex=None,
                      motivo=f"{ps.ETIQUETA[resultado.final]} ({color}) provisional: la fuente de datos no está validada para uso operativo.", **base)
    return Vineta(estado=DEFINITIVA, color=color, hex=HEX[color], motivo=f"{ps.ETIQUETA[resultado.final]} ({color}) según el motor de semáforo.", **base)


def plan_vinetas(resultados: Iterable["ps.ResultadoCliente"], fuente_apta: bool) -> Dict[str, Vineta]:
    return {r.codigo.upper(): vineta_de(r, fuente_apta) for r in resultados}


def colores_a_pintar(plan: Mapping[str, Vineta]) -> Dict[str, str]:
    """{codigo: hex} SOLO de las viñetas definitivas (lo unico que el Excel puede mostrar)."""
    return {c: v.hex for c, v in plan.items() if v.estado == DEFINITIVA and v.hex}


def resumen(plan: Mapping[str, Vineta]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for v in plan.values():
        out[v.estado] = out.get(v.estado, 0) + 1
    return out
