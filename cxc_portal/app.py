"""Aplicacion HTTP del portal: autenticacion por sesion, permisos por rol APLICADOS EN LA API, CSRF y cabeceras de seguridad.

Roles:
    admin     usuarios, vendedores, rutas/zonas/clientes/orden de folders, importaciones, auditoria. NO decide clasificaciones.
    gerencia  lectura global, reportes gerenciales, decisiones/excepciones de gerencia, visitas, auditoria (lectura).
    vendedor  solo SUS rutas (clientes, semaforo, facturas, alertas) y registrar visitas de sus rutas. Sin cartera global ni reportes.
Todo lo financiero exige sesion valida. Solo libreria estandar.
"""

from __future__ import annotations

import csv
import hmac
import io
import json
import re
import sys
import tempfile
from datetime import date
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from cxc_bot import payment_semaphore as sm

from . import security as sec
from .estado import ErrorNegocio, Estado
from .store import PortalServicio, Ruta

COOKIE = "cxc_session"
MAX_BODY = 512 * 1024
LOOPBACK = ("127.0.0.1", "localhost", "::1")

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")

REGLAS = {
    "vigencia": "Hub Cosprobell CxC — Principal, decisiones 08–09/10/2026",
    "ventana_facturas": sm.VENTANA_FACTURAS, "minimo_historial": sm.MIN_HISTORIAL,
    "umbrales_media_mora": {"A": f"≤ {sm.TOLERANCIA_A}", "B": f"> {sm.TOLERANCIA_A} y ≤ {sm.LIMITE_B}", "C": f"> {sm.LIMITE_B} y ≤ {sm.LIMITE_C}",
                            "CANDIDATO_SOLO_CONTADO": f"> {sm.LIMITE_C}"},
    "abiertas_entran_a_la_media_si": f"mora > {sm.ABIERTA_MORA_MINIMA} días", "plazos": {"foranea": sm.PLAZO_FORANEA, "local": sm.PLAZO_LOCAL},
    "alerta_por_factura": f"mora > {sm.ALERTA_FACTURA} días: abiertas = alerta operativa de cobro; pagadas = alerta histórica de comportamiento (nunca cobro pendiente)",
    "notas": ["Se evalúan las 10 facturas más recientes (emisión DESC, desempate número DESC).", "0–3 facturas en todo el historial: cliente de prueba (blanca).",
              "Mora = max(0, último recibo contable (o corte si está abierta) − emisión − plazo de la ruta). El vencimiento SAP se conserva, no es la base.",
              "Más de 60 días es solo candidato a solo contado: la viñeta roja la decide gerencia.",
              "Las decisiones de gerencia se guardan aparte con autor, motivo y fecha; el cálculo automático no se altera."],
}


class Contexto:
    def __init__(self, servicio: PortalServicio, estado: Estado, https: bool = False, hosts_permitidos: Optional[List[str]] = None,
                 confiar_proxy: bool = False, limitador: Optional[sec.LimitadorIntentos] = None):
        self.servicio, self.estado, self.https = servicio, estado, https
        self.hosts = {h.lower() for h in (hosts_permitidos or [])} | set(LOOPBACK)
        self.confiar_proxy = confiar_proxy
        self.limitador = limitador or sec.LimitadorIntentos()


class Peticion:
    def __init__(self, metodo: str, ruta: str, qs: dict, cuerpo: dict, usuario: Optional[dict], csrf: Optional[str], ip: str, params: dict):
        self.metodo, self.ruta, self.qs, self.cuerpo, self.usuario, self.csrf, self.ip, self.params = metodo, ruta, qs, cuerpo, usuario, csrf, ip, params

    def q(self, k: str, d: str = "") -> str:
        return (self.qs.get(k, [d])[0] or d).strip()


class Respuesta:
    def __init__(self, estado: int = 200, datos: Any = None, cuerpo: Optional[bytes] = None, tipo: str = "application/json; charset=utf-8",
                 cabeceras: Optional[dict] = None, cookies: Optional[List[str]] = None):
        self.estado, self.cabeceras, self.cookies, self.tipo = estado, cabeceras or {}, cookies or [], tipo
        self.cuerpo = cuerpo if cuerpo is not None else json.dumps(datos, ensure_ascii=False, default=str).encode("utf-8")


