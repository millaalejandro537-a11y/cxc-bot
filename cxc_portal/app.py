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
import secrets as _secrets
import sys
import tempfile
import time
from datetime import date
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from cxc_bot import payment_semaphore as sm

from . import security as sec
from .estado import ErrorNegocio, Estado
from .fuentes import es_operable
from .store import PortalServicio, Ruta

COOKIE = "cxc_session"
MAX_BODY = 512 * 1024
SOCKET_TIMEOUT_SEG = 30
LOOPBACK = ("127.0.0.1", "localhost", "::1")

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")

REGLAS = {
    "vigencia": "Hub Cosprobell CxC — Principal, decisiones 08–09/10/2026",
    "ventana_facturas": sm.VENTANA_FACTURAS, "minimo_historial": sm.MIN_HISTORIAL,
    "umbrales_media_mora": {"A": f"≤ {sm.TOLERANCIA_A}", "B": f"> {sm.TOLERANCIA_A} y ≤ {sm.LIMITE_B}", "C": f"> {sm.LIMITE_B} y ≤ {sm.LIMITE_C}",
                            (sm.SOLO_CONTADO if sm.CONTADO_AUTOMATICO else sm.CANDIDATO_SOLO_CONTADO): f"> {sm.LIMITE_C}"},
    "abiertas_entran_a_la_media_si": f"mora > {sm.ABIERTA_MORA_MINIMA} días", "plazos": {"foranea": sm.PLAZO_FORANEA, "local": sm.PLAZO_LOCAL},
    "alerta_por_factura": f"mora > {sm.ALERTA_FACTURA} días: abiertas = alerta operativa de cobro; pagadas = alerta histórica de comportamiento (nunca cobro pendiente)",
    "notas": ["Se evalúan las 10 facturas más recientes (emisión DESC, desempate número DESC).", "0–3 facturas en todo el historial: cliente de prueba (blanca).",
              "Mora = max(0, último recibo contable (o corte si está abierta) − emisión − plazo de la ruta). El vencimiento SAP se conserva, no es la base.",
              "Promedio de mora de más de 60 días = solo contado: viñeta ROJA automática (regla vigente), sin aprobación adicional de gerencia.",
              "Las decisiones de gerencia se guardan aparte con autor, motivo y fecha como trazabilidad. El sistema prevalece: una decisión solo resuelve lo que el sistema no puede clasificar (revisión humana / sin evidencia)."
              if sm.SISTEMA_PREVALECE else "Las decisiones de gerencia se guardan aparte con autor, motivo y fecha; el cálculo automático no se altera."],
    "vigentes": {"CONTADO_AUTOMATICO": sm.CONTADO_AUTOMATICO, "SISTEMA_PREVALECE": sm.SISTEMA_PREVALECE, "ANTICIPOS_RESPALDAN_PAGO": sm.ANTICIPOS_RESPALDAN_PAGO},
}


class Contexto:
    def __init__(self, servicio: PortalServicio, estado: Estado, https: bool = False, hosts_permitidos: Optional[List[str]] = None,
                 confiar_proxy: bool = False, limitador: Optional[sec.LimitadorIntentos] = None, roles_2fa: Tuple[str, ...] = (),
                 perimetro_token: Optional[str] = None, reloj_totp: Callable[[], float] = time.time, proxy_saltos: int = 1):
        self.servicio, self.estado, self.https = servicio, estado, https
        self.roles_2fa = tuple(roles_2fa)                  # roles que DEBEN tener segundo factor activo
        self.perimetro_token = perimetro_token or None     # si se define, toda peticion debe traer X-Perimeter-Token (acceso solo via el perimetro)
        self.reloj_totp = reloj_totp
        self.retos: Dict[str, dict] = {}                   # login en dos pasos: reto -> {usuario_id, expira, intentos}
        self.hosts = {h.lower() for h in (hosts_permitidos or [])} | set(LOOPBACK)
        self.confiar_proxy = confiar_proxy
        self.proxy_saltos = max(1, int(proxy_saltos))      # proxies de confianza delante de la app (X-Forwarded-For: se toma la entrada que ellos agregaron)
        self.limitador = limitador or sec.LimitadorIntentos()
        from .flujo import FlujoOperativo
        self.flujo = FlujoOperativo(servicio)


