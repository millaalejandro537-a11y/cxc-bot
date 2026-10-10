"""Vistas de ruta del portal: catalogo propio (BD de estado) + evidencia de la fuente + motor unico `payment_semaphore`.

`PortalServicio` es la unica puerta de lectura/escritura de negocio de la aplicacion; la capa HTTP solo valida permisos y
serializa. El semaforo se calcula SIEMPRE con `cxc_bot.payment_semaphore` (sin reglas aqui). Las decisiones de gerencia
vigentes (BD propia) se aplican como override y se exportan a `decisiones_gerencia.json` para que CXC BOT las use.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from cxc_bot import payment_semaphore as ps

from .estado import ErrorNegocio, Estado
from .fuentes import FuenteDatos

ENV_DATOS = "CXC_DATOS_DIR"
ENV_ESTADO = "CXC_ESTADO_DIR"


@dataclass
class ClienteRuta:
    folder: str
    zona: str
    codigo: str
    nombre: str
    saldo: float
    resultado: ps.ResultadoCliente


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
    estado_fisico: str = ""
    notas: str = ""
    avisos: List[str] = field(default_factory=list)

    def resumen(self) -> dict:
        calc: Dict[str, int] = {}
        final: Dict[str, int] = {}
        for c in self.clientes:
            calc[c.resultado.calculado] = calc.get(c.resultado.calculado, 0) + 1
            final[c.resultado.final] = final.get(c.resultado.final, 0) + 1
        return {
            "route_id": self.route_id, "nombre": self.nombre, "vendedor": self.vendedor, "tipo_ruta": self.tipo_ruta,
            "plazo_dias": self.plazo_dias, "fuente": self.fuente, "clientes": len(self.clientes),
            "saldo_total": round(sum(c.saldo for c in self.clientes), 2), "clasificacion": final, "clasificacion_calculada": calc,
            "overrides_gerencia": sum(1 for c in self.clientes if c.resultado.decision_gerencia),
            "alertas_cobro": sum(len(c.resultado.alertas_cobro) for c in self.clientes),
            "alertas_historicas": sum(len(c.resultado.alertas_historicas) for c in self.clientes),
            "estado_fisico": self.estado_fisico, "notas": self.notas, "avisos": self.avisos,
        }

    def cliente_dict(self, c: ClienteRuta) -> dict:
        s = c.resultado.to_dict()
        s["etiqueta_calculada"] = ps.ETIQUETA[c.resultado.calculado]
        return {"folder": c.folder, "zona": c.zona, "codigo": c.codigo, "nombre": c.nombre, "saldo": c.saldo, "ruta": self.route_id,
                "vendedor": self.vendedor, "plazo_dias": self.plazo_dias, "fuente": self.fuente, "semaforo": s}


def decision_a_motor(d: dict) -> ps.DecisionGerencia:
    return ps.DecisionGerencia(clasificacion_final=d["clasificacion_final"], motivo=d["motivo"], actor=d["actor"], fecha=d["fecha"])


class PortalServicio:
    def __init__(self, estado: Estado, fuente: Optional[FuenteDatos], estado_dir: Path, reporting=None, data_dir: Optional[Path] = None):
        self.estado, self.fuente, self.estado_dir = estado, fuente, Path(estado_dir)
        self.reporting, self.data_dir = reporting, (Path(data_dir) if data_dir else None)
        self._cache: Optional[Dict[str, Ruta]] = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ fuente
    def info_fuente(self) -> dict:
        if self.fuente is None:
            return {"tipo": "SIN_FUENTE", "en_vivo": False, "etiqueta": "Sin fuente de datos cargada (solo catálogo)", "fecha_corte": None,
                    "frescura": {"vigente": False, "motivo": "No hay snapshot ni fuente en vivo configurados."}}
        return {"tipo": self.fuente.tipo, "en_vivo": self.fuente.en_vivo, "etiqueta": self.fuente.etiqueta,
                "fecha_corte": self.fuente.fecha_corte.isoformat(), "frescura": self.fuente.frescura()}

    def capacidades(self) -> dict:
        """Que se puede usar hoy y que queda bloqueado hasta tener datos vigentes y autorización."""
        vivo = bool(self.fuente and self.fuente.en_vivo)
        return {
            "sin_sap_vivo": ["Administrar vendedores, rutas, zonas, clientes y orden de folders", "Alta de rutas nuevas sin cambiar código",
                             "Semáforo, facturas y alertas sobre el snapshot (con fecha de corte)", "Decisiones y excepciones de gerencia (con trazabilidad)",
                             "Registro de visitas y su trazabilidad", "Reportes gerenciales (cxc-reporting-core) sobre el snapshot, marcados NO VALIDADOS",
                             "Usuarios, roles y auditoría"],
            "bloqueado_hasta_sap_vivo": [] if vivo else [
                "Saldos y facturas actuales (hoy: snapshot del 08/10/2026)", "Alertas de cobro que reflejen la realidad de hoy",
                "Prioridades de cobro confiables", "Cortes VALIDADOS de cartera (requiere conciliación contable)",
                "Generar rutas con saldos vigentes para uso operativo"],
            "en_vivo": vivo}

    # ------------------------------------------------------------------ rutas evaluadas
    def invalidar(self) -> None:
        with self._lock:
            self._cache = None

    def _evaluar(self) -> Dict[str, Ruta]:
        decisiones = {c: decision_a_motor(d) for c, d in self.estado.decisiones_vigentes().items()}
        info = self.info_fuente()
        fuente_meta = {"tipo": info["tipo"], "fecha_corte": info["fecha_corte"], "en_vivo": info["en_vivo"], "etiqueta": info["etiqueta"], "notas": []}
        usable = self.fuente is not None and not self.fuente.en_vivo
        out: Dict[str, Ruta] = {}
        for r in self.estado.listar_rutas():
            codigos = [c["codigo"] for c in r["clientes"]]
            resultados = ps.evaluar_ruta(codigos, self.fuente, r["plazo_dias"], decisiones) if usable else {}
            clientes = []
            for c in r["clientes"]:
                res = resultados.get(c["codigo"])
                if res is None:  # sin fuente: no se infiere nada
                    res = ps.ResultadoCliente(c["codigo"], ps.SIN_DATOS, ps.SIN_DATOS, None, 0, 0, 0, 0, 0, 0, 0, (), (),
                                              ("No hay fuente de datos cargada: no se calcula ni se infiere ninguna clasificación.",), (), None)
                saldo = self.fuente.saldo_abierto(c["codigo"]) if usable else 0.0
                nombre = (self.fuente.nombre_cliente(c["codigo"]) if usable else None) or c["nombre"]
                clientes.append(ClienteRuta(c["folder"], c["zona"], c["codigo"], nombre, saldo, res))
            out[r["route_id"]] = Ruta(r["route_id"], r["nombre"], r["vendedor"], r["tipo_ruta"], r["plazo_dias"], fuente_meta, r["zonas"], clientes,
                                      r["estado_fisico"], r["notas"])
        return out

    def rutas(self) -> Dict[str, Ruta]:
        with self._lock:
            if self._cache is None:
                self._cache = self._evaluar()
            return self._cache

    def ruta(self, route_id: str) -> Optional[Ruta]:
        return self.rutas().get(route_id)

    def buscar_cliente(self, codigo: str):
        k = codigo.strip().upper()
        for r in self.rutas().values():
            for c in r.clientes:
                if c.codigo.upper() == k:
                    return r, c
        return None

    # ------------------------------------------------------------------ decisiones
    def registrar_decision(self, actor: dict, codigo: str, final: str, motivo: str) -> dict:
        hit = self.buscar_cliente(codigo)
        if not hit:
            raise ErrorNegocio("El cliente no pertenece a ninguna ruta.", 404)
        r, c = hit
        s = c.resultado
        d = self.estado.registrar_decision(actor, c.codigo, final, motivo, route_id=r.route_id, calculado=s.calculado, promedio=s.promedio_mora,
                                           fecha_corte=r.fuente.get("fecha_corte"))
        self.invalidar()
        self.exportar_decisiones()
        return d

    def anular_decision(self, actor: dict, codigo: str, motivo: str) -> dict:
        d = self.estado.anular_decision(actor, codigo, motivo)
        self.invalidar()
        self.exportar_decisiones()
        return d

    def exportar_decisiones(self) -> Optional[Path]:
        """Exporta las decisiones VIGENTES a decisiones_gerencia.json (lo consume el generador de rutas de CXC BOT). Escritura atomica."""
        destino = self.data_dir / "decisiones_gerencia.json" if self.data_dir else None
        if destino is None or not destino.parent.is_dir():
            return None
        data = {"_nota": "Exportado desde la BD del portal (fuente de verdad). No editar a mano."}
        for cod, d in sorted(self.estado.decisiones_vigentes().items()):
            data[cod] = {"clasificacion_final": d["clasificacion_final"], "motivo": d["motivo"], "actor": d["actor"], "fecha": d["fecha"]}
        tmp = destino.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, destino)
        return destino

    # ------------------------------------------------------------------ importacion de rutas desde Excel (solo lectura del archivo)
    def importar_ruta_desde_excel(self, actor: dict, archivo: str, route_id: str, nombre: str, vendedor: str, tipo_ruta: str,
                                  plazo_dias: Optional[int] = None, zonas: Optional[Dict[str, str]] = None) -> dict:
        """Lee la lista (Folder/#, Codigo, Nombre) de un Excel que esté DENTRO de <datos>/01_Rutas y crea/actualiza la ruta. No modifica el Excel."""
        if self.data_dir is None:
            raise ErrorNegocio("No hay directorio de datos configurado.", 400)
        base = (self.data_dir / "01_Rutas").resolve()
        origen = (base / archivo).resolve()
        if base not in origen.parents or origen.suffix.lower() != ".xlsx" or not origen.is_file():
            raise ErrorNegocio("Archivo no válido: debe ser un .xlsx dentro de 01_Rutas del directorio de datos.", 400)
        from .rutas_excel import leer_lista_ruta
        filas = leer_lista_ruta(origen)
        if not filas:
            raise ErrorNegocio("No se encontraron clientes en el archivo (se espera encabezado con Codigo y Nombre).", 400)
        r = self.estado.guardar_ruta(actor, route_id, nombre, vendedor, tipo_ruta, plazo_dias, zonas=zonas or {}, clientes=filas)
        self.invalidar()
        return r