RUTAS: List[Tuple[str, "re.Pattern", Optional[Tuple[str, ...]], Callable, bool]] = []


def endpoint(metodo: str, patron: str, roles: Optional[Tuple[str, ...]] = ("admin", "gerencia", "vendedor"), publico: bool = False):
    """roles=None + publico=True: sin sesion. Con sesion: rol debe estar en `roles`."""
    rx = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", patron) + "$")

    def deco(fn):
        RUTAS.append((metodo, rx, roles, fn, publico))
        return fn
    return deco


ADM, GER, VEN = "admin", "gerencia", "vendedor"
TODOS = (ADM, GER, VEN)
LECTURA_GLOBAL = (ADM, GER)


# ----------------------------------------------------------------------------- helpers de permisos
def _visibles(ctx: Contexto, u: dict) -> Dict[str, Ruta]:
    rutas = ctx.servicio.rutas()
    if u["rol"] in (ADM, GER):
        return rutas
    return {k: r for k, r in rutas.items() if r.vendedor.lower() == (u.get("vendedor") or "").lower()}


def _ruta_visible(ctx: Contexto, u: dict, route_id: str) -> Ruta:
    r = _visibles(ctx, u).get(route_id)
    if r is None:  # no se distingue "no existe" de "sin permiso": no se filtra informacion
        raise ErrorNegocio("Ruta no encontrada.", 404)
    return r


def _cuerpo_str(p: Peticion, k: str, obligatorio: bool = True) -> str:
    v = p.cuerpo.get(k)
    if v is None or (isinstance(v, str) and not v.strip()):
        if obligatorio:
            raise ErrorNegocio(f"Falta el campo '{k}'.")
        return ""
    if not isinstance(v, (str, int, float)):
        raise ErrorNegocio(f"Campo '{k}' inválido.")
    return str(v).strip()


# ----------------------------------------------------------------------------- publico / auth
@endpoint("GET", "/api/health", roles=None, publico=True)
def health(ctx: Contexto, p: Peticion):
    return Respuesta(datos={"status": "ok"})


@endpoint("POST", "/api/auth/login", roles=None, publico=True)
def login(ctx: Contexto, p: Peticion):
    usuario, clave = _cuerpo_str(p, "usuario"), str(p.cuerpo.get("password") or "")
    clave_lim = f"{usuario.lower()}|{p.ip}"
    espera = ctx.limitador.bloqueado(clave_lim) or ctx.limitador.bloqueado(f"ip|{p.ip}")
    if espera:
        return Respuesta(429, {"error": f"Demasiados intentos. Intente de nuevo en {espera} s."}, cabeceras={"Retry-After": str(espera)})
    u = ctx.estado.autenticar(usuario, clave, p.ip)
    if not u:
        ctx.limitador.fallo(clave_lim)
        ctx.limitador.fallo(f"ip|{p.ip}")
        return Respuesta(401, {"error": "Usuario o contraseña incorrectos."})
    ctx.limitador.exito(clave_lim)
    ses = ctx.estado.crear_sesion(u["id"], p.ip)
    ck = f"{COOKIE}={ses['token']}; HttpOnly; SameSite=Strict; Path=/; Max-Age={sec.SESION_MAX_SEG}" + ("; Secure" if ctx.https else "")
    return Respuesta(datos={"usuario": u, "csrf": ses["csrf"], "debe_cambiar_password": bool(u["debe_cambiar_password"])}, cookies=[ck])


@endpoint("POST", "/api/auth/logout", roles=TODOS)
def logout(ctx: Contexto, p: Peticion):
    ctx.estado.cerrar_sesion(p.cuerpo.get("_token", ""))
    return Respuesta(datos={"ok": True}, cookies=[f"{COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0" + ("; Secure" if ctx.https else "")])


@endpoint("GET", "/api/auth/me", roles=TODOS)
def me(ctx: Contexto, p: Peticion):
    return Respuesta(datos={"usuario": p.usuario, "csrf": p.csrf})