class Peticion:
    def __init__(self, metodo: str, ruta: str, qs: dict, cuerpo: dict, usuario: Optional[dict], csrf: Optional[str], ip: str, params: dict, cabeceras: Optional[dict] = None):
        self.metodo, self.ruta, self.qs, self.cuerpo, self.usuario, self.csrf, self.ip, self.params = metodo, ruta, qs, cuerpo, usuario, csrf, ip, params
        self.cabeceras = cabeceras or {}

    def q(self, k: str, d: str = "") -> str:
        return (self.qs.get(k, [d])[0] or d).strip()

    def cabecera(self, nombre: str) -> str:
        return (self.cabeceras.get(nombre.lower()) or "").strip()


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
    try:
        with ctx.estado.tx() as c:
            c.execute("SELECT 1").fetchone()
    except Exception:  # noqa: BLE001 - sirve a monitores/reinicio automatico: 503 si la BD no responde
        return Respuesta(503, {"status": "error", "db": "error"})
    return Respuesta(datos={"status": "ok", "db": "ok"})


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
    if u["totp_activo"]:  # contraseña correcta pero falta el segundo factor: aun NO hay sesion
        ahora = time.time()
        for k in [k for k, v in ctx.retos.items() if v["expira"] < ahora]:
            ctx.retos.pop(k, None)
        reto = sec.nuevo_token()
        ctx.retos[sec.hash_token(reto)] = {"usuario": u, "expira": ahora + 300, "intentos": 0, "ip": p.ip}
        return Respuesta(datos={"requiere_2fa": True, "reto": reto})
    return _abrir_sesion(ctx, u, p)


def _abrir_sesion(ctx: Contexto, u: dict, p: Peticion):
    ses = ctx.estado.crear_sesion(u["id"], p.ip)
    ck = f"{COOKIE}={ses['token']}; HttpOnly; SameSite=Strict; Path=/; Max-Age={sec.SESION_MAX_SEG}" + ("; Secure" if ctx.https else "")
    return Respuesta(datos={"usuario": u, "csrf": ses["csrf"], "debe_cambiar_password": bool(u["debe_cambiar_password"]),
                            "debe_configurar_2fa": u["rol"] in ctx.roles_2fa and not u["totp_activo"]}, cookies=[ck])


@endpoint("POST", "/api/auth/login/2fa", roles=None, publico=True)
def login_2fa(ctx: Contexto, p: Peticion):
    h = sec.hash_token(_cuerpo_str(p, "reto"))
    reto = ctx.retos.get(h)
    if not reto or reto["expira"] < time.time():
        ctx.retos.pop(h, None)
        return Respuesta(401, {"error": "El paso de verificación expiró. Vuelva a iniciar sesión."})
    reto["intentos"] += 1
    if reto["intentos"] > 5:
        ctx.retos.pop(h, None)
        return Respuesta(429, {"error": "Demasiados intentos de código. Vuelva a iniciar sesión."})
    u = reto["usuario"]
    if not ctx.estado.totp_verificar_login(u["id"], str(p.cuerpo.get("codigo") or ""), ctx.reloj_totp()):
        with ctx.estado.tx() as c:
            ctx.estado.auditar(c, u, "login.fallido", "usuario", str(u["id"]), {"motivo": "2fa"}, p.ip)
        return Respuesta(401, {"error": "Código incorrecto."})
    ctx.retos.pop(h, None)
    return _abrir_sesion(ctx, u, p)


@endpoint("POST", "/api/auth/2fa/iniciar", roles=TODOS)
def tfa_iniciar(ctx: Contexto, p: Peticion):
    secreto = ctx.estado.totp_preparar(p.usuario["id"])
    return Respuesta(datos={"secreto": secreto, "uri": sec.totp_uri(secreto, p.usuario["usuario"]),
                            "instrucciones": "Agregue la cuenta en su app de autenticación (Google/Microsoft Authenticator, Authy, 1Password) con la clave o el enlace y confirme con el código de 6 dígitos."})


@endpoint("POST", "/api/auth/2fa/activar", roles=TODOS)
def tfa_activar(ctx: Contexto, p: Peticion):
    codigos = ctx.estado.totp_activar(p.usuario, p.usuario["id"], str(p.cuerpo.get("codigo") or ""), ctx.reloj_totp())
    return Respuesta(datos={"activo": True, "codigos_recuperacion": codigos, "aviso": "Guárdelos ahora: cada código sirve una sola vez y no se volverán a mostrar."})


@endpoint("POST", "/api/admin/usuarios/{uid}/2fa/restablecer", roles=(ADM,))
def tfa_restablecer(ctx: Contexto, p: Peticion):
    ctx.estado.totp_restablecer(p.usuario, int(p.params["uid"]))
    return Respuesta(datos={"ok": True})


@endpoint("POST", "/api/auth/logout", roles=TODOS)
def logout(ctx: Contexto, p: Peticion):
    ctx.estado.cerrar_sesion(p.cuerpo.get("_token", ""))
    return Respuesta(datos={"ok": True}, cookies=[f"{COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0" + ("; Secure" if ctx.https else "")])


