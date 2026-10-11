"""Flujo operativo de una SALIDA, de punta a punta, sobre el motor unico de semaforo y la BD propia:

    programar (solo dias habiles lunes-sabado, feriados configurables)
      -> preparar 3 dias habiles antes (automatico e idempotente) -> evaluacion persistida por salida
      -> alertas deduplicadas + notificaciones SIMULADAS
      -> revision humana de excepciones -> Excel de ruta con versiones Actual/Historico (bloqueado hasta revisar)
      -> hoja de visita (documento reutilizable) -> seguimiento de visitas, cobros y ventas -> comparacion entre salidas.

No hay reglas de semaforo aqui (las pone `cxc_bot.payment_semaphore`). Nada sale del sistema: el unico notificador registra envios simulados.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol

from cxc_bot import payment_semaphore as ps
from cxc_bot.business_calendar import BusinessCalendar, load_business_calendar, load_preparation_lead_days

from .estado import ErrorNegocio

ACTOR_SISTEMA = {"id": None, "usuario": "sistema", "rol": "admin", "nombre": "Proceso automático"}
UMBRAL_AUMENTO_MORA = 5          # SUPUESTO (pendiente de confirmar): +5 dias de mora media entre salidas = alerta (la tolerancia A es 5)
ENVIO_EXTERNO_HABILITADO = False  # candado: ningun notificador puede enviar fuera del sistema mientras esto sea False
ORDEN_CLASE = {ps.A: 1, ps.B: 2, ps.C: 3, ps.CANDIDATO_SOLO_CONTADO: 4, ps.SOLO_CONTADO: 4}


def _fecha(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError) as e:
        raise ErrorNegocio("Las fechas deben ser AAAA-MM-DD.") from e


class Notificador(Protocol):
    def enviar(self, notificacion: dict) -> str: ...


class NotificadorSimulado:
    """Registra el envio como SIMULADO. No contacta a nadie: no hay correo, WhatsApp ni red."""

    def enviar(self, notificacion: dict) -> str:
        assert not ENVIO_EXTERNO_HABILITADO
        return f"SIMULADO: no se envió ningún mensaje a '{notificacion['destinatario']}'."


def huella_excel(datos: bytes) -> str:
    """Huella del CONTENIDO (valores/formulas, formatos, rellenos, bordes, anchos), independiente de metadatos que cambian en cada guardado."""
    import io

    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(datos))
    canon = []
    for ws in wb.worksheets:
        celdas = []
        for fila in ws.iter_rows():
            for c in fila:
                if c.value is None and not c.has_style:
                    continue
                b = c.border
                celdas.append([c.coordinate, c.value if not isinstance(c.value, (datetime, date)) else str(c.value), c.number_format,
                               c.fill.fgColor.rgb if c.fill and c.fill.fill_type else None,
                               [b.left.style, b.right.style, b.top.style, b.bottom.style], bool(c.font.b)])
        canon.append([ws.title, ws.max_row, ws.max_column, sorted((k, round(v.width or 0, 2)) for k, v in ws.column_dimensions.items()), celdas])
    return hashlib.sha256(json.dumps(canon, sort_keys=True, default=str).encode("utf-8")).hexdigest()


class FlujoOperativo:
    def __init__(self, servicio, notificador: Optional[Notificador] = None, hoy: Optional[Callable[[], date]] = None, config_scheduling: Optional[Path] = None):
        self.servicio, self.estado = servicio, servicio.estado
        self.notificador = notificador or NotificadorSimulado()
        self._hoy = hoy
        self._cfg = config_scheduling

    # ------------------------------------------------------------------ calendario
    def calendario_habil(self) -> BusinessCalendar:
        """Lunes a sabado habiles (domingo no); feriados = los del archivo de configuracion + los registrados en el portal."""
        kw = {"path": self._cfg} if self._cfg else {}
        base = load_business_calendar(**kw)
        feriados = {_fecha(f["fecha"]) for f in self.estado.listar_feriados()}
        return BusinessCalendar(non_business_weekdays=base.non_business_weekdays, holidays=set(base.holidays) | feriados, timezone=base.timezone)

    def anticipacion(self) -> int:
        return load_preparation_lead_days(**({"path": self._cfg} if self._cfg else {}))

    def hoy(self) -> date:
        if self._hoy:
            return self._hoy()
        from cxc_bot.business_calendar import today_in_timezone
        return today_in_timezone(self.calendario_habil().timezone)

    def fecha_preparacion(self, fecha_salida: str) -> date:
        return self.calendario_habil().subtract_business_days(_fecha(fecha_salida), self.anticipacion())

    def validar_fecha_salida(self, fecha_salida: str) -> None:
        d = _fecha(fecha_salida)
        cal = self.calendario_habil()
        if not cal.is_business_day(d):
            motivo = "domingo" if d.weekday() in cal.non_business_weekdays else "feriado"
            raise ErrorNegocio(f"{fecha_salida} no es día hábil ({motivo}): las salidas se programan de lunes a sábado, sin feriados.")

    def programar_salidas(self, actor: dict, route_id: str, fechas: List[str], fecha_regreso_dias: Optional[int] = None) -> dict:
        """Crea varias salidas de una ruta (idempotente: las que ya existen se reportan, no se duplican; fechas no habiles se rechazan)."""
        creadas, existentes, rechazadas = [], [], []
        existentes_f = {s["fecha_salida"] for s in self.estado.listar_salidas(route_id)}
        for f in sorted(set(fechas)):
            try:
                self.validar_fecha_salida(f)
                if f in existentes_f:
                    existentes.append(f)
                    continue
                s = self.estado.crear_salida(actor, route_id, f)
                creadas.append({"id": s["id"], "fecha_salida": f, "fecha_preparacion": self.fecha_preparacion(f).isoformat()})
            except ErrorNegocio as e:
                rechazadas.append({"fecha": f, "motivo": e.mensaje})
        return {"ruta": route_id, "creadas": creadas, "existentes": existentes, "rechazadas": rechazadas}

    def estado_preparacion(self, s: dict, hoy: date) -> str:
        if s["estado"] in ("cancelada", "cerrada"):
            return s["estado"]
        if s.get("preparada_en"):
            return "revisada" if s.get("revisada_por") else "preparada"
        fs = _fecha(s["fecha_salida"])
        if hoy > fs:
            return "atrasada"
        return "por_preparar" if hoy >= self.fecha_preparacion(s["fecha_salida"]) else "futura"

    def calendario_salidas(self, rutas_visibles: Dict[str, object], desde: Optional[str] = None, hasta: Optional[str] = None, route_id: str = "",
                           vendedor: str = "", periodo: str = "") -> dict:
        hoy = self.hoy()
        filas = []
        for s in self.estado.listar_salidas(route_id or None):
            r = rutas_visibles.get(s["route_id"])
            if r is None or (vendedor and r.vendedor.lower() != vendedor.lower()):
                continue
            if (desde and s["fecha_salida"] < desde) or (hasta and s["fecha_salida"] > hasta) or (periodo and not s["fecha_salida"].startswith(periodo)):
                continue
            filas.append({"id": s["id"], "ruta": s["route_id"], "ruta_nombre": r.nombre, "vendedor": r.vendedor, "fecha_salida": s["fecha_salida"],
                          "periodo": s["fecha_salida"][:7], "fecha_preparacion": self.fecha_preparacion(s["fecha_salida"]).isoformat(),
                          "estado": s["estado"], "preparacion": self.estado_preparacion(s, hoy), "excepciones": (s.get("reporte_excepciones") or {}).get("total_excepciones"),
                          "revisada": bool(s.get("revisada_por"))})
        filas.sort(key=lambda x: (x["fecha_salida"], x["ruta"]))
        return {"hoy": hoy.isoformat(), "anticipacion_dias_habiles": self.anticipacion(), "salidas": filas,
                "feriados": self.estado.listar_feriados(), "semana_laboral": "lunes a sábado"}

    # ------------------------------------------------------------------ preparacion + alertas + notificaciones
    def preparar(self, actor: dict, sid: int) -> dict:
        sal = self.servicio.preparar_salida(actor, sid)
        self.estado.cerrar_alertas(sid, ("PREPARACION_ATRASADA", "PREPARACION_FALLIDA"), "Resuelto: la salida ya fue preparada.")
        self.generar_alertas(sal)
        self.encolar_aviso_preparacion(sal)
        return sal

    def generar_alertas(self, sal: dict) -> int:
        """Alertas deduplicadas a partir del reporte de excepciones y de la comparacion con la salida anterior. NO decide nada: solo avisa."""
        rep, sid, rid = sal["reporte_excepciones"] or {}, sal["id"], sal["route_id"]
        nuevas = 0
        def a(clave, tipo, sev, titulo, cod=None, det=None):
            nonlocal nuevas
            nuevas += self.estado.registrar_alerta(f"{tipo}|{sid}|{clave}", tipo, sev, titulo, rid, sid, cod, det)
        for x in rep.get("cambios_de_clasificacion", []):
            r0, r1 = ORDEN_CLASE.get(x["antes"]), ORDEN_CLASE.get(x["ahora"])
            empeora = r0 is not None and r1 is not None and r1 > r0
            a(f"{x['codigo']}|{x['antes']}>{x['ahora']}", "CAMBIO_CATEGORIA", "alta" if empeora else "media",
              f"{x['folder']} {x['nombre']}: cambió de {x['antes']} a {x['ahora']}" + (" (empeora)" if empeora else ""), x["codigo"], x)
        previo = self.estado.resumen_salida_anterior(rid, sal["fecha_salida"])
        with self.estado.tx() as c:
            actual = {r["codigo"]: dict(r) for r in c.execute("SELECT * FROM salida_resumen WHERE salida_id = ?", (sid,))}
        for cod, y in actual.items():
            x = previo.get(cod)
            if x and x["promedio_mora"] is not None and y["promedio_mora"] is not None and y["promedio_mora"] - x["promedio_mora"] >= UMBRAL_AUMENTO_MORA:
                a(f"{cod}", "AUMENTO_MORA", "media", f"{y['folder']}: la mora media subió de {x['promedio_mora']} a {y['promedio_mora']} días entre salidas", cod,
                  {"antes": x["promedio_mora"], "ahora": y["promedio_mora"], "umbral_supuesto": UMBRAL_AUMENTO_MORA})
        for x in rep.get("sin_datos", []):
            a(x["codigo"], "SIN_EVIDENCIA", "media", f"{x['folder']} {x['nombre']}: sin evidencia suficiente para clasificar", x["codigo"], x)
        for x in rep.get("pendientes_gerencia", []):
            que = "candidato a solo contado" if x["calculado"] == ps.CANDIDATO_SOLO_CONTADO else f"requiere revisión ({x['calculado']})"
            a(x["codigo"], "CANDIDATO_PENDIENTE_GERENCIA", "alta",
              f"{x['folder']} {x['nombre']}: {que}; requiere decisión de gerencia (no se aplica automáticamente)", x["codigo"], x)
        for x in rep.get("alertas_cobro", []):
            a(f"{x['codigo']}|{x['factura']}", "COBRO_MORA_ABIERTA", "media",
              f"{x['folder']} {x['nombre']}: factura {x['factura']} abierta con {x['mora']} días de mora", x["codigo"], x)
        for x in rep.get("clientes_nuevos", []):
            a(x["codigo"], "CLIENTE_NUEVO", "info", f"{x['folder']} {x['nombre']}: cliente nuevo en la ruta", x["codigo"], x)
        for x in rep.get("clientes_retirados", []):
            a(x["codigo"], "CLIENTE_RETIRADO", "info", f"{x['folder']}: cliente retirado de la ruta", x["codigo"], x)
        om = rep.get("omisiones") or {}
        for x in om.get("omitidas_confirmadas", []):
            a(x["codigo"], "OMISION_3_SALIDAS", "alta", f"{x['folder']} {x['nombre']}: 3 salidas consecutivas con visita confirmada como NO realizada", x["codigo"], x)
        for x in om.get("sin_registro_en_salidas", []):
            a(x["codigo"], "SIN_REGISTRO_VISITA", "media", f"{x['folder']} {x['nombre']}: sin registro de visita en salidas recientes (revisar; la falta de firma no prueba que no se visitó)", x["codigo"], x)
        if rep.get("candidatos_pendientes"):
            a("candidatos", "CANDIDATOS_SIN_CONFIRMAR", "media", f"{len(rep['candidatos_pendientes'])} cliente(s) propuesto(s) sin confirmar o rechazar", None,
              {"codigos": [c["codigo"] for c in rep["candidatos_pendientes"]]})
        for adv in rep.get("advertencias", []):
            a("datos", "DATOS_NO_VIVOS", "info", adv)
        return nuevas

    def encolar_aviso_preparacion(self, sal: dict) -> int:
        rep = sal["reporte_excepciones"] or {}
        ruta = self.servicio.ruta(sal["route_id"])
        altas = sum(1 for x in self.estado.listar_alertas([sal["route_id"]], salida_id=sal["id"]) if x["severidad"] == "alta")
        asunto = f"Salida {sal['fecha_salida']} · {ruta.nombre if ruta else sal['route_id']}: preparada"
        cuerpo = (f"La salida del {sal['fecha_salida']} de la ruta {ruta.nombre if ruta else sal['route_id']} fue preparada. "
                  f"Excepciones: {rep.get('total_excepciones', 0)}; alertas de severidad alta: {altas}. "
                  "Datos del snapshot, no en vivo. (Mensaje SIMULADO: no sale del sistema.)")
        n = 0
        for u in self.estado.listar_usuarios():
            if not u["activo"]:
                continue
            if u["rol"] in ("admin", "gerencia") or (u["rol"] == "vendedor" and ruta and (u.get("vendedor") or "").lower() == ruta.vendedor.lower()):
                n += self.estado.encolar_notificacion(f"salida_preparada|{sal['id']}|{u['usuario']}", "SALIDA_PREPARADA", u["usuario"], u["rol"], asunto, cuerpo)
        return n

    def procesar_notificaciones(self) -> dict:
        res = {"simulado": 0, "error": 0}
        for n in self.estado.pendientes_notificacion():
            try:
                self.estado.marcar_notificacion(n["id"], "simulado", self.notificador.enviar(n))
                res["simulado"] += 1
            except Exception as e:  # noqa: BLE001 - un destinatario con error no frena a los demas
                self.estado.marcar_notificacion(n["id"], "error", f"{type(e).__name__}: {e}")
                res["error"] += 1
        return res

    # ------------------------------------------------------------------ ciclo diario (idempotente)
    def ciclo_diario(self, actor: Optional[dict] = None, hoy: Optional[str] = None) -> dict:
        actor = actor or ACTOR_SISTEMA
        dia = _fecha(hoy) if hoy else self.hoy()
        res = {"fecha": dia.isoformat(), "preparadas": [], "atrasadas": [], "errores": [], "futuras": 0, "ya_preparadas": 0}
        for s in self.estado.listar_salidas():
            if s["estado"] not in ("programada", "preparada"):
                continue
            if s.get("preparada_en"):
                res["ya_preparadas"] += 1
                continue
            fp = self.fecha_preparacion(s["fecha_salida"])
            if dia > _fecha(s["fecha_salida"]):
                self.estado.registrar_alerta(f"PREPARACION_ATRASADA|{s['id']}", "PREPARACION_ATRASADA", "alta",
                                             f"La salida del {s['fecha_salida']} de {s['route_id']} pasó sin prepararse", s["route_id"], s["id"])
                res["atrasadas"].append(s["id"])
            elif dia >= fp:
                try:
                    self.preparar(actor, s["id"])
                    res["preparadas"].append({"salida": s["id"], "ruta": s["route_id"], "fecha_salida": s["fecha_salida"]})
                except ErrorNegocio as e:
                    self.estado.registrar_alerta(f"PREPARACION_FALLIDA|{s['id']}|{dia.isoformat()}", "PREPARACION_FALLIDA", "media",
                                                 f"No se pudo preparar la salida {s['fecha_salida']} de {s['route_id']}: {e.mensaje}", s["route_id"], s["id"])
                    res["errores"].append({"salida": s["id"], "error": e.mensaje})
            else:
                res["futuras"] += 1
        res["resumen_semanal"] = self.encolar_resumen_semanal(dia)
        res["notificaciones"] = self.procesar_notificaciones()
        with self.estado.tx() as c:
            self.estado.auditar(c, actor, "ciclo.diario", "sistema", dia.isoformat(), {k: (len(v) if isinstance(v, list) else v) for k, v in res.items() if k != "notificaciones"})
        return res

    def encolar_resumen_semanal(self, dia: date) -> int:
        """Reporte automatico (una vez por semana ISO) para gerencia/admin y, filtrado a sus rutas, para cada vendedor. SIMULADO."""
        from .dashboard import resumen_texto
        iso = dia.isocalendar()
        n = 0
        rutas = self.servicio.rutas()
        for u in self.estado.listar_usuarios():
            if not u["activo"]:
                continue
            if u["rol"] == "vendedor":
                propias = {k: r for k, r in rutas.items() if r.vendedor.lower() == (u.get("vendedor") or "").lower()}
                if not propias:
                    continue
                cuerpo = resumen_texto(self.estado, propias, dia)
            else:
                cuerpo = resumen_texto(self.estado, rutas, dia)
            n += self.estado.encolar_notificacion(f"resumen_semanal|{iso[0]}-W{iso[1]:02d}|{u['usuario']}", "RESUMEN_SEMANAL", u["usuario"], u["rol"],
                                                  f"Resumen de cartera y rutas · semana {iso[1]}", cuerpo)
        return n

    # ------------------------------------------------------------------ impresion (solo registro de solicitudes)
    def preparar_impresion(self, actor: dict, sid: int, copias: int = 1) -> dict:
        """Crea, sin duplicar, las solicitudes de la salida: hoja de visita vinculada y Excel vigente de la salida SI no es borrador.
        Nada se imprime: un futuro agente autenticado tomara los pendientes."""
        sal = self.estado.obtener_salida(sid)
        if not sal:
            raise ErrorNegocio("Salida no encontrada.", 404)
        activas = [i for i in self.estado.listar_impresiones() if i["salida_id"] == sid and i["estado"] in ("pendiente", "tomado", "impreso")]
        creadas, existentes, omitidas = [], [], []
        for d in self.estado.documentos_de_salida(sid):
            if d["tipo"] != "HOJA_VISITA":
                continue
            previa = next((i for i in activas if i["documento_id"] == d["id"]), None)
            if previa:
                existentes.append(previa["id"])
            else:
                creadas.append(self.estado.solicitar_impresion(actor, "HOJA_VISITA", sal["route_id"], sid, d["id"], f"Hoja de visita v{d['version']}", copias)["id"])
        e = self.estado.excel_actual(sal["route_id"])
        if e and e["fecha_salida"] == sal["fecha_salida"]:
            if e["borrador"]:
                omitidas.append({"tipo": "EXCEL_RUTA", "motivo": "El Excel es un BORRADOR (fuente no vigente): no se prepara su impresión."})
            else:
                previa = next((i for i in activas if i.get("excel_id") == e["id"]), None)
                if previa:
                    existentes.append(previa["id"])
                else:
                    creadas.append(self.estado.solicitar_impresion(actor, "EXCEL_RUTA", sal["route_id"], sid, None, None, copias, excel_id=e["id"])["id"])
        elif not e:
            omitidas.append({"tipo": "EXCEL_RUTA", "motivo": "La salida aún no tiene Excel publicado."})
        return {"salida": sid, "creadas": creadas, "existentes": existentes, "omitidas": omitidas, "nota": "Solo registro: no hay impresoras conectadas."}

    # ------------------------------------------------------------------ Excel de ruta publicable
    def generar_excel_salida(self, actor: dict, sid: int, base: str) -> dict:
        sal = self.estado.obtener_salida(sid)
        if not sal:
            raise ErrorNegocio("Salida no encontrada.", 404)
        if not sal.get("preparada_en"):
            raise ErrorNegocio("Prepare la salida antes de generar el Excel: las excepciones se identifican antes de publicar.", 409)
        if not sal.get("revisada_por"):
            raise ErrorNegocio("Falta la revisión humana de las excepciones de la salida: revísela antes de publicar el Excel.", 409)
        pend = self.estado.listar_candidatos(sal["route_id"], "propuesta")
        if pend:
            raise ErrorNegocio(f"Hay {len(pend)} cliente(s) propuesto(s) sin confirmar o rechazar: el roster del Excel no está cerrado.", 409)
        datos, nombre, avisos = self.servicio.generar_excel(actor, sal["route_id"], base, sal["fecha_salida"])
        fuente = self.servicio.fuente
        borrador = not bool(getattr(fuente, "apta_para_publicar", False))   # solo una fuente en vivo REAL, fresca y sin problemas de calidad publica
        ruta = self.estado.obtener_ruta(sal["route_id"])
        return self.estado.registrar_excel(actor, sal["route_id"], sid, sal["fecha_salida"], nombre, datos, hashlib.sha256(datos).hexdigest(), huella_excel(datos), borrador,
                                           getattr(fuente, "tipo", "SIN_FUENTE"), fuente.fecha_corte.isoformat() if fuente else "", len(ruta["clientes"]), avisos)