@endpoint("POST", "/api/auth/password", roles=TODOS)
def cambiar_password(ctx: Contexto, p: Peticion):
    ctx.estado.cambiar_password(p.usuario["id"], str(p.cuerpo.get("actual") or ""), str(p.cuerpo.get("nueva") or ""), p.usuario)
    return Respuesta(datos={"ok": True}, cookies=[f"{COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0" + ("; Secure" if ctx.https else "")])


# ----------------------------------------------------------------------------- lectura de negocio
@endpoint("GET", "/api/fuente")
def fuente(ctx: Contexto, p: Peticion):
    d = {"fuente": ctx.servicio.info_fuente(), "capacidades": ctx.servicio.capacidades()}
    if p.usuario["rol"] in LECTURA_GLOBAL:
        from .reporting_bridge import estado_reporting_core
        d["reporting_core"] = estado_reporting_core()
    return Respuesta(datos=d)


@endpoint("GET", "/api/reglas")
def reglas(ctx: Contexto, p: Peticion):
    return Respuesta(datos=REGLAS)


@endpoint("GET", "/api/rutas")
def rutas(ctx: Contexto, p: Peticion):
    vis = _visibles(ctx, p.usuario)
    return Respuesta(datos={"rutas": [r.resumen() for r in vis.values()], "fuente": ctx.servicio.info_fuente()})


def _clientes_filtrados(r: Ruta, p: Peticion) -> List[dict]:
    zona, clas, q = p.q("zona").upper(), p.q("clasificacion").upper(), p.q("q").upper()
    out = []
    for c in r.clientes:  # orden de la lista = orden de folders
        if zona and c.zona.upper() != zona:
            continue
        if clas and c.resultado.final.upper() != clas:
            continue
        if q and q not in c.codigo.upper() and q not in c.nombre.upper() and q not in c.folder.upper():
            continue
        out.append(r.cliente_dict(c))
    return out


@endpoint("GET", "/api/rutas/{rid}/clientes")
def rutas_clientes(ctx: Contexto, p: Peticion):
    r = _ruta_visible(ctx, p.usuario, p.params["rid"])
    return Respuesta(datos={"ruta": r.resumen(), "zonas": r.zonas, "clientes": _clientes_filtrados(r, p)})


@endpoint("GET", "/api/rutas/{rid}/reporte.csv")
def ruta_csv(ctx: Contexto, p: Peticion):
    r = _ruta_visible(ctx, p.usuario, p.params["rid"])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["# Ruta", r.nombre, "Fuente", r.fuente.get("etiqueta"), "Corte", r.fuente.get("fecha_corte")])
    w.writerow(["Folder", "Zona", "Codigo", "Cliente", "Saldo", "Clasificacion calculada", "Clasificacion final", "Color final", "Mora media",
                "Facturas historial", "Facturas evaluables", "Alertas de cobro (abiertas > 60 d)", "Alertas historicas (pagadas > 60 d)", "Decision de gerencia"])
    for c in r.clientes:
        s = c.resultado
        dec = s.decision_gerencia
        w.writerow([c.folder, c.zona, c.codigo, c.nombre, f"{c.saldo:.2f}", s.calculado, s.final, s.color_final,
                    f"{s.promedio_mora:.2f}" if s.promedio_mora is not None else "", s.historial_total, s.evaluables,
                    len(s.alertas_cobro), len(s.alertas_historicas), f"{dec.actor}: {dec.motivo}" if dec else ""])
    ctx.estado_auditar(p, "reporte.csv", r.route_id)
    return Respuesta(cuerpo=("﻿" + buf.getvalue()).encode("utf-8"), tipo="text/csv; charset=utf-8",
                     cabeceras={"Content-Disposition": f'attachment; filename="cxc_{r.route_id}_{r.fuente.get("fecha_corte")}.csv"'})


@endpoint("GET", "/api/clientes/{codigo}")
def cliente(ctx: Contexto, p: Peticion):
    hit = ctx.servicio.buscar_cliente(p.params["codigo"])
    if not hit or hit[0].route_id not in _visibles(ctx, p.usuario):
        raise ErrorNegocio("Cliente no encontrado.", 404)
    r, c = hit
    d = r.cliente_dict(c)
    if p.usuario["rol"] in LECTURA_GLOBAL:
        d["historial_decisiones"] = ctx.estado.historial_decisiones(c.codigo)
    d["visitas"] = ctx.estado.listar_visitas(route_id=r.route_id, codigo=c.codigo)[:20]
    return Respuesta(datos=d)