@endpoint("GET", "/api/auth/me", roles=TODOS)
def me(ctx: Contexto, p: Peticion):
    return Respuesta(datos={"usuario": p.usuario, "csrf": p.csrf, "debe_configurar_2fa": p.usuario["rol"] in ctx.roles_2fa and not p.usuario["totp_activo"]})


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
        d["detalle"] = ctx.servicio.detalle_fuente()          # cortes por entidad, exclusiones, motivo de no publicable (solo gerencia/admin)
    return Respuesta(datos=d)


@endpoint("POST", "/api/fuente/recargar", roles=LECTURA_GLOBAL)
def fuente_recargar(ctx: Contexto, p: Peticion):
    return Respuesta(datos=ctx.servicio.recargar_fuente(p.usuario, p.ip))


@endpoint("GET", "/api/fuente/discrepancias", roles=LECTURA_GLOBAL)
def fuente_discrepancias(ctx: Contexto, p: Peticion):
    return Respuesta(datos=ctx.servicio.discrepancias_fuente(p.usuario, p.ip))


@endpoint("GET", "/api/fuente/conciliacion", roles=LECTURA_GLOBAL)
def fuente_conciliacion(ctx: Contexto, p: Peticion):
    return Respuesta(datos=ctx.servicio.conciliacion_fuente(p.usuario, p.ip))


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
                    len(s.alertas_cobro), len(s.alertas_historicas), (f"{dec.actor}: {dec.motivo}" + ("" if s.decision_aplicada else " [solo trazabilidad: prevalece el sistema]")) if dec else ""])
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
    def monto(k):
        v = p.cuerpo.get(k)
        if v in (None, ""):
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            raise ErrorNegocio(f"El {k} debe ser numérico.")
        try:
            return round(float(v), 2)
        except ValueError as e:
            raise ErrorNegocio(f"El {k} debe ser numérico.") from e
    v = ctx.estado.registrar_visita(p.usuario, r.route_id, _cuerpo_str(p, "fecha_salida"), _cuerpo_str(p, "codigo"), _cuerpo_str(p, "estado"),
                                    _cuerpo_str(p, "motivo", False), _cuerpo_str(p, "observacion", False), monto("cobro"), monto("venta"))
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


# ----------------------------------------------------------------------------- salidas, hojas de visita y seguimiento
def _salida_visible(ctx: Contexto, p: Peticion) -> dict:
    sal = ctx.estado.obtener_salida(int(p.params["sid"])) if p.params["sid"].isdigit() else {}
    if not sal or sal["route_id"] not in _visibles(ctx, p.usuario):
        raise ErrorNegocio("Salida no encontrada.", 404)
    return sal


@endpoint("GET", "/api/salidas")
def salidas(ctx: Contexto, p: Peticion):
    vis = _visibles(ctx, p.usuario)
    rid = p.q("ruta")
    if rid and rid not in vis:
        raise ErrorNegocio("Ruta no encontrada.", 404)
    return Respuesta(datos={"salidas": [s for s in ctx.estado.listar_salidas(rid or None) if s["route_id"] in vis]})


@endpoint("POST", "/api/salidas", roles=LECTURA_GLOBAL)
def salida_crear(ctx: Contexto, p: Peticion):
    return Respuesta(201, ctx.estado.crear_salida(p.usuario, _cuerpo_str(p, "ruta"), _cuerpo_str(p, "fecha_salida"), _cuerpo_str(p, "fecha_regreso", False) or None,
                                                  _cuerpo_str(p, "notas", False)))


@endpoint("GET", "/api/salidas/{sid}")
def salida_detalle(ctx: Contexto, p: Peticion):
    sal = _salida_visible(ctx, p)
    return Respuesta(datos={"salida": sal, "seguimiento": ctx.estado.seguimiento_salida(sal["id"])})


@endpoint("POST", "/api/salidas/{sid}/preparar", roles=LECTURA_GLOBAL)
def salida_preparar(ctx: Contexto, p: Peticion):
    _salida_visible(ctx, p)
    return Respuesta(datos=ctx.flujo.preparar(p.usuario, int(p.params["sid"])))


@endpoint("POST", "/api/salidas/{sid}/revisar", roles=LECTURA_GLOBAL)
def salida_revisar(ctx: Contexto, p: Peticion):
    return Respuesta(datos=ctx.estado.revisar_salida(p.usuario, int(p.params["sid"]), _cuerpo_str(p, "nota")))


@endpoint("POST", "/api/salidas/{sid}/estado", roles=LECTURA_GLOBAL)
def salida_estado(ctx: Contexto, p: Peticion):
    return Respuesta(datos=ctx.estado.cambiar_estado_salida(p.usuario, int(p.params["sid"]), _cuerpo_str(p, "estado")))


