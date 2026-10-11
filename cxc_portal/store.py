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
    requiere_hojas_visita: bool = False
    implantacion: str = "OPERATIVA"

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
            "requiere_hojas_visita": self.requiere_hojas_visita, "implantacion": self.implantacion,
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
        self._version = -1
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
                                      r["estado_fisico"], r["notas"], requiere_hojas_visita=bool(r["requiere_hojas_visita"]), implantacion=r["implantacion"])
        return out

    def rutas(self) -> Dict[str, Ruta]:
        # La cache se invalida sola si OTRO proceso (CLI admin, integrar-ruta) cambia el catalogo o las decisiones en la BD compartida.
        v = self.estado.version_catalogo()
        with self._lock:
            if self._cache is None or v != self._version:
                self._cache = self._evaluar()
                self._version = v
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

    # ------------------------------------------------------------------ Excel de ruta (generador de CXC BOT)
    def _config_excel(self, r: dict):
        from cxc_bot.config import RouteNotFoundError, load_route_config
        for nombre in filter(None, (r.get("plantilla_excel"), r["route_id"], r["route_id"].replace("_", " "), r["nombre"].upper(), r["nombre"])):
            try:
                return load_route_config(nombre)
            except RouteNotFoundError:
                continue
        raise ErrorNegocio("La ruta no tiene plantilla Excel configurada en config/routes.json (hoja principal, fila de encabezado, columnas).", 409)

    def bases_excel(self) -> List[str]:
        if self.data_dir is None or not (self.data_dir / "01_Rutas").is_dir():
            return []
        return sorted(p.name for p in (self.data_dir / "01_Rutas").glob("*.xlsx"))

    def generar_excel(self, actor: dict, route_id: str, base: str, fecha_salida: str):
        """Genera el Excel de la ruta con el MISMO generador de CXC BOT (formato operativo intacto): roster y folders desde la BD propia,
        hoja de datos SAP desde la fuente actual. Es un BORRADOR mientras la fuente sea un snapshot. No modifica el archivo base."""
        import shutil
        import tempfile
        from datetime import datetime

        import openpyxl

        from cxc_bot import excel_service, validators
        from cxc_bot.models import ClientEntry, SapClientRecord

        r = self.estado.obtener_ruta(route_id)
        if not r or not r["activa"]:
            raise ErrorNegocio("Ruta no encontrada.", 404)
        if self.fuente is None or self.fuente.en_vivo:
            raise ErrorNegocio("No hay un snapshot utilizable para llenar la hoja de datos SAP.", 409)
        if self.data_dir is None:
            raise ErrorNegocio("No hay directorio de datos configurado.", 400)
        try:
            fecha = datetime.strptime(fecha_salida, "%Y-%m-%d").date()
        except ValueError as e:
            raise ErrorNegocio("fecha_salida debe ser AAAA-MM-DD.") from e
        carpeta = (self.data_dir / "01_Rutas").resolve()
        origen = (carpeta / base).resolve()
        if carpeta not in origen.parents or origen.suffix.lower() != ".xlsx" or not origen.is_file():
            raise ErrorNegocio("Archivo base no válido: debe ser un .xlsx dentro de 01_Rutas del directorio de datos.", 400)
        config = self._config_excel(r)
        registros = [SapClientRecord(codigo=c, nombre=n, saldo=float(sal)) for c, n, sal in self.fuente.registros_sap()]
        nombres_sap = {x.codigo.upper(): " ".join(x.nombre.upper().split()) for x in registros}
        # Si el nombre de la ruta difiere del registrado en SAP, se conserva como nombre propio de la ruta (literal), no la formula.
        roster = [ClientEntry(codigo=c["codigo"], folder=c["folder"],
                              nombre_ruta=(c["nombre"] if c["nombre"] and " ".join(c["nombre"].upper().split()) != nombres_sap.get(c["codigo"].upper(), "") else None))
                  for c in r["clientes"]]
        with tempfile.TemporaryDirectory(prefix="cxc_xlsx_") as tmp:
            tmp = Path(tmp)
            # Formato operativo = SOLO las dos hojas (portada + datos SAP): se descartan hojas de trabajo/borrador EN LA COPIA.
            wb = openpyxl.load_workbook(origen)
            for nombre in list(wb.sheetnames):
                if nombre not in (config.main_sheet, config.data_sheet):
                    del wb[nombre]
            base_tmp = tmp / "base.xlsx"
            wb.save(base_tmp)
            salida = tmp / "salida.xlsx"
            try:
                excel_service.generate_route(config, base_tmp, salida, fecha, registros, override_roster=roster)
            except excel_service.ExcelServiceError as e:
                raise ErrorNegocio(f"No se pudo generar el Excel: {e}", 409) from e
            literales = {e.codigo.upper(): e.nombre_ruta for e in roster if e.nombre_ruta}
            rep = validators.validate_workbook(salida, config, expected_date=fecha, expected_client_count=len(roster), folder_mode=True, nombres_literales=literales)
            if not rep.ok:
                raise ErrorNegocio("El Excel generado no pasó la validación: " + "; ".join(rep.errors[:5]), 409)
            fuente_f = (self.fuente.fecha_corte.isoformat() if self.fuente else "sin-fuente")
            nombre = f"{r['route_id']}_{fecha.isoformat()}_BORRADOR_snapshot_{fuente_f}.xlsx"
            salidas = self.estado_dir / "salidas"
            salidas.mkdir(parents=True, exist_ok=True)
            shutil.copy2(salida, salidas / nombre)
            datos = salida.read_bytes()
        with self.estado.tx() as c:
            self.estado.auditar(c, actor, "excel.generar", "ruta", route_id, {"base": base, "fecha_salida": fecha_salida, "archivo": nombre, "clientes": len(roster)})
        return datos, nombre, rep.warnings

    # ------------------------------------------------------------------ candidatos: busqueda en el snapshot (solo SUGIERE; la ruta la confirma una persona)
    @staticmethod
    def _norm(t: str) -> str:
        import unicodedata
        t = unicodedata.normalize("NFKD", t or "")
        return " ".join("".join(c for c in t if not unicodedata.combining(c)).upper().split())

    def buscar_candidatos(self, palabras: List[str], route_id: str, limite: int = 200) -> List[dict]:
        """Clientes de la fuente cuyo nombre contiene alguna palabra (p. ej. el nombre de una zona). NO modifica nada: devuelve sugerencias
        con la ruta a la que ya pertenecen (si alguna) para que una persona decida."""
        if self.fuente is None or self.fuente.en_vivo:
            raise ErrorNegocio("No hay un snapshot utilizable para buscar clientes.", 409)
        claves = [self._norm(p) for p in palabras if self._norm(p)]
        if not claves:
            raise ErrorNegocio("Indique al menos una palabra de búsqueda (p. ej. el nombre de la zona).")
        asignado = {}
        for r in self.estado.listar_rutas(solo_activas=False):
            for c in r["clientes"]:
                asignado.setdefault(c["codigo"], r["route_id"])
        propuestos = {c["codigo"]: c["estado"] for c in self.estado.listar_candidatos(route_id)}
        out = []
        for cod, nombre, saldo in self.fuente.registros_sap():
            n = self._norm(nombre)
            if any(k in n for k in claves):
                out.append({"codigo": cod, "nombre": nombre, "saldo": round(float(saldo), 2), "ya_en_ruta": asignado.get(cod),
                            "candidato": propuestos.get(cod)})
                if len(out) >= limite:
                    break
        return out

    # ------------------------------------------------------------------ preparacion de una salida: actualizacion y revision de excepciones
    def preparar_salida(self, actor: dict, sid: int) -> dict:
        sal = self.estado.obtener_salida(sid)
        if not sal:
            raise ErrorNegocio("Salida no encontrada.", 404)
        ruta = self.ruta(sal["route_id"])
        if ruta is None or ruta.implantacion != "OPERATIVA" or not ruta.clientes:
            raise ErrorNegocio("La ruta no está operativa o no tiene clientes confirmados.", 409)
        previo = self.estado.resumen_salida_anterior(sal["route_id"], sal["fecha_salida"])
        resumen, ex = [], {"candidatos_pendientes": [], "sin_datos": [], "pendientes_gerencia": [], "alertas_cobro": [], "cambios_de_clasificacion": [],
                           "clientes_nuevos": [], "clientes_retirados": [], "omisiones": None, "advertencias": []}
        for c in ruta.clientes:
            r = c.resultado
            resumen.append({"codigo": c.codigo, "folder": c.folder, "calculado": r.calculado, "final": r.final, "promedio_mora": r.promedio_mora, "saldo": c.saldo})
            if r.final == "SIN_DATOS":
                ex["sin_datos"].append({"codigo": c.codigo, "folder": c.folder, "nombre": c.nombre})
            if r.requiere_gerencia:
                ex["pendientes_gerencia"].append({"codigo": c.codigo, "folder": c.folder, "nombre": c.nombre, "calculado": r.calculado})
            for a in r.alertas_cobro:
                ex["alertas_cobro"].append({"codigo": c.codigo, "folder": c.folder, "nombre": c.nombre, "factura": a.numero, "mora": a.mora, "saldo_pendiente": a.saldo_pendiente})
            if previo:
                p0 = previo.get(c.codigo)
                if p0 is None:
                    ex["clientes_nuevos"].append({"codigo": c.codigo, "folder": c.folder, "nombre": c.nombre})
                elif p0["final"] != r.final:
                    ex["cambios_de_clasificacion"].append({"codigo": c.codigo, "folder": c.folder, "nombre": c.nombre, "antes": p0["final"], "ahora": r.final})
        if previo:
            actuales = {c.codigo for c in ruta.clientes}
            ex["clientes_retirados"] = [{"codigo": k, "folder": v["folder"]} for k, v in previo.items() if k not in actuales]
        ex["candidatos_pendientes"] = [{"id": c["id"], "codigo": c["codigo"], "nombre": c["nombre"]} for c in self.estado.listar_candidatos(sal["route_id"], "propuesta")]
        if ruta.requiere_hojas_visita:
            ex["omisiones"] = self.estado.omisiones_consecutivas(sal["route_id"], 3)
        if not (self.fuente and self.fuente.en_vivo):
            ex["advertencias"].append(f"Datos del snapshot {ruta.fuente.get('fecha_corte')}, NO en vivo: saldos y alertas de cobro pueden estar desactualizados.")
        total = sum(len(v) for k, v in ex.items() if isinstance(v, list) and k != "advertencias")
        if ex["omisiones"]:
            total += len(ex["omisiones"]["omitidas_confirmadas"]) + len(ex["omisiones"]["sin_registro_en_salidas"])
        reporte = {**ex, "total_excepciones": total, "requiere_revision": total > 0, "fecha_corte_datos": ruta.fuente.get("fecha_corte")}
        return self.estado.guardar_preparacion(actor, sid, resumen, reporte)

    # ------------------------------------------------------------------ documentos reutilizables (hojas de visita)
    def _carpeta_docs(self) -> Path:
        return self.estado_dir / "documentos"

    def importar_documento(self, actor: dict, route_id: str, archivo: str, motivo: str = "", nueva_version: bool = False, confirmado: bool = False) -> dict:
        """Registra (v1) o versiona un documento de hojas de visita leyendo un .xlsx de <datos>/01_Rutas. El original se conserva intacto y el
        almacenado queda en la carpeta PRIVADA de estado (fuera de Git y del frontend publico)."""
        from .hojas_visita_excel import leer_hojas_visita
        if self.data_dir is None:
            raise ErrorNegocio("No hay directorio de datos configurado.", 400)
        base = (self.data_dir / "01_Rutas").resolve()
        origen = (base / archivo).resolve()
        if base not in origen.parents or origen.suffix.lower() != ".xlsx" or not origen.is_file():
            raise ErrorNegocio("Archivo no válido: debe ser un .xlsx dentro de 01_Rutas del directorio de datos.", 400)
        try:
            contenido = leer_hojas_visita(origen)
        except Exception as e:  # noqa: BLE001
            raise ErrorNegocio(f"No se pudo leer el documento: {type(e).__name__}", 400) from e
        if not contenido["total_clientes"]:
            raise ErrorNegocio("El documento no contiene clientes reconocibles (se espera COLOR / CÓDIGO SAP / SALÓN-CLIENTE).", 400)
        return self.estado.registrar_documento(actor, route_id, "HOJA_VISITA", origen.name, origen.read_bytes(), contenido, self._carpeta_docs(),
                                               motivo, nueva_version, confirmado)

    def bytes_documento(self, did: int):
        d = self.estado.obtener_documento(did)
        if not d:
            raise ErrorNegocio("Documento no encontrado.", 404)
        ruta = (self._carpeta_docs() / d["almacenado"]).resolve()
        if self._carpeta_docs().resolve() not in ruta.parents or not ruta.is_file():
            raise ErrorNegocio("Archivo del documento no disponible.", 404)
        datos = ruta.read_bytes()
        import hashlib
        if hashlib.sha256(datos).hexdigest() != d["sha256"]:
            raise ErrorNegocio("El archivo almacenado no coincide con su hash registrado (integridad).", 500)
        return d, datos

    def proponer_desde_documento(self, actor: dict, route_id: str, did: int) -> List[dict]:
        """Propone (NO confirma) los clientes del documento como candidatos de la ruta, en el orden del documento. La confirmacion es humana."""
        d = self.estado.obtener_documento(did)
        if not d or d["route_id"] != route_id:
            raise ErrorNegocio("Documento no encontrado.", 404)
        items = [{"codigo": c["codigo"], "nombre": c["nombre"], "zona": ""} for h in d["contenido"]["hojas"] for c in h["clientes"]]
        return self.estado.proponer_candidatos(actor, route_id, items, f"documento v{d['version']}")