@endpoint("GET", "/api/alertas")
def alertas(ctx: Contexto, p: Peticion):
    vis = dict(_visibles(ctx, p.usuario))
    rid = p.q("ruta")
    if rid:
        if rid not in vis:
            raise ErrorNegocio("Ruta no encontrada.", 404)
        vis = {rid: vis[rid]}
    cobro, hist = [], []
    for r in vis.values():
        for c in r.clientes:
            base = {"codigo": c.codigo, "cliente": c.nombre, "folder": c.folder, "ruta": r.route_id, "ruta_nombre": r.nombre, "vendedor": r.vendedor,
                    "fecha_corte": r.fuente.get("fecha_corte"), "clasificacion_final": c.resultado.final}
            cobro += [{**base, **a.to_dict()} for a in c.resultado.alertas_cobro]
            hist += [{**base, **a.to_dict()} for a in c.resultado.alertas_historicas]
    cobro.sort(key=lambda a: (a["saldo_pendiente"] or 0), reverse=True)
    hist.sort(key=lambda a: a["mora"], reverse=True)
    return Respuesta(datos={
        "nota": "Datos del snapshot, no en vivo. Las alertas de cobro son operativas (hay saldo pendiente); las históricas son de comportamiento y NO son cobro pendiente.",
        "cobro_operativas": cobro, "historicas_comportamiento": hist})


@endpoint("GET", "/api/prioridades", roles=LECTURA_GLOBAL)
def prioridades(ctx: Contexto, p: Peticion):
    n = max(1, min(50, int(p.q("n", "10") or 10)))
    rid = p.q("ruta")
    rutas_ = [ctx.servicio.ruta(rid)] if rid and ctx.servicio.ruta(rid) else list(ctx.servicio.rutas().values())
    items = [(r, c) for r in rutas_ for c in r.clientes if c.saldo > 0]
    items.sort(key=lambda rc: rc[1].saldo, reverse=True)
    return Respuesta(datos={"nota": "Mayores saldos del snapshot. No es cartera en vivo; la prioridad de cobro confiable requiere fuente vigente.",
                            "prioridades": [{**r.cliente_dict(c), "ruta_nombre": r.nombre} for r, c in items[:n]]})


# ----------------------------------------------------------------------------- decisiones (solo gerencia)
@endpoint("GET", "/api/decisiones", roles=LECTURA_GLOBAL)
def decisiones(ctx: Contexto, p: Peticion):
    cod = p.q("codigo")
    if cod:
        return Respuesta(datos={"decisiones": ctx.estado.historial_decisiones(cod)})
    return Respuesta(datos={"decisiones": list(ctx.estado.decisiones_vigentes().values())})


@endpoint("POST", "/api/decisiones", roles=(GER,))
def decision_registrar(ctx: Contexto, p: Peticion):
    d = ctx.servicio.registrar_decision(p.usuario, _cuerpo_str(p, "codigo"), _cuerpo_str(p, "clasificacion_final"), _cuerpo_str(p, "motivo"))
    return Respuesta(201, d)


@endpoint("POST", "/api/decisiones/anular", roles=(GER,))
def decision_anular(ctx: Contexto, p: Peticion):
    return Respuesta(datos=ctx.servicio.anular_decision(p.usuario, _cuerpo_str(p, "codigo"), _cuerpo_str(p, "motivo")))


# ----------------------------------------------------------------------------- visitas
@endpoint("GET", "/api/visitas")
def visitas(ctx: Contexto, p: Peticion):
    vis = _visibles(ctx, p.usuario)
    rid = p.q("ruta")
    if rid and rid not in vis:
        raise ErrorNegocio("Ruta no encontrada.", 404)
    filas = ctx.estado.listar_visitas(route_id=rid or None, fecha_salida=p.q("fecha") or None, codigo=p.q("codigo") or None)
    return Respuesta(datos={"visitas": [f for f in filas if f["route_id"] in vis]})