@endpoint("GET", "/api/salidas/{sid}/hoja-visita")
def salida_hoja(ctx: Contexto, p: Peticion):
    """Hoja de visita de la salida = documento reutilizable vinculado (no se regenera). El vendedor solo la de sus rutas."""
    sal = _salida_visible(ctx, p)
    return Respuesta(datos=ctx.estado.hoja_de_salida(sal["id"]))


@endpoint("GET", "/api/rutas/{rid}/omisiones")
def ruta_omisiones(ctx: Contexto, p: Peticion):
    r = _ruta_visible(ctx, p.usuario, p.params["rid"])
    return Respuesta(datos=ctx.estado.omisiones_consecutivas(r.route_id, max(2, min(6, int(p.q("n", "3") or 3)))))


# ----------------------------------------------------------------------------- incorporacion de clientes a una ruta (confirmacion humana)
@endpoint("POST", "/api/admin/rutas/{rid}/candidatos/buscar", roles=LECTURA_GLOBAL)
def cand_buscar(ctx: Contexto, p: Peticion):
    palabras = p.cuerpo.get("palabras")
    if not isinstance(palabras, list):
        raise ErrorNegocio("'palabras' debe ser una lista.")
    return Respuesta(datos={"sugerencias": ctx.servicio.buscar_candidatos([str(x) for x in palabras], p.params["rid"])})


@endpoint("GET", "/api/admin/rutas/{rid}/candidatos", roles=LECTURA_GLOBAL)
def cand_listar(ctx: Contexto, p: Peticion):
    return Respuesta(datos={"candidatos": ctx.estado.listar_candidatos(p.params["rid"], p.q("estado") or None), "folders_usados": ctx.estado.folders_usados(p.params["rid"])})


@endpoint("POST", "/api/admin/rutas/{rid}/candidatos", roles=LECTURA_GLOBAL)
def cand_proponer(ctx: Contexto, p: Peticion):
    items = p.cuerpo.get("clientes")
    if not isinstance(items, list) or not items:
        raise ErrorNegocio("'clientes' debe ser una lista no vacía de {codigo, nombre, zona}.")
    return Respuesta(201, {"propuestos": ctx.estado.proponer_candidatos(p.usuario, p.params["rid"], items, _cuerpo_str(p, "origen", False) or "manual")})


@endpoint("POST", "/api/admin/rutas/{rid}/candidatos/{cid}/confirmar", roles=LECTURA_GLOBAL)
def cand_confirmar(ctx: Contexto, p: Peticion):
    r = ctx.estado.confirmar_candidato(p.usuario, p.params["rid"], int(p.params["cid"]), _cuerpo_str(p, "zona"), _cuerpo_str(p, "folder", False) or None)
    ctx.servicio.invalidar()
    return Respuesta(201, r)


@endpoint("POST", "/api/admin/rutas/{rid}/candidatos/{cid}/rechazar", roles=LECTURA_GLOBAL)
def cand_rechazar(ctx: Contexto, p: Peticion):
    return Respuesta(datos=ctx.estado.rechazar_candidato(p.usuario, p.params["rid"], int(p.params["cid"]), _cuerpo_str(p, "motivo")))


@endpoint("POST", "/api/admin/rutas/{rid}/zonas", roles=LECTURA_GLOBAL)
def zona_agregar(ctx: Contexto, p: Peticion):
    r = ctx.estado.agregar_zona(p.usuario, p.params["rid"], _cuerpo_str(p, "prefijo"), _cuerpo_str(p, "nombre"))
    ctx.servicio.invalidar()
    return Respuesta(201, r)


@endpoint("POST", "/api/admin/rutas/{rid}/implantacion", roles=(ADM,))
def ruta_implantacion(ctx: Contexto, p: Peticion):
    r = ctx.estado.fijar_implantacion(p.usuario, p.params["rid"], _cuerpo_str(p, "estado"))
    ctx.servicio.invalidar()
    return Respuesta(datos=r)


# ----------------------------------------------------------------------------- documentos reutilizables (hojas de visita) y cola de impresion
def _doc_visible(ctx: Contexto, p: Peticion, did: str) -> dict:
    d = ctx.estado.obtener_documento(int(did)) if did.isdigit() else {}
    if not d or d["route_id"] not in _visibles(ctx, p.usuario):
        raise ErrorNegocio("Documento no encontrado.", 404)
    return d


@endpoint("GET", "/api/rutas/{rid}/documentos")
def ruta_documentos(ctx: Contexto, p: Peticion):
    r = _ruta_visible(ctx, p.usuario, p.params["rid"])
    return Respuesta(datos={"documentos": ctx.estado.listar_documentos(r.route_id), "usa_hojas_de_visita": r.requiere_hojas_visita})


