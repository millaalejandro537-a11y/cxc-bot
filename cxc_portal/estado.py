"""Base de datos PROPIA del portal (SQLite, archivo en el directorio de ESTADO privado, fuera de Git).

Separada de SAP, de Cosprobell BD y de cualquier recurso de BLC: aqui solo viven datos operativos NUESTROS.
    usuarios, sesiones            autenticacion y roles
    vendedores, rutas, zonas, clientes_ruta   catalogo operativo (orden de folders incluido) editable sin tocar codigo
    decisiones                    excepciones/decisiones de gerencia (versionadas, nunca se borran)
    visitas, visita_eventos       registro de visitas con trazabilidad
    auditoria                     quien hizo que (inmutable)
Nada financiero de SAP se guarda aqui: las facturas/recibos se leen de la fuente de datos (snapshot hoy; en vivo mañana).
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import security as sec

SCHEMA_VERSION = 1

DDL = """
CREATE TABLE IF NOT EXISTS meta (clave TEXT PRIMARY KEY, valor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY, usuario TEXT NOT NULL UNIQUE COLLATE NOCASE, nombre TEXT NOT NULL,
    rol TEXT NOT NULL CHECK (rol IN ('admin','gerencia','vendedor')),
    vendedor_id INTEGER REFERENCES vendedores(id), password_hash TEXT NOT NULL,
    activo INTEGER NOT NULL DEFAULT 1, debe_cambiar_password INTEGER NOT NULL DEFAULT 1,
    creado TEXT NOT NULL, ultimo_acceso TEXT);
CREATE TABLE IF NOT EXISTS sesiones (
    token_hash TEXT PRIMARY KEY, usuario_id INTEGER NOT NULL REFERENCES usuarios(id), csrf TEXT NOT NULL,
    creada TEXT NOT NULL, ultimo_uso TEXT NOT NULL, expira TEXT NOT NULL, ip TEXT);