@endpoint("POST", "/api/visitas")
def visita_registrar(ctx: Contexto, p: Peticion):
    r = _ruta_visible(ctx, p.usuario, _cuerpo_str(p, "ruta"))
    v = ctx.estado.registrar_visita(p.usuario, r.route_id, _cuerpo_str(p, "fecha_salida"), _cuerpo_str(p, "codigo"), _cuerpo_str(p, "estado"),
                                    _cuerpo_str(p, "motivo", False), _cuerpo_str(p, "observacion", False))
    return Respuesta(201, v)


@endpoint("GET", "/api/visitas/cobertura")
def visitas_cobertura(ctx: Contexto, p: Peticion):
    r = _ruta_visible(ctx, p.usuario, p.q("ruta"))
    return Respuesta(datos=ctx.estado.cobertura_visitas(r.route_id, p.q("fecha") or date.today().isoformat()))


@endpoint("GET", "/api/visitas/{vid}/eventos")
def visita_eventos(ctx: Contexto, p: Peticion):
    vis = _visibles(ctx, p.usuario)
    v = next((x for x in ctx.estado.listar_visitas() if str(x["id"]) == p.params["vid"] and x["route_id"] in vis), None)
    if not v:
        raise ErrorNegocio("Visita no encontrada.", 404)
    return Respuesta(datos={"visita": v, "eventos": ctx.estado.eventos_visita(v["id"])})


# ----------------------------------------------------------------------------- Excel de ruta (generador de CXC BOT)
@endpoint("GET", "/api/excel/bases", roles=LECTURA_GLOBAL)
def excel_bases(ctx: Contexto, p: Peticion):
    return Respuesta(datos={"bases": ctx.servicio.bases_excel()})


@endpoint("POST", "/api/rutas/{rid}/generar-excel", roles=LECTURA_GLOBAL)
def generar_excel(ctx: Contexto, p: Peticion):
    datos, nombre, avisos = ctx.servicio.generar_excel(p.usuario, p.params["rid"], _cuerpo_str(p, "base"), _cuerpo_str(p, "fecha_salida"))
    return Respuesta(cuerpo=datos, tipo="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     cabeceras={"Content-Disposition": f'attachment; filename="{nombre}"',
                                "X-Cxc-Aviso": "BORRADOR: saldos del snapshot historico, no en vivo. No usar como ruta operativa vigente."})


# ----------------------------------------------------------------------------- reportes gerenciales (cxc-reporting-core)
def _reporting(ctx: Contexto):
    if ctx.servicio.reporting is None:
        raise ErrorNegocio("cxc-reporting-core no está disponible en este servidor (defina CXC_REPORTING_SRC).", 503)
    return ctx.servicio.reporting


@endpoint("POST", "/api/reportes/importar-corte", roles=LECTURA_GLOBAL)
def reporte_importar(ctx: Contexto, p: Peticion):
    rep = _reporting(ctx)
    if ctx.servicio.fuente is None or ctx.servicio.fuente.en_vivo:
        raise ErrorNegocio("No hay un snapshot utilizable para importar.", 409)
    res = rep.importar_corte(ctx.estado.listar_rutas())
    ctx.estado_auditar(p, "reporte.importar_corte", res.get("id_corte") or "")
    return Respuesta(datos=res)


@endpoint("GET", "/api/reportes/gerencial", roles=LECTURA_GLOBAL)
def reporte_gerencial(ctx: Contexto, p: Peticion):
    rep = _reporting(ctx)
    try:
        return Respuesta(datos=rep.reporte())
    except Exception as e:  # noqa: BLE001
        raise ErrorNegocio(f"Sin corte disponible: {e}", 409) from e