@endpoint("POST", "/api/admin/rutas/{rid}/documentos", roles=(ADM,))
def doc_importar(ctx: Contexto, p: Peticion):
    """Registra la v1 (o una NUEVA VERSION con confirmar=true y motivo). Nunca sobrescribe."""
    d = ctx.servicio.importar_documento(p.usuario, p.params["rid"], _cuerpo_str(p, "archivo"), _cuerpo_str(p, "motivo", False),
                                        bool(p.cuerpo.get("nueva_version")), bool(p.cuerpo.get("confirmar")))
    return Respuesta(201, d)


@endpoint("GET", "/api/documentos/{did}")
def doc_detalle(ctx: Contexto, p: Peticion):
    return Respuesta(datos=_doc_visible(ctx, p, p.params["did"]))


@endpoint("GET", "/api/documentos/{did}/descargar")
def doc_descargar(ctx: Contexto, p: Peticion):
    _doc_visible(ctx, p, p.params["did"])
    d, datos = ctx.servicio.bytes_documento(int(p.params["did"]))
    with ctx.estado.tx() as c:
        ctx.estado.auditar(c, p.usuario, "documento.descargar", "documento", str(d["id"]), {"version": d["version"]}, p.ip)
    return Respuesta(cuerpo=datos, tipo="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     cabeceras={"Content-Disposition": f'attachment; filename="{d["route_id"]}_hojas_de_visita_v{d["version"]}.xlsx"'})


@endpoint("POST", "/api/admin/rutas/{rid}/documentos/{did}/proponer-clientes", roles=LECTURA_GLOBAL)
def doc_proponer(ctx: Contexto, p: Peticion):
    return Respuesta(201, {"propuestos": ctx.servicio.proponer_desde_documento(p.usuario, p.params["rid"], int(p.params["did"]))})


@endpoint("GET", "/api/salidas/{sid}/documentos")
def salida_documentos(ctx: Contexto, p: Peticion):
    sal = _salida_visible(ctx, p)
    return Respuesta(datos={"documentos": ctx.estado.documentos_de_salida(sal["id"])})


@endpoint("GET", "/api/impresion")
def impresion_listar(ctx: Contexto, p: Peticion):
    ids = None if p.usuario["rol"] in LECTURA_GLOBAL else list(_visibles(ctx, p.usuario))
    return Respuesta(datos={"solicitudes": ctx.estado.listar_impresiones(ids, p.q("estado") or None),
                            "nota": "Solo registro de solicitudes: todavía no hay agente de impresión conectado; nada se imprime automáticamente."})


@endpoint("POST", "/api/impresion")
def impresion_solicitar(ctx: Contexto, p: Peticion):
    rid = _cuerpo_str(p, "ruta", False) or None
    if rid:
        _ruta_visible(ctx, p.usuario, rid)
    elif p.usuario["rol"] == VEN:
        raise ErrorNegocio("Indique la ruta.")
    return Respuesta(201, ctx.estado.solicitar_impresion(p.usuario, _cuerpo_str(p, "tipo"), rid, int(p.cuerpo["salida_id"]) if p.cuerpo.get("salida_id") else None,
                                                         int(p.cuerpo["documento_id"]) if p.cuerpo.get("documento_id") else None, _cuerpo_str(p, "referencia", False) or None,
                                                         int(p.cuerpo.get("copias") or 1), _cuerpo_str(p, "destino", False) or None))


@endpoint("POST", "/api/impresion/{iid}/cancelar", roles=LECTURA_GLOBAL)
def impresion_cancelar(ctx: Contexto, p: Peticion):
    return Respuesta(datos=ctx.estado.cancelar_impresion(p.usuario, int(p.params["iid"])))


@endpoint("POST", "/api/admin/agentes-impresion", roles=(ADM,))
def agente_registrar(ctx: Contexto, p: Peticion):
    token = sec.nuevo_token()
    a = ctx.estado.registrar_agente(p.usuario, _cuerpo_str(p, "nombre"), sec.hash_token(token))
    return Respuesta(201, {**a, "token": token, "aviso": "Guarde este token ahora: no se vuelve a mostrar. El agente lo envía en X-Agent-Token."})


def _agente(ctx: Contexto, p: Peticion) -> dict:
    """Los endpoints del agente NO usan sesion de usuario: autentican con su token propio (solo se guarda el hash). Mismo limite de intentos por IP."""
    if ctx.limitador.bloqueado(f"agente|{p.ip}"):
        raise ErrorNegocio("Demasiados intentos.", 429)
    token = p.cabecera("X-Agent-Token")
    ag = ctx.estado.agente_por_token(sec.hash_token(token)) if token else None
    if not ag:
        ctx.limitador.fallo(f"agente|{p.ip}")
        raise ErrorNegocio("Agente no autorizado.", 401)
    return ag


@endpoint("POST", "/api/agente/siguiente", roles=None, publico=True)
def agente_siguiente(ctx: Contexto, p: Peticion):
    ag = _agente(ctx, p)
    t = ctx.estado.agente_tomar_pendiente(ag["id"])
    return Respuesta(datos={"trabajo": t} if t else {"trabajo": None})


@endpoint("POST", "/api/agente/{iid}/resultado", roles=None, publico=True)
def agente_resultado(ctx: Contexto, p: Peticion):
    ag = _agente(ctx, p)
    return Respuesta(datos=ctx.estado.agente_reportar(ag["id"], int(p.params["iid"]), bool(p.cuerpo.get("ok")), str(p.cuerpo.get("detalle") or "")))


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
    if not es_operable(ctx.servicio.fuente):
        raise ErrorNegocio("No hay una fuente utilizable para importar.", 409)
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
                                _cuerpo_str(p, "estado_fisico", False), _cuerpo_str(p, "notas", False),
                                requiere_hojas_visita=p.cuerpo.get("requiere_hojas_visita") if isinstance(p.cuerpo.get("requiere_hojas_visita"), bool) else None)
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