CREATE TABLE IF NOT EXISTS vendedores (id INTEGER PRIMARY KEY, nombre TEXT NOT NULL UNIQUE COLLATE NOCASE, activo INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS rutas (
    route_id TEXT PRIMARY KEY, nombre TEXT NOT NULL, vendedor_id INTEGER NOT NULL REFERENCES vendedores(id),
    tipo_ruta TEXT NOT NULL CHECK (tipo_ruta IN ('FORANEA','LOCAL')), plazo_dias INTEGER NOT NULL CHECK (plazo_dias > 0),
    estado_fisico TEXT NOT NULL DEFAULT '', notas TEXT NOT NULL DEFAULT '', activa INTEGER NOT NULL DEFAULT 1, creada TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS zonas (
    route_id TEXT NOT NULL REFERENCES rutas(route_id), prefijo TEXT NOT NULL, nombre TEXT NOT NULL, PRIMARY KEY (route_id, prefijo));
CREATE TABLE IF NOT EXISTS clientes_ruta (
    route_id TEXT NOT NULL REFERENCES rutas(route_id), codigo TEXT NOT NULL, folder TEXT NOT NULL, zona TEXT NOT NULL,
    nombre TEXT NOT NULL, orden INTEGER NOT NULL, PRIMARY KEY (route_id, codigo), UNIQUE (route_id, folder));
CREATE TABLE IF NOT EXISTS decisiones (
    id INTEGER PRIMARY KEY, codigo_cliente TEXT NOT NULL, route_id TEXT, clasificacion_final TEXT NOT NULL
        CHECK (clasificacion_final IN ('A','B','C','SOLO_CONTADO','EN_PRUEBA')),
    motivo TEXT NOT NULL, calculado_al_decidir TEXT, promedio_al_decidir REAL, fuente_fecha_corte TEXT,
    usuario_id INTEGER REFERENCES usuarios(id), actor TEXT NOT NULL, fecha TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'vigente' CHECK (estado IN ('vigente','sustituida','anulada')),
    anulada_por TEXT, anulada_motivo TEXT, anulada_en TEXT, origen TEXT NOT NULL DEFAULT 'portal');
CREATE INDEX IF NOT EXISTS idx_decisiones_cliente ON decisiones(codigo_cliente, estado);
CREATE TABLE IF NOT EXISTS visitas (
    id INTEGER PRIMARY KEY, route_id TEXT NOT NULL REFERENCES rutas(route_id), fecha_salida TEXT NOT NULL,
    codigo_cliente TEXT NOT NULL, estado TEXT NOT NULL CHECK (estado IN ('prevista','visitada_firmada','visitada_sin_firma','no_visitada')),
    motivo TEXT NOT NULL DEFAULT '', observacion TEXT NOT NULL DEFAULT '', registrada_por INTEGER NOT NULL REFERENCES usuarios(id),
    creada TEXT NOT NULL, actualizada TEXT NOT NULL, UNIQUE (route_id, fecha_salida, codigo_cliente));
CREATE TABLE IF NOT EXISTS visita_eventos (
    id INTEGER PRIMARY KEY, visita_id INTEGER NOT NULL REFERENCES visitas(id), ts TEXT NOT NULL, usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    estado_anterior TEXT, estado_nuevo TEXT NOT NULL, motivo TEXT NOT NULL DEFAULT '', observacion TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS auditoria (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, usuario TEXT, rol TEXT, accion TEXT NOT NULL, entidad TEXT, referencia TEXT, detalle TEXT, ip TEXT);
"""

CLASIFICACIONES_DECISION = ("A", "B", "C", "SOLO_CONTADO", "EN_PRUEBA")
ESTADOS_VISITA = ("prevista", "visitada_firmada", "visitada_sin_firma", "no_visitada")


class ErrorNegocio(Exception):
    """Error de validacion o regla de negocio; el mensaje es apto para mostrar al usuario."""

    def __init__(self, mensaje: str, codigo: int = 400):
        super().__init__(mensaje)
        self.mensaje, self.codigo = mensaje, codigo


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Estado:
    """Acceso a la BD propia. Una conexion por llamada (hilos seguros); WAL; claves foraneas activas."""

    def __init__(self, ruta: Path, reloj=ahora):
        self.ruta = Path(ruta)
        self.reloj = reloj
        self._lock = threading.Lock()
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        with self.tx() as c:
            c.executescript(DDL)
            c.execute("INSERT OR IGNORE INTO meta(clave, valor) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))

    @contextmanager
    def tx(self):
        con = sqlite3.connect(self.ruta, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA journal_mode = WAL")
        try:
            with self._lock:
                yield con
                con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _t(self) -> str:
        return iso(self.reloj())

    # ------------------------------------------------------------------ auditoria
    def auditar(self, con, actor: Optional[dict], accion: str, entidad: str = "", referencia: str = "", detalle: Optional[dict] = None, ip: str = "") -> None:
        con.execute("INSERT INTO auditoria(ts, usuario, rol, accion, entidad, referencia, detalle, ip) VALUES (?,?,?,?,?,?,?,?)",
                    (self._t(), (actor or {}).get("usuario"), (actor or {}).get("rol"), accion, entidad, referencia,
                     json.dumps(detalle, ensure_ascii=False, default=str) if detalle is not None else None, ip))

    def listar_auditoria(self, limite: int = 200) -> List[dict]:
        with self.tx() as c:
            return [dict(r) for r in c.execute("SELECT * FROM auditoria ORDER BY id DESC LIMIT ?", (limite,))]

    # ------------------------------------------------------------------ vendedores / usuarios
    def asegurar_vendedor(self, con, nombre: str) -> int:
        nombre = nombre.strip()
        if not nombre:
            raise ErrorNegocio("El nombre del vendedor es obligatorio.")
        r = con.execute("SELECT id FROM vendedores WHERE nombre = ?", (nombre,)).fetchone()
        if r:
            return r["id"]
        return con.execute("INSERT INTO vendedores(nombre) VALUES (?)", (nombre,)).lastrowid

    def crear_vendedor(self, actor: dict, nombre: str) -> dict:
        with self.tx() as c:
            vid = self.asegurar_vendedor(c, nombre)
            self.auditar(c, actor, "vendedor.crear", "vendedor", str(vid), {"nombre": nombre})
            return dict(c.execute("SELECT * FROM vendedores WHERE id = ?", (vid,)).fetchone())

    def listar_vendedores(self) -> List[dict]:
        with self.tx() as c:
            return [dict(r) for r in c.execute("SELECT * FROM vendedores ORDER BY nombre")]

    def crear_usuario(self, actor: Optional[dict], usuario: str, nombre: str, rol: str, password: str, vendedor: Optional[str] = None,
                      debe_cambiar: bool = True) -> dict:
        usuario = usuario.strip()
        if not re.fullmatch(r"[A-Za-z0-9._@-]{3,64}", usuario):
            raise ErrorNegocio("Usuario inválido (3-64 caracteres: letras, números . _ @ -).")
        if rol not in sec.ROLES:
            raise ErrorNegocio("Rol inválido.")
        try:
            sec.validar_password(password, usuario)
        except sec.PasswordInvalida as e:
            raise ErrorNegocio(str(e)) from e
        with self.tx() as c:
            vid = None
            if rol == "vendedor":
                if not vendedor:
                    raise ErrorNegocio("Un usuario con rol vendedor debe estar vinculado a un vendedor.")
                vid = self.asegurar_vendedor(c, vendedor)
            if c.execute("SELECT 1 FROM usuarios WHERE usuario = ?", (usuario,)).fetchone():
                raise ErrorNegocio("Ese usuario ya existe.", 409)
            uid = c.execute("INSERT INTO usuarios(usuario, nombre, rol, vendedor_id, password_hash, debe_cambiar_password, creado) VALUES (?,?,?,?,?,?,?)",
                            (usuario, nombre.strip() or usuario, rol, vid, sec.hash_password(password), int(debe_cambiar), self._t())).lastrowid
            self.auditar(c, actor, "usuario.crear", "usuario", str(uid), {"usuario": usuario, "rol": rol, "vendedor": vendedor})
            return self._usuario(c, uid)

    def _usuario(self, c, uid: int) -> dict:
        r = c.execute("SELECT u.id, u.usuario, u.nombre, u.rol, u.activo, u.debe_cambiar_password, u.vendedor_id, v.nombre AS vendedor "
                      "FROM usuarios u LEFT JOIN vendedores v ON v.id = u.vendedor_id WHERE u.id = ?", (uid,)).fetchone()
        return dict(r) if r else {}

    def listar_usuarios(self) -> List[dict]:
        with self.tx() as c:
            return [dict(r) for r in c.execute(
                "SELECT u.id, u.usuario, u.nombre, u.rol, u.activo, u.debe_cambiar_password, v.nombre AS vendedor, u.ultimo_acceso "
                "FROM usuarios u LEFT JOIN vendedores v ON v.id = u.vendedor_id ORDER BY u.usuario")]

    def actualizar_usuario(self, actor: dict, uid: int, activo: Optional[bool] = None, rol: Optional[str] = None, password: Optional[str] = None) -> dict:
        with self.tx() as c:
            u = c.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone()
            if not u:
                raise ErrorNegocio("Usuario no encontrado.", 404)
            if activo is not None:
                if uid == actor.get("id") and not activo:
                    raise ErrorNegocio("No puede desactivar su propia cuenta.")
                c.execute("UPDATE usuarios SET activo = ? WHERE id = ?", (int(activo), uid))
                if not activo:
                    c.execute("DELETE FROM sesiones WHERE usuario_id = ?", (uid,))
            if rol is not None:
                if rol not in sec.ROLES:
                    raise ErrorNegocio("Rol inválido.")
                if rol == "vendedor" and not u["vendedor_id"]:
                    raise ErrorNegocio("Para rol vendedor el usuario debe estar vinculado a un vendedor.")
                if uid == actor.get("id") and rol != "admin":
                    raise ErrorNegocio("No puede quitarse a sí mismo el rol de administrador.")
                c.execute("UPDATE usuarios SET rol = ? WHERE id = ?", (rol, uid))
                c.execute("DELETE FROM sesiones WHERE usuario_id = ?", (uid,))
            if password is not None:
                try:
                    sec.validar_password(password, u["usuario"])
                except sec.PasswordInvalida as e:
                    raise ErrorNegocio(str(e)) from e
                c.execute("UPDATE usuarios SET password_hash = ?, debe_cambiar_password = 1 WHERE id = ?", (sec.hash_password(password), uid))
                c.execute("DELETE FROM sesiones WHERE usuario_id = ?", (uid,))
            self.auditar(c, actor, "usuario.actualizar", "usuario", str(uid), {"activo": activo, "rol": rol, "password": password is not None})
            return self._usuario(c, uid)

    def cambiar_password(self, usuario_id: int, actual: str, nueva: str, actor: dict) -> None:
        with self.tx() as c:
            u = c.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
            if not u or not sec.verificar_password(actual, u["password_hash"]):
                raise ErrorNegocio("La contraseña actual no es correcta.", 403)
            try:
                sec.validar_password(nueva, u["usuario"])
            except sec.PasswordInvalida as e:
                raise ErrorNegocio(str(e)) from e
            if actual == nueva:
                raise ErrorNegocio("La nueva contraseña debe ser distinta.")
            c.execute("UPDATE usuarios SET password_hash = ?, debe_cambiar_password = 0 WHERE id = ?", (sec.hash_password(nueva), usuario_id))
            c.execute("DELETE FROM sesiones WHERE usuario_id = ?", (usuario_id,))
            self.auditar(c, actor, "usuario.cambiar_password", "usuario", str(usuario_id))

    # ------------------------------------------------------------------ autenticacion y sesiones
    def autenticar(self, usuario: str, password: str, ip: str = "") -> Optional[dict]:
        with self.tx() as c:
            u = c.execute("SELECT * FROM usuarios WHERE usuario = ?", (usuario.strip(),)).fetchone()
            if not u:
                sec.verificar_inexistente(password)
                self.auditar(c, {"usuario": usuario[:64]}, "login.fallido", "usuario", "", {"motivo": "usuario_inexistente"}, ip)
                return None
            ok = sec.verificar_password(password, u["password_hash"])
            if not ok or not u["activo"]:
                self.auditar(c, {"usuario": u["usuario"], "rol": u["rol"]}, "login.fallido", "usuario", str(u["id"]),
                             {"motivo": "clave" if not ok else "inactivo"}, ip)
                return None
            c.execute("UPDATE usuarios SET ultimo_acceso = ? WHERE id = ?", (self._t(), u["id"]))
            res = self._usuario(c, u["id"])
            self.auditar(c, res, "login.ok", "usuario", str(u["id"]), None, ip)
            return res

    def crear_sesion(self, usuario_id: int, ip: str = "") -> Dict[str, str]:
        token, csrf = sec.nuevo_token(), sec.nuevo_token()
        t = self.reloj()
        with self.tx() as c:
            c.execute("DELETE FROM sesiones WHERE expira < ?", (iso(t),))
            c.execute("INSERT INTO sesiones(token_hash, usuario_id, csrf, creada, ultimo_uso, expira, ip) VALUES (?,?,?,?,?,?,?)",
                      (sec.hash_token(token), usuario_id, csrf, iso(t), iso(t), iso(t + timedelta(seconds=sec.SESION_MAX_SEG)), ip))
        return {"token": token, "csrf": csrf}

    def sesion(self, token: str) -> Optional[dict]:
        """Devuelve {'usuario': {...}, 'csrf': ...} si la sesion es valida (no expirada ni inactiva) y renueva el uso."""
        if not token:
            return None
        t = self.reloj()
        with self.tx() as c:
            s = c.execute("SELECT * FROM sesiones WHERE token_hash = ?", (sec.hash_token(token),)).fetchone()
            if not s:
                return None
            ultimo = datetime.strptime(s["ultimo_uso"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            expira = datetime.strptime(s["expira"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if t >= expira or (t - ultimo).total_seconds() > sec.SESION_IDLE_SEG:
                c.execute("DELETE FROM sesiones WHERE token_hash = ?", (s["token_hash"],))
                return None
            u = self._usuario(c, s["usuario_id"])
            if not u or not u["activo"]:
                c.execute("DELETE FROM sesiones WHERE token_hash = ?", (s["token_hash"],))
                return None
            c.execute("UPDATE sesiones SET ultimo_uso = ? WHERE token_hash = ?", (iso(t), s["token_hash"]))
            return {"usuario": u, "csrf": s["csrf"]}

    def cerrar_sesion(self, token: str) -> None:
        if token:
            with self.tx() as c:
                c.execute("DELETE FROM sesiones WHERE token_hash = ?", (sec.hash_token(token),))

    # ------------------------------------------------------------------ catalogo: rutas, zonas, clientes
    def guardar_ruta(self, actor: Optional[dict], route_id: str, nombre: str, vendedor: str, tipo_ruta: str, plazo_dias: Optional[int] = None,
                     zonas: Optional[Dict[str, str]] = None, clientes: Optional[List[dict]] = None, estado_fisico: str = "", notas: str = "",
                     reemplazar_clientes: bool = True) -> dict:
        route_id = re.sub(r"[^A-Z0-9_]", "_", route_id.strip().upper())
        if not route_id or not nombre.strip():
            raise ErrorNegocio("route_id y nombre son obligatorios.")
        tipo_ruta = tipo_ruta.upper()
        if tipo_ruta not in ("FORANEA", "LOCAL"):
            raise ErrorNegocio("tipo_ruta debe ser FORANEA o LOCAL.")
        plazo = int(plazo_dias) if plazo_dias else (60 if tipo_ruta == "FORANEA" else 30)
        if plazo <= 0 or plazo > 365:
            raise ErrorNegocio("plazo_dias fuera de rango.")
        with self.tx() as c:
            vid = self.asegurar_vendedor(c, vendedor)
            existe = c.execute("SELECT 1 FROM rutas WHERE route_id = ?", (route_id,)).fetchone()
            if existe:
                c.execute("UPDATE rutas SET nombre=?, vendedor_id=?, tipo_ruta=?, plazo_dias=?, estado_fisico=?, notas=? WHERE route_id=?",
                          (nombre.strip(), vid, tipo_ruta, plazo, estado_fisico, notas, route_id))
            else:
                c.execute("INSERT INTO rutas(route_id, nombre, vendedor_id, tipo_ruta, plazo_dias, estado_fisico, notas, creada) VALUES (?,?,?,?,?,?,?,?)",
                          (route_id, nombre.strip(), vid, tipo_ruta, plazo, estado_fisico, notas, self._t()))
            if zonas is not None:
                c.execute("DELETE FROM zonas WHERE route_id = ?", (route_id,))
                for pref, nom in zonas.items():
                    c.execute("INSERT INTO zonas(route_id, prefijo, nombre) VALUES (?,?,?)", (route_id, pref.strip().upper(), nom.strip()))
            if clientes is not None:
                self._reemplazar_clientes(c, route_id, clientes, reemplazar_clientes)
            self.auditar(c, actor, "ruta.guardar" if existe else "ruta.crear", "ruta", route_id,
                         {"nombre": nombre, "vendedor": vendedor, "plazo_dias": plazo, "clientes": len(clientes) if clientes is not None else None})
        return self.obtener_ruta(route_id)

    def _reemplazar_clientes(self, c, route_id: str, clientes: List[dict], reemplazar: bool) -> None:
        vistos, folders = set(), set()
        norm = []
        for i, cl in enumerate(clientes, 1):
            cod = str(cl.get("codigo", "")).strip().upper()
            folder = str(cl.get("folder", "")).strip()
            if not re.fullmatch(r"C\d{3,8}", cod):
                raise ErrorNegocio(f"Código de cliente inválido en la fila {i}: '{cod}'.")
            if not folder:
                raise ErrorNegocio(f"Falta el folder del cliente {cod}.")
            if cod in vistos:
                raise ErrorNegocio(f"Cliente duplicado en la ruta: {cod}.")
            if folder in folders:
                raise ErrorNegocio(f"Folder duplicado en la ruta: {folder}.")
            vistos.add(cod)
            folders.add(folder)
            m = re.match(r"^\s*\d+\s+([A-Za-z]{1,4})\s*$", folder)
            zona = (cl.get("zona") or (m.group(1) if m else "")).strip().upper()
            norm.append((route_id, cod, folder, zona, str(cl.get("nombre", "")).strip(), i))
        if reemplazar:
            c.execute("DELETE FROM clientes_ruta WHERE route_id = ?", (route_id,))
        c.executemany("INSERT OR REPLACE INTO clientes_ruta(route_id, codigo, folder, zona, nombre, orden) VALUES (?,?,?,?,?,?)", norm)

    def agregar_cliente(self, actor: dict, route_id: str, codigo: str, nombre: str, zona: str) -> dict:
        """Alta nueva: toma el siguiente numero de SU zona (ej. 10 LV) sin renumerar los folders existentes."""
        codigo, zona = codigo.strip().upper(), zona.strip().upper()
        if not re.fullmatch(r"C\d{3,8}", codigo) or not re.fullmatch(r"[A-Z]{1,4}", zona):
            raise ErrorNegocio("Código o zona inválidos.")
        with self.tx() as c:
            if not c.execute("SELECT 1 FROM rutas WHERE route_id = ?", (route_id,)).fetchone():
                raise ErrorNegocio("Ruta no encontrada.", 404)
            if c.execute("SELECT 1 FROM clientes_ruta WHERE route_id = ? AND codigo = ?", (route_id, codigo)).fetchone():
                raise ErrorNegocio("El cliente ya está en la ruta.", 409)
            nums = [int(m.group(1)) for r in c.execute("SELECT folder FROM clientes_ruta WHERE route_id = ? AND zona = ?", (route_id, zona))
                    if (m := re.match(r"^\s*(\d+)\s", r["folder"]))]
            folder = f"{max(nums, default=0) + 1} {zona}"
            orden = (c.execute("SELECT COALESCE(MAX(orden),0) AS m FROM clientes_ruta WHERE route_id = ?", (route_id,)).fetchone()["m"]) + 1
            c.execute("INSERT INTO clientes_ruta(route_id, codigo, folder, zona, nombre, orden) VALUES (?,?,?,?,?,?)", (route_id, codigo, folder, zona, nombre.strip(), orden))
            self.auditar(c, actor, "cliente_ruta.agregar", "ruta", route_id, {"codigo": codigo, "folder": folder})
        return {"route_id": route_id, "codigo": codigo, "folder": folder, "zona": zona}

    def quitar_cliente(self, actor: dict, route_id: str, codigo: str) -> None:
        """Retira el cliente de la ruta SIN renumerar y SIN reutilizar su folder (se conserva el hueco)."""
        with self.tx() as c:
            n = c.execute("DELETE FROM clientes_ruta WHERE route_id = ? AND codigo = ?", (route_id, codigo.strip().upper())).rowcount
            if not n:
                raise ErrorNegocio("El cliente no está en la ruta.", 404)
            self.auditar(c, actor, "cliente_ruta.quitar", "ruta", route_id, {"codigo": codigo})

    def desactivar_ruta(self, actor: dict, route_id: str, activa: bool) -> None:
        with self.tx() as c:
            if not c.execute("UPDATE rutas SET activa = ? WHERE route_id = ?", (int(activa), route_id)).rowcount:
                raise ErrorNegocio("Ruta no encontrada.", 404)
            self.auditar(c, actor, "ruta.activar" if activa else "ruta.desactivar", "ruta", route_id)

    def obtener_ruta(self, route_id: str) -> dict:
        with self.tx() as c:
            r = c.execute("SELECT r.*, v.nombre AS vendedor FROM rutas r JOIN vendedores v ON v.id = r.vendedor_id WHERE route_id = ?", (route_id,)).fetchone()
            if not r:
                return {}
            d = dict(r)
            d["zonas"] = [dict(z) for z in c.execute("SELECT prefijo, nombre FROM zonas WHERE route_id = ? ORDER BY prefijo", (route_id,))]
            d["clientes"] = [dict(x) for x in c.execute("SELECT codigo, folder, zona, nombre, orden FROM clientes_ruta WHERE route_id = ? ORDER BY orden", (route_id,))]
            return d

    def listar_rutas(self, solo_activas: bool = True) -> List[dict]:
        with self.tx() as c:
            ids = [r["route_id"] for r in c.execute("SELECT route_id FROM rutas" + (" WHERE activa = 1" if solo_activas else "") + " ORDER BY nombre")]
        return [self.obtener_ruta(i) for i in ids]

    # ------------------------------------------------------------------ decisiones de gerencia
    def decisiones_vigentes(self) -> Dict[str, dict]:
        with self.tx() as c:
            return {r["codigo_cliente"]: dict(r) for r in c.execute("SELECT * FROM decisiones WHERE estado = 'vigente'")}

    def historial_decisiones(self, codigo: str) -> List[dict]:
        with self.tx() as c:
            return [dict(r) for r in c.execute("SELECT * FROM decisiones WHERE codigo_cliente = ? ORDER BY id DESC", (codigo.strip().upper(),))]

    def registrar_decision(self, actor: dict, codigo: str, clasificacion_final: str, motivo: str, route_id: Optional[str] = None,
                           calculado: Optional[str] = None, promedio: Optional[float] = None, fecha_corte: Optional[str] = None,
                           origen: str = "portal", fecha: Optional[str] = None) -> dict:
        codigo = codigo.strip().upper()
        if clasificacion_final not in CLASIFICACIONES_DECISION:
            raise ErrorNegocio(f"Clasificación final inválida. Use: {', '.join(CLASIFICACIONES_DECISION)}.")
        motivo = (motivo or "").strip()
        if len(motivo) < 10:
            raise ErrorNegocio("El motivo de la decisión es obligatorio (mínimo 10 caracteres).")
        with self.tx() as c:
            if c.execute("UPDATE decisiones SET estado = 'sustituida' WHERE codigo_cliente = ? AND estado = 'vigente'", (codigo,)).rowcount:
                pass
            did = c.execute(
                "INSERT INTO decisiones(codigo_cliente, route_id, clasificacion_final, motivo, calculado_al_decidir, promedio_al_decidir, fuente_fecha_corte, "
                "usuario_id, actor, fecha, origen) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (codigo, route_id, clasificacion_final, motivo, calculado, promedio, fecha_corte, actor.get("id"), actor.get("nombre") or actor.get("usuario") or "Gerencia",
                 fecha or self._t()[:10], origen)).lastrowid
            self.auditar(c, actor, "decision.registrar", "cliente", codigo, {"id": did, "final": clasificacion_final, "calculado": calculado})
            return dict(c.execute("SELECT * FROM decisiones WHERE id = ?", (did,)).fetchone())

    def anular_decision(self, actor: dict, codigo: str, motivo: str) -> dict:
        motivo = (motivo or "").strip()
        if len(motivo) < 10:
            raise ErrorNegocio("El motivo de la anulación es obligatorio (mínimo 10 caracteres).")
        codigo = codigo.strip().upper()
        with self.tx() as c:
            r = c.execute("SELECT * FROM decisiones WHERE codigo_cliente = ? AND estado = 'vigente'", (codigo,)).fetchone()
            if not r:
                raise ErrorNegocio("No hay una decisión vigente para ese cliente.", 404)
            c.execute("UPDATE decisiones SET estado='anulada', anulada_por=?, anulada_motivo=?, anulada_en=? WHERE id=?",
                      (actor.get("usuario"), motivo, self._t(), r["id"]))
            self.auditar(c, actor, "decision.anular", "cliente", codigo, {"id": r["id"], "motivo": motivo})
            return dict(c.execute("SELECT * FROM decisiones WHERE id = ?", (r["id"],)).fetchone())

    # ------------------------------------------------------------------ visitas
    def registrar_visita(self, actor: dict, route_id: str, fecha_salida: str, codigo: str, estado: str, motivo: str = "", observacion: str = "") -> dict:
        if estado not in ESTADOS_VISITA:
            raise ErrorNegocio(f"Estado de visita inválido. Use: {', '.join(ESTADOS_VISITA)}.")
        try:
            datetime.strptime(fecha_salida, "%Y-%m-%d")
        except ValueError as e:
            raise ErrorNegocio("fecha_salida debe ser AAAA-MM-DD.") from e
        codigo = codigo.strip().upper()
        if estado in ("no_visitada", "visitada_sin_firma") and len((motivo or "").strip()) < 3:
            raise ErrorNegocio("Indique el motivo cuando la visita no se hizo o no tiene firma.")
        with self.tx() as c:
            if not c.execute("SELECT 1 FROM clientes_ruta WHERE route_id = ? AND codigo = ?", (route_id, codigo)).fetchone():
                raise ErrorNegocio("El cliente no pertenece a esa ruta.", 404)
            t = self._t()
            v = c.execute("SELECT * FROM visitas WHERE route_id = ? AND fecha_salida = ? AND codigo_cliente = ?", (route_id, fecha_salida, codigo)).fetchone()
            if v:
                anterior = v["estado"]
                c.execute("UPDATE visitas SET estado=?, motivo=?, observacion=?, actualizada=?, registrada_por=? WHERE id=?",
                          (estado, motivo.strip(), observacion.strip(), t, actor["id"], v["id"]))
                vid = v["id"]
            else:
                anterior = None
                vid = c.execute("INSERT INTO visitas(route_id, fecha_salida, codigo_cliente, estado, motivo, observacion, registrada_por, creada, actualizada) "
                                "VALUES (?,?,?,?,?,?,?,?,?)", (route_id, fecha_salida, codigo, estado, motivo.strip(), observacion.strip(), actor["id"], t, t)).lastrowid
            c.execute("INSERT INTO visita_eventos(visita_id, ts, usuario_id, estado_anterior, estado_nuevo, motivo, observacion) VALUES (?,?,?,?,?,?,?)",
                      (vid, t, actor["id"], anterior, estado, motivo.strip(), observacion.strip()))
            self.auditar(c, actor, "visita.registrar", "visita", str(vid), {"ruta": route_id, "cliente": codigo, "estado": estado})
            return dict(c.execute("SELECT * FROM visitas WHERE id = ?", (vid,)).fetchone())

    def listar_visitas(self, route_id: Optional[str] = None, fecha_salida: Optional[str] = None, codigo: Optional[str] = None) -> List[dict]:
        q, p = "SELECT v.*, u.usuario AS registrada_por_usuario FROM visitas v JOIN usuarios u ON u.id = v.registrada_por WHERE 1=1", []
        for col, val in (("v.route_id", route_id), ("v.fecha_salida", fecha_salida), ("v.codigo_cliente", codigo.strip().upper() if codigo else None)):
            if val:
                q += f" AND {col} = ?"
                p.append(val)
        with self.tx() as c:
            return [dict(r) for r in c.execute(q + " ORDER BY v.fecha_salida DESC, v.id DESC", p)]

    def eventos_visita(self, visita_id: int) -> List[dict]:
        with self.tx() as c:
            return [dict(r) for r in c.execute("SELECT e.*, u.usuario FROM visita_eventos e JOIN usuarios u ON u.id = e.usuario_id WHERE visita_id = ? ORDER BY e.id", (visita_id,))]

    def cobertura_visitas(self, route_id: str, fecha_salida: str) -> dict:
        """Previstas/firmadas/sin firma/no visitadas y clientes sin registro. Falta de firma NO es evidencia de no visita."""
        with self.tx() as c:
            total = c.execute("SELECT COUNT(*) AS n FROM clientes_ruta WHERE route_id = ?", (route_id,)).fetchone()["n"]
            cuenta = {r["estado"]: r["n"] for r in c.execute("SELECT estado, COUNT(*) AS n FROM visitas WHERE route_id = ? AND fecha_salida = ? GROUP BY estado", (route_id, fecha_salida))}
        registrados = sum(cuenta.values())
        return {"route_id": route_id, "fecha_salida": fecha_salida, "clientes_en_ruta": total, "sin_registro": total - registrados, **{e: cuenta.get(e, 0) for e in ESTADOS_VISITA}}

    def salidas_consecutivas_sin_visita(self, route_id: str, codigo: str, n: int = 3) -> bool:
        """True si las ultimas `n` salidas registradas de la ruta marcan al cliente como no_visitada (evidencia concluyente, no solo falta de firma)."""
        with self.tx() as c:
            fechas = [r["fecha_salida"] for r in c.execute("SELECT DISTINCT fecha_salida FROM visitas WHERE route_id = ? ORDER BY fecha_salida DESC LIMIT ?", (route_id, n))]
            if len(fechas) < n:
                return False
            ests = [c.execute("SELECT estado FROM visitas WHERE route_id=? AND fecha_salida=? AND codigo_cliente=?", (route_id, f, codigo.strip().upper())).fetchone() for f in fechas]
            return all(e is not None and e["estado"] == "no_visitada" for e in ests)