@endpoint("GET", "/api/reportes/gerencial.xlsx", roles=LECTURA_GLOBAL)
def reporte_xlsx(ctx: Contexto, p: Peticion):
    rep = _reporting(ctx)
    with tempfile.TemporaryDirectory(prefix="cxc_rep_") as d:
        try:
            ruta = rep.escribir_xlsx(Path(d) / "reporte.xlsx")
        except Exception as e:  # noqa: BLE001
            raise ErrorNegocio(f"Sin corte disponible: {e}", 409) from e
        datos = ruta.read_bytes()
    ctx.estado_auditar(p, "reporte.xlsx", "")
    return Respuesta(cuerpo=datos, tipo="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     cabeceras={"Content-Disposition": 'attachment; filename="cxc_reporte_gerencial_NO_VALIDADO.xlsx"'})


@endpoint("GET", "/api/reportes/cliente/{codigo}", roles=LECTURA_GLOBAL)
def reporte_cliente(ctx: Contexto, p: Peticion):
    d = _reporting(ctx).cliente_360(p.params["codigo"])
    if d is None:
        raise ErrorNegocio("Cliente no encontrado en el corte.", 404)
    return Respuesta(datos=d)


# ----------------------------------------------------------------------------- administracion
@endpoint("GET", "/api/admin/usuarios", roles=(ADM,))
def adm_usuarios(ctx: Contexto, p: Peticion):
    return Respuesta(datos={"usuarios": ctx.estado.listar_usuarios()})


@endpoint("POST", "/api/admin/usuarios", roles=(ADM,))
def adm_usuario_crear(ctx: Contexto, p: Peticion):
    clave = _cuerpo_str(p, "password")
    u = ctx.estado.crear_usuario(p.usuario, _cuerpo_str(p, "usuario"), _cuerpo_str(p, "nombre", False), _cuerpo_str(p, "rol"), clave,
                                 _cuerpo_str(p, "vendedor", False) or None)
    return Respuesta(201, u)


@endpoint("PATCH", "/api/admin/usuarios/{uid}", roles=(ADM,))
def adm_usuario_actualizar(ctx: Contexto, p: Peticion):
    activo = p.cuerpo.get("activo")
    return Respuesta(datos=ctx.estado.actualizar_usuario(p.usuario, int(p.params["uid"]), activo if isinstance(activo, bool) else None,
                                                         _cuerpo_str(p, "rol", False) or None, _cuerpo_str(p, "password", False) or None))


@endpoint("GET", "/api/admin/vendedores", roles=(ADM, GER))
def adm_vendedores(ctx: Contexto, p: Peticion):
    return Respuesta(datos={"vendedores": ctx.estado.listar_vendedores()})


@endpoint("POST", "/api/admin/vendedores", roles=(ADM,))
def adm_vendedor_crear(ctx: Contexto, p: Peticion):
    return Respuesta(201, ctx.estado.crear_vendedor(p.usuario, _cuerpo_str(p, "nombre")))


@endpoint("GET", "/api/admin/rutas", roles=(ADM, GER))
def adm_rutas(ctx: Contexto, p: Peticion):
    return Respuesta(datos={"rutas": ctx.estado.listar_rutas(solo_activas=False)})


@endpoint("POST", "/api/admin/rutas", roles=(ADM,))
def adm_ruta_guardar(ctx: Contexto, p: Peticion):
    zonas = p.cuerpo.get("zonas")
    clientes = p.cuerpo.get("clientes")
    if zonas is not None and not isinstance(zonas, dict):
        raise ErrorNegocio("'zonas' debe ser un objeto {prefijo: nombre}.")
    if clientes is not None and not isinstance(clientes, list):
        raise ErrorNegocio("'clientes' debe ser una lista.")
    r = ctx.estado.guardar_ruta(p.usuario, _cuerpo_str(p, "route_id"), _cuerpo_str(p, "nombre"), _cuerpo_str(p, "vendedor"), _cuerpo_str(p, "tipo_ruta"),
                                int(p.cuerpo["plazo_dias"]) if p.cuerpo.get("plazo_dias") else None, zonas, clientes,
                                _cuerpo_str(p, "estado_fisico", False), _cuerpo_str(p, "notas", False))
    ctx.servicio.invalidar()
    return Respuesta(201, r)


@endpoint("POST", "/api/admin/rutas/importar", roles=(ADM,))
def adm_ruta_importar(ctx: Contexto, p: Peticion):
    zonas = p.cuerpo.get("zonas")
    r = ctx.servicio.importar_ruta_desde_excel(p.usuario, _cuerpo_str(p, "archivo"), _cuerpo_str(p, "route_id"), _cuerpo_str(p, "nombre"),
                                               _cuerpo_str(p, "vendedor"), _cuerpo_str(p, "tipo_ruta"),
                                               int(p.cuerpo["plazo_dias"]) if p.cuerpo.get("plazo_dias") else None, zonas if isinstance(zonas, dict) else None)
    return Respuesta(201, r)


@endpoint("POST", "/api/admin/rutas/{rid}/clientes", roles=(ADM,))
def adm_cliente_agregar(ctx: Contexto, p: Peticion):
    r = ctx.estado.agregar_cliente(p.usuario, p.params["rid"], _cuerpo_str(p, "codigo"), _cuerpo_str(p, "nombre", False), _cuerpo_str(p, "zona"))
    ctx.servicio.invalidar()
    return Respuesta(201, r)


@endpoint("DELETE", "/api/admin/rutas/{rid}/clientes/{codigo}", roles=(ADM,))
def adm_cliente_quitar(ctx: Contexto, p: Peticion):
    ctx.estado.quitar_cliente(p.usuario, p.params["rid"], p.params["codigo"])
    ctx.servicio.invalidar()
    return Respuesta(datos={"ok": True})


@endpoint("POST", "/api/admin/rutas/{rid}/activa", roles=(ADM,))
def adm_ruta_activa(ctx: Contexto, p: Peticion):
    ctx.estado.desactivar_ruta(p.usuario, p.params["rid"], bool(p.cuerpo.get("activa")))
    ctx.servicio.invalidar()
    return Respuesta(datos={"ok": True})


@endpoint("GET", "/api/admin/auditoria", roles=(ADM, GER))
def adm_auditoria(ctx: Contexto, p: Peticion):
    return Respuesta(datos={"eventos": ctx.estado.listar_auditoria(min(500, int(p.q("n", "200") or 200)))})


# ----------------------------------------------------------------------------- servidor HTTP
def _estado_auditar(ctx: Contexto):
    def f(p: Peticion, accion: str, referencia: str) -> None:
        with ctx.estado.tx() as c:
            ctx.estado.auditar(c, p.usuario, accion, "reporte", referencia, None, p.ip)
    return f


def make_handler(ctx: Contexto, web_dir: Optional[Path]):
    ctx.estado_auditar = _estado_auditar(ctx)

    class Handler(BaseHTTPRequestHandler):
        server_version = "cxc-portal"
        sys_version = ""
        protocol_version = "HTTP/1.0"  # una peticion por conexion: sin cuerpos sin leer ni conexiones colgadas

        def log_message(self, fmt, *args):
            pass

        # -- utilidades
        def _ip(self) -> str:
            if ctx.confiar_proxy:
                xff = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
                if xff:
                    return xff[:64]
            return self.client_address[0]

        def _host_ok(self) -> bool:
            h = (self.headers.get("Host") or "").strip().lower()
            nombre = h[1:h.index("]")] if h.startswith("[") else (h.rsplit(":", 1)[0] if ":" in h else h)
            return nombre in ctx.hosts

        def _origen_ok(self) -> bool:
            o = self.headers.get("Origin")
            if not o:
                return True
            m = re.match(r"^https?://([^/:]+|\[[^\]]+\])(:\d+)?$", o)
            return bool(m) and m.group(1).strip("[]").lower() in ctx.hosts

        def _enviar(self, r: Respuesta, api: bool = True):
            self.send_response(r.estado)
            self.send_header("Content-Type", r.tipo)
            self.send_header("Content-Length", str(len(r.cuerpo)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            if api:
                self.send_header("Cache-Control", "no-store")
            if ctx.https:
                self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            for k, v in r.cabeceras.items():
                self.send_header(k, v)
            for ck in r.cookies:
                self.send_header("Set-Cookie", ck)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(r.cuerpo)

        def _token(self) -> str:
            raw = self.headers.get("Cookie") or ""
            try:
                c = SimpleCookie(raw)
                return c[COOKIE].value if COOKIE in c else ""
            except Exception:  # noqa: BLE001
                return ""

        def _cuerpo(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ErrorNegocio("Cuerpo demasiado grande.", 413)
            if not n:
                return {}
            try:
                d = json.loads(self.rfile.read(n))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                raise ErrorNegocio("Se esperaba JSON válido.") from e
            if not isinstance(d, dict):
                raise ErrorNegocio("Se esperaba un objeto JSON.")
            return d

        # -- despacho
        def _manejar(self):
            if not self._host_ok():
                return self._enviar(Respuesta(403, {"error": "Host no permitido."}))
            u = urlparse(self.path)
            ruta = unquote(u.path)
            if not ruta.startswith("/api/"):
                return self._estatico(ruta) if self.command in ("GET", "HEAD") else self._enviar(Respuesta(405, {"error": "Método no permitido."}))
            metodo = self.command
            candidatos = [(rx.match(ruta), roles, fn, pub) for m, rx, roles, fn, pub in RUTAS if m == metodo]
            hallado = next(((m, roles, fn, pub) for m, roles, fn, pub in candidatos if m), None)
            if hallado is None:
                existe = any(rx.match(ruta) for _, rx, _, _, _ in RUTAS)
                return self._enviar(Respuesta(405 if existe else 404, {"error": "Método no permitido." if existe else "Endpoint no encontrado."}))
            m, roles, fn, publico = hallado
            try:
                if metodo != "GET" and not self._origen_ok():
                    return self._enviar(Respuesta(403, {"error": "Origen no permitido."}))
                token = self._token()
                ses = None if publico else ctx.estado.sesion(token)
                if not publico:
                    if ses is None:
                        return self._enviar(Respuesta(401, {"error": "Sesión requerida."}))
                    usuario = ses["usuario"]
                    if usuario["debe_cambiar_password"] and ruta not in ("/api/auth/password", "/api/auth/me", "/api/auth/logout"):
                        return self._enviar(Respuesta(403, {"error": "Debe cambiar su contraseña antes de continuar.", "debe_cambiar_password": True}))
                    if roles is not None and usuario["rol"] not in roles:
                        with ctx.estado.tx() as c:
                            ctx.estado.auditar(c, usuario, "acceso.denegado", "api", ruta, {"metodo": metodo}, self._ip())
                        return self._enviar(Respuesta(403, {"error": "No tiene permiso para esta operación."}))
                    if metodo != "GET" and not hmac.compare_digest(self.headers.get("X-CSRF-Token") or "", ses["csrf"]):
                        return self._enviar(Respuesta(403, {"error": "Token CSRF inválido."}))
                cuerpo = self._cuerpo() if metodo in ("POST", "PATCH", "PUT", "DELETE") else {}
                if fn is logout:
                    cuerpo["_token"] = token
                p = Peticion(metodo, ruta, parse_qs(u.query), cuerpo, ses["usuario"] if ses else None, ses["csrf"] if ses else None, self._ip(), m.groupdict())
                self._enviar(fn(ctx, p))
            except ErrorNegocio as e:
                self._enviar(Respuesta(e.codigo, {"error": e.mensaje}))
            except Exception as e:  # noqa: BLE001 - nunca se filtra el detalle interno al cliente
                print(f"ERROR {metodo} {ruta}: {type(e).__name__}: {e}", file=sys.stderr)
                self._enviar(Respuesta(500, {"error": "Error interno."}))

        do_GET = do_POST = do_PATCH = do_DELETE = do_PUT = _manejar

        def do_HEAD(self):  # noqa: N802
            self._manejar()

        def do_OPTIONS(self):  # noqa: N802 - sin CORS: el frontend se sirve desde el mismo origen
            self._enviar(Respuesta(204, cuerpo=b""))

        # -- frontend estatico (publico: solo codigo de la interfaz, nunca datos)
        def _estatico(self, ruta: str):
            if not web_dir or not Path(web_dir).is_dir():
                return self._enviar(Respuesta(404, {"error": "Frontend no construido."}), api=False)
            base = Path(web_dir).resolve()
            destino = (base / (ruta.lstrip("/") or "index.html")).resolve()
            if base not in destino.parents and destino != base:
                return self._enviar(Respuesta(403, {"error": "Ruta no permitida."}), api=False)
            if not destino.is_file():
                destino = base / "index.html"
            import mimetypes
            tipo = mimetypes.guess_type(str(destino))[0] or "application/octet-stream"
            if tipo.startswith("text/") or tipo in ("application/javascript", "application/json"):
                tipo += "; charset=utf-8"
            self._enviar(Respuesta(200, cuerpo=destino.read_bytes(), tipo=tipo), api=False)

    return Handler