# ----------------------------------------------------------------------------- calendario, ciclo de salidas, avisos, Excel versionado, dashboard
@endpoint("GET", "/api/calendario")
def calendario(ctx: Contexto, p: Peticion):
    vis = _visibles(ctx, p.usuario)
    rid = p.q("ruta")
    if rid and rid not in vis:
        raise ErrorNegocio("Ruta no encontrada.", 404)
    return Respuesta(datos=ctx.flujo.calendario_salidas(vis, p.q("desde") or None, p.q("hasta") or None, rid, p.q("vendedor") if p.usuario["rol"] != VEN else "", p.q("periodo")))


@endpoint("POST", "/api/salidas/serie", roles=LECTURA_GLOBAL)
def salidas_serie(ctx: Contexto, p: Peticion):
    fechas = p.cuerpo.get("fechas")
    if not isinstance(fechas, list) or not fechas or len(fechas) > 62 or not all(isinstance(f, str) for f in fechas):
        raise ErrorNegocio("Indique las fechas: una lista de 1 a 62 fechas AAAA-MM-DD.")
    r = ctx.flujo.programar_salidas(p.usuario, _cuerpo_str(p, "ruta"), fechas)
    return Respuesta(201 if r["creadas"] else 200, r)


@endpoint("POST", "/api/salidas/ciclo", roles=LECTURA_GLOBAL)
def salidas_ciclo(ctx: Contexto, p: Peticion):
    """Prepara las salidas que ya entraron en su ventana de 3 dias habiles (idempotente). El campo hoy solo sirve para ensayo (queda auditado)."""
    return Respuesta(datos=ctx.flujo.ciclo_diario(p.usuario, _cuerpo_str(p, "hoy", False) or None))


@endpoint("GET", "/api/feriados")
def feriados(ctx: Contexto, p: Peticion):
    return Respuesta(datos={"feriados": ctx.estado.listar_feriados(), "semana_laboral": "lunes a sábado (domingo no es hábil)"})


@endpoint("POST", "/api/admin/feriados", roles=LECTURA_GLOBAL)
def feriado_agregar(ctx: Contexto, p: Peticion):
    return Respuesta(201, ctx.estado.agregar_feriado(p.usuario, _cuerpo_str(p, "fecha"), _cuerpo_str(p, "nombre")))


@endpoint("DELETE", "/api/admin/feriados/{fecha}", roles=LECTURA_GLOBAL)
def feriado_quitar(ctx: Contexto, p: Peticion):
    ctx.estado.quitar_feriado(p.usuario, p.params["fecha"])
    return Respuesta(datos={"ok": True})


@endpoint("GET", "/api/comparar-salidas")
def salidas_comparar(ctx: Contexto, p: Peticion):
    try:
        a, b = int(p.q("a")), int(p.q("b"))
    except ValueError as e:
        raise ErrorNegocio("Indique las salidas a comparar (a = anterior, b = posterior).") from e
    vis = _visibles(ctx, p.usuario)
    for i in (a, b):
        s = ctx.estado.obtener_salida(i)
        if not s or s["route_id"] not in vis:
            raise ErrorNegocio("Salida no encontrada.", 404)
    return Respuesta(datos=ctx.estado.comparar_salidas(a, b))


@endpoint("GET", "/api/avisos")
def avisos(ctx: Contexto, p: Peticion):
    vis = _visibles(ctx, p.usuario)
    rid = p.q("ruta")
    if rid and rid not in vis:
        raise ErrorNegocio("Ruta no encontrada.", 404)
    sid = p.q("salida")
    filas = ctx.estado.listar_alertas([rid] if rid else list(vis), p.q("estado") or None, p.q("tipo") or None, int(sid) if sid.isdigit() else None)
    return Respuesta(datos={"avisos": filas, "nota": "Avisos generados por el sistema: informan, no deciden. Un resultado en contado es ROJO automático; gerencia decide solo donde el sistema no puede clasificar (revisión humana / sin evidencia)."})


