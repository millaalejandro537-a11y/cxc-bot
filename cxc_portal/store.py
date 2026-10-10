"""Rutas del portal (solo lectura): clientes en orden de folders + evaluacion con el motor unico `cxc_bot.payment_semaphore`.

Los datos reales (consultas SAP 08/10/2026 y Excel aprobados por gerencia) viven FUERA de Git, en un directorio privado
indicado por --datos o la variable CXC_DATOS_DIR. Sin ese directorio solo se carga la ruta DEMO sintetica.

Tipos de fuente: SNAPSHOT_SAP_HISTORICO (consultas exportadas, NO en vivo), SINTETICO_DEMO y EN_VIVO (aun inexistente).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from cxc_bot import payment_semaphore as ps

from .gerencia_excel import FilaAprobada, leer_aprobado
from cxc_bot.sap_exports import cargar_consultas, cargar_decisiones

FUENTE_SNAPSHOT_SAP = "SNAPSHOT_SAP_HISTORICO"
FUENTE_SINTETICO = "SINTETICO_DEMO"
FUENTE_EN_VIVO = "EN_VIVO"

CORTE_SNAPSHOT = date(2026, 10, 8)
FECHA_DECISIONES = "2026-10-09"
ENV_DATOS = "CXC_DATOS_DIR"


@dataclass
class ClienteRuta:
    folder: str
    zona: str
    codigo: str
    nombre: str
    saldo: float
    resultado: ps.ResultadoCliente
    conciliacion: Optional[dict] = None   # cotejo contra el Excel aprobado por gerencia (si existe)


@dataclass
class Ruta:
    route_id: str
    nombre: str
    vendedor: str
    tipo_ruta: str
    plazo_dias: int
    fuente: dict
    zonas: List[dict]
    clientes: List[ClienteRuta]
    avisos: List[str] = field(default_factory=list)

    @property
    def es_en_vivo(self) -> bool:
        return self.fuente.get("tipo") == FUENTE_EN_VIVO

    def resumen(self) -> dict:
        calc: Dict[str, int] = {}
        final: Dict[str, int] = {}
        for c in self.clientes:
            calc[c.resultado.calculado] = calc.get(c.resultado.calculado, 0) + 1
            final[c.resultado.final] = final.get(c.resultado.final, 0) + 1
        return {
            "route_id": self.route_id, "nombre": self.nombre, "vendedor": self.vendedor, "tipo_ruta": self.tipo_ruta,
            "plazo_dias": self.plazo_dias, "fuente": self.fuente, "clientes": len(self.clientes),
            "saldo_total": round(sum(c.saldo for c in self.clientes), 2),
            "clasificacion": final, "clasificacion_calculada": calc,
            "overrides_gerencia": sum(1 for c in self.clientes if c.resultado.decision_gerencia),
            "alertas_cobro": sum(len(c.resultado.alertas_cobro) for c in self.clientes),
            "alertas_historicas": sum(len(c.resultado.alertas_historicas) for c in self.clientes),
            "avisos": self.avisos,
        }

    def cliente_dict(self, c: ClienteRuta) -> dict:
        s = c.resultado.to_dict()
        s["etiqueta_calculada"] = ps.ETIQUETA[c.resultado.calculado]
        return {"folder": c.folder, "zona": c.zona, "codigo": c.codigo, "nombre": c.nombre, "saldo": c.saldo,
                "ruta": self.route_id, "vendedor": self.vendedor, "plazo_dias": self.plazo_dias, "fuente": self.fuente,
                "conciliacion_gerencia": c.conciliacion, "semaforo": s}


def _decision(f: FilaAprobada) -> Optional[ps.DecisionGerencia]:
    if not f.es_override:
        return None
    return ps.DecisionGerencia(clasificacion_final=f.final, motivo=f.motivo_gerencia or "Decisión de gerencia (motivo no informado)",
                               actor="Gerencia", fecha=FECHA_DECISIONES)


def _conciliar(f: FilaAprobada, r: ps.ResultadoCliente, decision_posterior: bool = False) -> dict:
    """Cotejo motor Python vs Excel aprobado: los conteos y la media deben coincidir; si no, se ve en el portal."""
    difs = []
    pares = [("historial", f.historial, r.historial_total), ("ventana", f.ventana, r.en_ventana), ("pagadas", f.pagadas, r.pagadas),
             ("abiertas", f.abiertas, r.abiertas), ("no acreditadas", f.no_acreditadas, r.no_acreditadas),
             ("evaluables", f.evaluables, r.evaluables), ("suma de mora", f.suma_mora, r.suma_mora), ("alertas > 60", f.alertas, len(r.alertas))]
    for nombre, esp, got in pares:
        if esp is not None and int(esp) != int(got):
            difs.append(f"{nombre}: Excel {esp} vs motor {got}")
    if f.calculado != r.calculado:
        difs.append(f"clasificación calculada: Excel {f.calculado} vs motor {r.calculado}")
    explicadas = []
    if f.final != r.final:
        if decision_posterior:  # el Excel original NO se altera: la decision gerencial posterior se registra aparte
            explicadas.append(f"clasificación final: Excel aprobado {f.final}; decisión posterior de gerencia {r.final} (el Excel original se conserva sin cambios)")
        else:
            difs.append(f"clasificación final: Excel {f.final} vs portal {r.final}")
    return {"coincide": not difs, "diferencias": difs, "explicadas_por_decision_posterior": explicadas}


def construir_ruta_aprobada(*, route_id: str, nombre: str, vendedor: str, plazo_dias: int, tipo_ruta: str, zonas: Dict[str, str],
                            filas: List[FilaAprobada], datos, notas: List[str],
                            decisiones_posteriores: Optional[Dict[str, ps.DecisionGerencia]] = None) -> Ruta:
    clientes: List[ClienteRuta] = []
    avisos: List[str] = []
    for f in filas:
        posterior = (decisiones_posteriores or {}).get(f.codigo)
        dec = posterior or _decision(f)
        r = ps.evaluar_cliente(f.codigo, datos.facturas.get(f.codigo, []), CORTE_SNAPSHOT, plazo_dias, dec)
        conc = _conciliar(f, r, decision_posterior=posterior is not None)
        if not conc["coincide"]:
            avisos.append(f"{f.folder} {f.codigo}: " + "; ".join(conc["diferencias"]))
        clientes.append(ClienteRuta(f.folder, f.zona, f.codigo, datos.nombres.get(f.codigo) or f.nombre,
                                    datos.saldo_abierto.get(f.codigo, 0.0), r, conc))
    fuente = {
        "tipo": FUENTE_SNAPSHOT_SAP, "fecha_corte": CORTE_SNAPSHOT.isoformat(), "en_vivo": False,
        "etiqueta": "Snapshot SAP 08/10/2026 (consultas exportadas) — NO es dato en vivo",
        "notas": ["Facturas, recibos y aplicaciones: consultas SAP 1, 2 y 3 exportadas el 08/10/2026.",
                  "Saldo = facturas abiertas de la consulta 1 al 08/10/2026 (coincide con el saldo de la lista de ruta).",
                  "Decisiones de gerencia reportadas el 09/10/2026; se muestran junto al cálculo automático."] + notas,
    }
    return Ruta(route_id, nombre, vendedor, tipo_ruta, plazo_dias, fuente,
                [{"prefijo": k, "nombre": v} for k, v in zonas.items()], clientes, avisos)


RUTAS_APROBADAS = (
    dict(route_id="LAS_VEGAS", nombre="Las Vegas", vendedor="Erick Brizuela", plazo_dias=ps.PLAZO_FORANEA, tipo_ruta="FORANEA",
         archivo="Las_Vegas_Final_Aprobado_Gerencia_09Oct2026.xlsx",
         zonas={"LV": "Las Vegas", "FB": "Flores de Bijao", "PB": "Peña Blanca", "RL": "Río Lindo", "SC": "San Antonio de Cortés"},
         notas=["Orden de folders: lista física confirmada (24 clientes).", "Estado físico: terminada (24 viñetas colocadas y numeradas)."]),
    dict(route_id="YORO", nombre="Yoro", vendedor="Darwing Milla", plazo_dias=ps.PLAZO_FORANEA, tipo_ruta="FORANEA",
         archivo="Yoro_Semaforo_Aprobacion_Gerencia_09Oct2026.xlsx",
         zonas={"SY": "Santa Rita, Yoro", "MY": "Morazán, Yoro", "PY": "Punta de Ocote", "YT": "Yorito", "YO": "Yoro"},
         notas=["Orden de folders: lista física confirmada (31 clientes: SY 7, MY 7, PY 1, YT 3, YO 13); IARA'S BEAUTY SALON = C10617, sin C10924.",
                "Estado físico: PENDIENTE (≈ 4 de 31 viñetas colocadas); la ruta no está terminada."]),
)


def cargar_rutas_reales(data_dir: Path) -> List[Ruta]:
    rutas_dir = Path(data_dir) / "01_Rutas"
    filas_por_ruta = {cfg["route_id"]: leer_aprobado(rutas_dir / cfg["archivo"], cfg["zonas"]) for cfg in RUTAS_APROBADAS}
    codigos = {f.codigo for filas in filas_por_ruta.values() for f in filas}
    datos = cargar_consultas(Path(data_dir), codigos)
    decisiones = cargar_decisiones(Path(data_dir))
    out = []
    for cfg in RUTAS_APROBADAS:
        out.append(construir_ruta_aprobada(
            route_id=cfg["route_id"], nombre=cfg["nombre"], vendedor=cfg["vendedor"], plazo_dias=cfg["plazo_dias"],
            tipo_ruta=cfg["tipo_ruta"], zonas=cfg["zonas"], filas=filas_por_ruta[cfg["route_id"]], datos=datos, notas=cfg["notas"], decisiones_posteriores=decisiones))
    return out


class RouteStore:
    """Rutas cargadas. `rutas` permite inyectar rutas ya construidas (pruebas); `data_dir` carga las reales."""

    def __init__(self, data_dir: Optional[Path] = None, rutas: Optional[List[Ruta]] = None, include_demo: bool = True):
        self.rutas: Dict[str, Ruta] = {}
        self.errores: List[str] = []
        self.data_dir = Path(data_dir) if data_dir else (Path(os.environ[ENV_DATOS]) if os.environ.get(ENV_DATOS) else None)
        if rutas is not None:
            for r in rutas:
                self.rutas[r.route_id] = r
        elif self.data_dir:
            try:
                for r in cargar_rutas_reales(self.data_dir):
                    self.rutas[r.route_id] = r
            except Exception as e:  # noqa: BLE001 - el portal arranca igual; el error se ve en /api/fuentes
                self.errores.append(f"No se pudieron cargar los datos de {self.data_dir.name}: {e}")
        if include_demo:
            from .demo import demo_ruta
            self.rutas.setdefault("DEMO", demo_ruta())

    def listar(self) -> List[dict]:
        return [r.resumen() for r in self.rutas.values()]

    def ruta(self, route_id: str) -> Optional[Ruta]:
        return self.rutas.get(route_id)

    def buscar_cliente(self, codigo: str):
        k = codigo.strip().upper()
        for r in self.rutas.values():
            for c in r.clientes:
                if c.codigo.upper() == k:
                    return r, c
        return None