@endpoint("POST", "/api/avisos/{aid}/atender", roles=LECTURA_GLOBAL)
def aviso_atender(ctx: Contexto, p: Peticion):
    return Respuesta(datos=ctx.estado.atender_alerta(p.usuario, int(p.params["aid"]), _cuerpo_str(p, "estado"), _cuerpo_str(p, "nota", False)))


@endpoint("GET", "/api/notificaciones")
def notificaciones(ctx: Contexto, p: Peticion):
    dest = None if p.usuario["rol"] in LECTURA_GLOBAL else p.usuario["usuario"]
    return Respuesta(datos={"notificaciones": ctx.estado.listar_notificaciones(dest, p.q("estado") or None),
                            "nota": "Todas las notificaciones son SIMULADAS: no se envía ningún mensaje fuera del sistema."})


@endpoint("POST", "/api/salidas/{sid}/excel", roles=LECTURA_GLOBAL)
def salida_excel(ctx: Contexto, p: Peticion):
    _salida_visible(ctx, p)
    e = ctx.flujo.generar_excel_salida(p.usuario, int(p.params["sid"]), _cuerpo_str(p, "base"))
    return Respuesta(201 if e["creada"] else 200, e)


@endpoint("GET", "/api/rutas/{rid}/excel")
def excel_versiones(ctx: Contexto, p: Peticion):
    r = _ruta_visible(ctx, p.usuario, p.params["rid"])
    return Respuesta(datos={"versiones": ctx.estado.listar_excel(r.route_id), "estructura": "Actual/ (vigente) e Historico/ (inmutable)",
                            "integridad": ctx.estado.verificar_excel()})


@endpoint("GET", "/api/excel/{eid}/descargar")
def excel_descargar(ctx: Contexto, p: Peticion):
    import hashlib
    e = next((x for r in _visibles(ctx, p.usuario) for x in ctx.estado.listar_excel(r) if str(x["id"]) == p.params["eid"]), None)
    if not e:
        raise ErrorNegocio("Archivo no encontrado.", 404)
    f = ctx.estado.carpeta_excel / e["archivo"]
    if not f.is_file():
        raise ErrorNegocio("Archivo no disponible.", 404)
    datos = f.read_bytes()
    if hashlib.sha256(datos).hexdigest() != e["sha256"]:
        raise ErrorNegocio("El archivo no coincide con su hash registrado (integridad).", 500)
    ctx.estado_auditar(p, "excel.descargar", p.params["eid"])
    return Respuesta(cuerpo=datos, tipo="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     cabeceras={"Content-Disposition": f'attachment; filename="{Path(e["archivo"]).name}"'})


@endpoint("GET", "/api/dashboard", roles=LECTURA_GLOBAL)
def dashboard(ctx: Contexto, p: Peticion):
    from . import dashboard as dash
    rutas = ctx.servicio.rutas()
    rid = p.q("ruta")
    if rid:
        if rid not in rutas:
            raise ErrorNegocio("Ruta no encontrada.", 404)
        rutas = {rid: rutas[rid]}
    return Respuesta(datos=dash.construir(ctx.servicio, ctx.estado, rutas, ctx.servicio.reporting, global_view=not rid))


@endpoint("GET", "/api/dashboard/mio")
def dashboard_mio(ctx: Contexto, p: Peticion):
    from . import dashboard as dash
    return Respuesta(datos=dash.construir(ctx.servicio, ctx.estado, _visibles(ctx, p.usuario), None, global_view=False))


@endpoint("POST", "/api/salidas/{sid}/impresion", roles=LECTURA_GLOBAL)
def salida_impresion(ctx: Contexto, p: Peticion):
    """Prepara (idempotente) las solicitudes de impresion de la salida: hoja de visita vinculada y Excel vigente si NO es borrador. Solo registra."""
    _salida_visible(ctx, p)
    try:
        copias = int(p.cuerpo.get("copias") or 1)
    except (TypeError, ValueError) as e:
        raise ErrorNegocio("copias debe ser un número entero (1 a 20).") from e
    if not 1 <= copias <= 20:
        raise ErrorNegocio("Copias fuera de rango (1 a 20).")
    return Respuesta(datos=ctx.flujo.preparar_impresion(p.usuario, int(p.params["sid"]), copias))


@endpoint("POST", "/api/agente/{iid}/archivo", roles=None, publico=True)
def agente_archivo(ctx: Contexto, p: Peticion):
    ag = _agente(ctx, p)
    nombre, datos, sha = ctx.estado.archivo_para_agente(ag["id"], int(p.params["iid"]))
    return Respuesta(cuerpo=datos, tipo="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     cabeceras={"Content-Disposition": f'attachment; filename="{nombre}"', "X-Content-SHA256": sha})


@endpoint("GET", "/api/configuracion", roles=LECTURA_GLOBAL)
def configuracion_leer(ctx: Contexto, p: Peticion):
    return Respuesta(datos={"valores": ctx.estado.configuracion(), "por_defecto": ctx.estado.CONFIG_DEFECTO, "rangos": ctx.estado.CONFIG_RANGOS,
                            "descripcion": {"umbral_aumento_mora_dias": "Aviso cuando la mora promedio aumenta esta cantidad de días o más entre salidas (aprobado: 5).",
                                            "oportunidad_ventana_dias": "Ventana (días antes del corte) para contar compras de un cliente A (aprobado: 180).",
                                            "oportunidad_max_facturas": "Oportunidad comercial informativa si el cliente A tiene esta cantidad de facturas o menos en la ventana (aprobado: 2).",
                                            "vinetas_excel": "1 = el Excel de ruta lleva el relleno de viñeta (solo con fuente validada y clientes con clase determinada). 0 = desactivado (por defecto): ubicación de la viñeta pendiente de confirmar con el Excel aprobado."}})


@endpoint("POST", "/api/admin/configuracion", roles=LECTURA_GLOBAL)
def configuracion_fijar(ctx: Contexto, p: Peticion):
    return Respuesta(datos={"valores": ctx.estado.fijar_configuracion(p.usuario, _cuerpo_str(p, "clave"), p.cuerpo.get("valor"))})


def make_handler(ctx: Contexto, web_dir: Optional[Path]):
    ctx.estado_auditar = _estado_auditar(ctx)

    class Handler(BaseHTTPRequestHandler):
        server_version = "cxc-portal"
        sys_version = ""
        protocol_version = "HTTP/1.0"  # una peticion por conexion: sin cuerpos sin leer ni conexiones colgadas
        timeout = SOCKET_TIMEOUT_SEG   # conexiones lentas/colgadas (slowloris) liberan su hilo

        def log_message(self, fmt, *args):
            pass

        # -- utilidades
        def _ip(self) -> str:
            if ctx.confiar_proxy:
                # El cliente controla las PRIMERAS entradas de X-Forwarded-For; solo es confiable la que agregaron NUESTROS proxies (las ultimas).
                partes = [x.strip() for x in (self.headers.get("X-Forwarded-For") or "").split(",") if x.strip()]
                if len(partes) >= ctx.proxy_saltos:
                    return partes[-ctx.proxy_saltos][:64]
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
            es_salud = self.command in ("GET", "HEAD") and urlparse(self.path).path == "/api/health"   # los monitores de la plataforma usan su propio Host; solo responde ok/db
            if not es_salud and not self._host_ok():
                return self._enviar(Respuesta(403, {"error": "Host no permitido."}))
            if ctx.perimetro_token and urlparse(self.path).path != "/api/health":
                dado = self.headers.get("X-Perimeter-Token") or ""
                if not hmac.compare_digest(dado.encode(), ctx.perimetro_token.encode()):
                    return self._enviar(Respuesta(403, {"error": "Acceso directo no permitido: use la dirección oficial del portal."}))
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
                    if (usuario["rol"] in ctx.roles_2fa and not usuario["totp_activo"]
                            and not ruta.startswith("/api/auth/2fa/") and ruta not in ("/api/auth/me", "/api/auth/logout", "/api/auth/password")):
                        return self._enviar(Respuesta(403, {"error": "Debe activar el segundo factor de autenticación antes de continuar.", "debe_configurar_2fa": True}))
                    if roles is not None and usuario["rol"] not in roles:
                        with ctx.estado.tx() as c:
                            ctx.estado.auditar(c, usuario, "acceso.denegado", "api", ruta, {"metodo": metodo}, self._ip())
                        return self._enviar(Respuesta(403, {"error": "No tiene permiso para esta operación."}))
                    if metodo != "GET" and not hmac.compare_digest(self.headers.get("X-CSRF-Token") or "", ses["csrf"]):
                        return self._enviar(Respuesta(403, {"error": "Token CSRF inválido."}))
                cuerpo = self._cuerpo() if metodo in ("POST", "PATCH", "PUT", "DELETE") else {}
                if fn is logout:
                    cuerpo["_token"] = token
                p = Peticion(metodo, ruta, parse_qs(u.query), cuerpo, ses["usuario"] if ses else None, ses["csrf"] if ses else None, self._ip(), m.groupdict(),
                             {k.lower(): v for k, v in self.headers.items()})
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
