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

import base64
import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import security as sec
from .estado_docs import DDL_DOCS, DocumentosMixin
from .estado_ops import OperacionesMixin, migrar

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
    estado_fisico TEXT NOT NULL DEFAULT '', notas TEXT NOT NULL DEFAULT '', activa INTEGER NOT NULL DEFAULT 1, creada TEXT NOT NULL,
    plantilla_excel TEXT);
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


class Estado(OperacionesMixin, DocumentosMixin):
    """Acceso a la BD propia. Una conexion por llamada (hilos seguros); WAL; claves foraneas activas."""

    def __init__(self, ruta: Path, reloj=ahora):
        self.ruta = Path(ruta)
        self.reloj = reloj
        self._lock = threading.Lock()
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        with self.tx() as c:
            c.executescript(DDL)
            if "plantilla_excel" not in [r["name"] for r in c.execute("PRAGMA table_info(rutas)")]:  # migracion de BD creadas antes de esta columna
                c.execute("ALTER TABLE rutas ADD COLUMN plantilla_excel TEXT")
            c.executescript(DDL_DOCS)
            migrar(c)  # salidas, hojas de visita, candidatos, folders permanentes, 2FA y columnas nuevas
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
        r = c.execute("SELECT u.id, u.usuario, u.nombre, u.rol, u.activo, u.debe_cambiar_password, u.vendedor_id, u.totp_activo, v.nombre AS vendedor "
                      "FROM usuarios u LEFT JOIN vendedores v ON v.id = u.vendedor_id WHERE u.id = ?", (uid,)).fetchone()
        return dict(r) if r else {}

    def listar_usuarios(self) -> List[dict]:
        with self.tx() as c:
            return [dict(r) for r in c.execute(
                "SELECT u.id, u.usuario, u.nombre, u.rol, u.activo, u.debe_cambiar_password, u.totp_activo, v.nombre AS vendedor, u.ultimo_acceso "
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
                     reemplazar_clientes: bool = True, plantilla_excel: Optional[str] = None, requiere_hojas_visita: Optional[bool] = None,
                     implantacion: Optional[str] = None) -> dict:
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
                c.execute("UPDATE rutas SET nombre=?, vendedor_id=?, tipo_ruta=?, plazo_dias=?, estado_fisico=?, notas=?, plantilla_excel=COALESCE(?, plantilla_excel) WHERE route_id=?",
                          (nombre.strip(), vid, tipo_ruta, plazo, estado_fisico, notas, plantilla_excel, route_id))
            else:
                c.execute("INSERT INTO rutas(route_id, nombre, vendedor_id, tipo_ruta, plazo_dias, estado_fisico, notas, creada, plantilla_excel) VALUES (?,?,?,?,?,?,?,?,?)",
                          (route_id, nombre.strip(), vid, tipo_ruta, plazo, estado_fisico, notas, self._t(), plantilla_excel))
            if requiere_hojas_visita is not None:
                c.execute("UPDATE rutas SET requiere_hojas_visita = ? WHERE route_id = ?", (int(requiere_hojas_visita), route_id))
            if zonas is not None:
                c.execute("DELETE FROM zonas WHERE route_id = ?", (route_id,))
                for pref, nom in zonas.items():
                    c.execute("INSERT INTO zonas(route_id, prefijo, nombre) VALUES (?,?,?)", (route_id, pref.strip().upper(), nom.strip()))
            if clientes is not None:
                self._reemplazar_clientes(c, route_id, clientes, reemplazar_clientes)
            if implantacion is not None:
                c.execute("UPDATE rutas SET implantacion = ? WHERE route_id = ?", (implantacion, route_id))
            elif not existe:  # una ruta nueva sin clientes confirmados NO nace operativa
                c.execute("UPDATE rutas SET implantacion = ? WHERE route_id = ?", ("OPERATIVA" if clientes else "PENDIENTE_INFORMACION", route_id))
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
        for fila in norm:
            self._hwm_registrar(c, route_id, fila[2])

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
            folder = f"{self._hwm(c, route_id, zona) + 1} {zona}"  # numeracion PERMANENTE: nunca se reutiliza un folder
            orden = (c.execute("SELECT COALESCE(MAX(orden),0) AS m FROM clientes_ruta WHERE route_id = ?", (route_id,)).fetchone()["m"]) + 1
            c.execute("INSERT INTO clientes_ruta(route_id, codigo, folder, zona, nombre, orden) VALUES (?,?,?,?,?,?)", (route_id, codigo, folder, zona, nombre.strip(), orden))
            self._hwm_registrar(c, route_id, folder)
            self.auditar(c, actor, "cliente_ruta.agregar", "ruta", route_id, {"codigo": codigo, "folder": folder})
        return {"route_id": route_id, "codigo": codigo, "folder": folder, "zona": zona}

    def actualizar_nombres_clientes(self, actor: Optional[dict], route_id: str, nombres: Dict[str, str]) -> int:
        """Actualiza SOLO el nombre (cosmetico) de clientes ya existentes en la ruta; no toca orden ni folders."""
        n = 0
        with self.tx() as c:
            for cod, nombre in nombres.items():
                n += c.execute("UPDATE clientes_ruta SET nombre = ? WHERE route_id = ? AND codigo = ? AND nombre <> ?", (nombre, route_id, cod.upper(), nombre)).rowcount
            if n:
                self.auditar(c, actor, "ruta.actualizar_nombres", "ruta", route_id, {"actualizados": n})
        return n

    def quitar_cliente(self, actor: dict, route_id: str, codigo: str) -> None:
        """Retira el cliente de la ruta SIN renumerar y SIN reutilizar su folder (se conserva el hueco)."""
        with self.tx() as c:
            n = c.execute("DELETE FROM clientes_ruta WHERE route_id = ? AND codigo = ?", (route_id, codigo.strip().upper())).rowcount
            if not n:
                raise ErrorNegocio("El cliente no está en la ruta.", 404)
            self.auditar(c, actor, "cliente_ruta.quitar", "ruta", route_id, {"codigo": codigo})

    def asignar_plantilla_excel(self, actor: Optional[dict], route_id: str, plantilla: Optional[str]) -> None:
        with self.tx() as c:
            if not c.execute("UPDATE rutas SET plantilla_excel = ? WHERE route_id = ?", (plantilla, route_id)).rowcount:
                raise ErrorNegocio("Ruta no encontrada.", 404)
            self.auditar(c, actor, "ruta.plantilla_excel", "ruta", route_id, {"plantilla": plantilla})

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
            r = c.execute("SELECT requiere_hojas_visita AS req FROM rutas WHERE route_id = ?", (route_id,)).fetchone()
            if not r or not r["req"]:
                raise ErrorNegocio("Esta ruta no usa hojas de visita.", 409)
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


    # ------------------------------------------------------------------ segundo factor (TOTP)
    def totp_preparar(self, usuario_id: int) -> str:
        """Genera un secreto PENDIENTE (no activo hasta confirmar con un codigo). Reemplaza uno pendiente anterior."""
        sec_ = sec.totp_nuevo_secreto()
        with self.tx() as c:
            u = c.execute("SELECT totp_activo FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
            if not u:
                raise ErrorNegocio("Usuario no encontrado.", 404)
            if u["totp_activo"]:
                raise ErrorNegocio("El segundo factor ya está activo. Pida a un administrador que lo restablezca.", 409)
            c.execute("UPDATE usuarios SET totp_secret = ?, totp_ultimo_paso = 0 WHERE id = ?", (sec_, usuario_id))
        return sec_

    def totp_activar(self, actor: dict, usuario_id: int, codigo: str, instante: float) -> List[str]:
        with self.tx() as c:
            u = c.execute("SELECT totp_secret, totp_activo FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
            if not u or not u["totp_secret"] or u["totp_activo"]:
                raise ErrorNegocio("No hay un alta de segundo factor pendiente.", 409)
            paso = sec.totp_verificar(u["totp_secret"], codigo, instante)
            if paso is None:
                raise ErrorNegocio("Código incorrecto. Verifique la hora del dispositivo e intente de nuevo.", 400)
            codigos = sec.codigos_recuperacion_nuevos()
            c.execute("UPDATE usuarios SET totp_activo = 1, totp_ultimo_paso = ?, recovery_hashes = ? WHERE id = ?",
                      (paso, json.dumps([sec.hash_recuperacion(x) for x in codigos]), usuario_id))
            self.auditar(c, actor, "2fa.activar", "usuario", str(usuario_id))
        return codigos

    def totp_verificar_login(self, usuario_id: int, codigo: str, instante: float) -> bool:
        """Verifica un codigo TOTP (con anti-repeticion) o un codigo de recuperacion de un solo uso."""
        with self.tx() as c:
            u = c.execute("SELECT totp_secret, totp_activo, totp_ultimo_paso, recovery_hashes FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
            if not u or not u["totp_activo"]:
                return False
            paso = sec.totp_verificar(u["totp_secret"], codigo, instante, u["totp_ultimo_paso"])
            if paso is not None:
                c.execute("UPDATE usuarios SET totp_ultimo_paso = ? WHERE id = ?", (paso, usuario_id))
                return True
            h = sec.hash_recuperacion(str(codigo or ""))
            hashes = json.loads(u["recovery_hashes"] or "[]")
            if h in hashes:
                hashes.remove(h)
                c.execute("UPDATE usuarios SET recovery_hashes = ? WHERE id = ?", (json.dumps(hashes), usuario_id))
                self.auditar(c, {"id": usuario_id}, "2fa.recuperacion_usada", "usuario", str(usuario_id), {"restantes": len(hashes)})
                return True
            return False

    def totp_restablecer(self, actor: dict, usuario_id: int) -> None:
        with self.tx() as c:
            if not c.execute("UPDATE usuarios SET totp_secret = NULL, totp_activo = 0, recovery_hashes = NULL, totp_ultimo_paso = 0 WHERE id = ?", (usuario_id,)).rowcount:
                raise ErrorNegocio("Usuario no encontrado.", 404)
            c.execute("DELETE FROM sesiones WHERE usuario_id = ?", (usuario_id,))
            self.auditar(c, actor, "2fa.restablecer", "usuario", str(usuario_id))

    def totp_secreto_pendiente(self, usuario_id: int) -> Optional[str]:
        with self.tx() as c:
            r = c.execute("SELECT totp_secret, totp_activo FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
            return r["totp_secret"] if r and not r["totp_activo"] else None

    # ------------------------------------------------------------------ respaldo / restauracion / exportacion portable
    def respaldar(self, destino_dir: Path, conservar: int = 14) -> Path:
        """Copia CONSISTENTE (API de respaldo de SQLite, segura con el servidor en marcha) + comprobacion de integridad + suma SHA-256."""
        import hashlib
        destino_dir = Path(destino_dir)
        destino_dir.mkdir(parents=True, exist_ok=True)
        nombre = f"portal-{self.reloj().strftime('%Y%m%d-%H%M%S')}.sqlite3"
        destino = destino_dir / nombre
        origen = sqlite3.connect(self.ruta)
        copia = sqlite3.connect(destino)
        try:
            origen.backup(copia)
            ok = copia.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            copia.close()
            origen.close()
        if ok != "ok":
            destino.unlink(missing_ok=True)
            raise ErrorNegocio(f"El respaldo no pasó la comprobación de integridad: {ok}", 500)
        self._respaldar_documentos(destino_dir / "documentos")
        h = hashlib.sha256(destino.read_bytes()).hexdigest()
        (destino_dir / (nombre + ".sha256")).write_text(f"{h}  {nombre}\n", encoding="utf-8")
        antiguos = sorted(destino_dir.glob("portal-*.sqlite3"))
        for viejo in antiguos[:-conservar] if conservar > 0 else []:
            viejo.unlink(missing_ok=True)
            (destino_dir / (viejo.name + ".sha256")).unlink(missing_ok=True)
        return destino

    def _respaldar_documentos(self, destino: Path) -> int:
        """Copia espejo (solo agrega) de los .xlsx de documentos: la BD sola no permite descargar versiones anteriores tras una restauracion."""
        import shutil
        origen, n = self.carpeta_documentos, 0
        if origen.is_dir():
            for f in origen.rglob("*"):
                rel = f.relative_to(origen)
                if f.is_file() and not (destino / rel).exists():
                    (destino / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, destino / rel)
                    n += 1
        return n

    @staticmethod
    def restaurar(archivo: Path, destino: Path, forzar: bool = False) -> Path:
        """Restaura un respaldo verificado (suma SHA-256 + integrity_check). Si ya existe una BD en el destino exige `forzar` y la conserva como .pre-restauracion."""
        import hashlib
        import shutil
        archivo, destino = Path(archivo), Path(destino)
        suma = archivo.with_name(archivo.name + ".sha256")
        if not suma.is_file():
            raise ErrorNegocio("Falta el archivo .sha256 del respaldo.", 400)
        esperado = suma.read_text(encoding="utf-8").split()[0]
        if hashlib.sha256(archivo.read_bytes()).hexdigest() != esperado:
            raise ErrorNegocio("La suma SHA-256 no coincide: respaldo alterado o incompleto.", 400)
        con = sqlite3.connect(archivo)
        try:
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ErrorNegocio("El respaldo no pasa la comprobación de integridad.", 400)
        finally:
            con.close()
        if destino.exists():
            if not forzar:
                raise ErrorNegocio("Ya existe una BD en el destino: use forzar (se conserva una copia .pre-restauracion).", 409)
            shutil.copy2(destino, destino.with_suffix(destino.suffix + ".pre-restauracion"))
        for extra in (destino.with_suffix(destino.suffix + "-wal"), destino.with_suffix(destino.suffix + "-shm")):
            extra.unlink(missing_ok=True)
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archivo, destino)
        docs = archivo.parent / "documentos"   # copia espejo que deja `respaldar`: se repone sin sobrescribir lo existente
        if docs.is_dir():
            for f in docs.rglob("*"):
                rel = f.relative_to(docs)
                if f.is_file() and not (destino.parent / "documentos" / rel).exists():
                    (destino.parent / "documentos" / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, destino.parent / "documentos" / rel)
        return destino

    def verificar_documentos(self) -> List[str]:
        """Problemas de integridad entre la BD y los .xlsx en disco (falta el archivo o su sha256 no coincide). Lista vacia = todo coincide."""
        with self.tx() as c:
            filas = [dict(r) for r in c.execute("SELECT id, route_id, version, almacenado, sha256 FROM documentos")]
        problemas = []
        for d in filas:
            f = self.carpeta_documentos / d["almacenado"]
            if not f.is_file():
                problemas.append(f"documento {d['id']} ({d['route_id']} v{d['version']}): falta el archivo {d['almacenado']}")
            elif hashlib.sha256(f.read_bytes()).hexdigest() != d["sha256"]:
                problemas.append(f"documento {d['id']} ({d['route_id']} v{d['version']}): el hash no coincide")
        return problemas

    TABLAS_EXPORT = ("meta", "vendedores", "usuarios", "rutas", "zonas", "clientes_ruta", "decisiones", "salidas", "salida_resumen", "hojas_visita", "visitas",
                     "visita_eventos", "folder_hwm", "candidatos", "auditoria", "documentos", "salida_documentos", "agentes_impresion", "cola_impresion")

    @property
    def carpeta_documentos(self) -> Path:
        return self.ruta.parent / "documentos"

    def exportar_json(self) -> dict:
        """Volcado LOGICO portable de todas las tablas (para migrar a PostgreSQL u otro motor). Contiene hashes de contraseña y semillas 2FA: tratarlo como secreto."""
        with self.tx() as c:
            tablas = {t: [dict(r) for r in c.execute(f"SELECT * FROM {t}")] for t in self.TABLAS_EXPORT}
        archivos = {}   # los .xlsx de los documentos viven en disco, no en la BD: viajan en el volcado (base64) para no perder las versiones
        for d in tablas["documentos"]:
            f = self.carpeta_documentos / d["almacenado"]
            if f.is_file():
                archivos[d["almacenado"]] = base64.b64encode(f.read_bytes()).decode("ascii")
        return {"formato": "cxc-portal-export/1", "schema_version": SCHEMA_VERSION, "exportado": self._t(), "tablas": tablas, "archivos_documentos": archivos}

    def importar_json(self, datos: dict) -> Dict[str, int]:
        """Carga un volcado en una BD VACIA (no mezcla ni sobrescribe)."""
        if datos.get("formato") != "cxc-portal-export/1":
            raise ErrorNegocio("Formato de exportación desconocido.", 400)
        with self.tx() as c:
            if c.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] or c.execute("SELECT COUNT(*) FROM rutas").fetchone()[0]:
                raise ErrorNegocio("La BD destino no está vacía: la importación no mezcla datos.", 409)
            c.execute("PRAGMA foreign_keys = OFF")
            cuenta = {}
            docs = {d["almacenado"]: d["sha256"] for d in datos["tablas"].get("documentos", [])}
            for rel, b64 in (datos.get("archivos_documentos") or {}).items():
                contenido = base64.b64decode(b64)
                destino = (self.carpeta_documentos / rel).resolve()
                if self.carpeta_documentos.resolve() not in destino.parents or rel not in docs or hashlib.sha256(contenido).hexdigest() != docs[rel]:
                    raise ErrorNegocio(f"Documento '{rel}' del volcado no coincide con su registro (ruta o hash).", 400)
            for t in self.TABLAS_EXPORT:
                filas = datos["tablas"].get(t, [])
                if t == "meta":
                    c.execute("DELETE FROM meta")
                for f in filas:
                    cols = list(f.keys())
                    c.execute(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [f[k] for k in cols])
                cuenta[t] = len(filas)
            self.auditar(c, {"usuario": "importacion", "rol": "admin"}, "bd.importar", "bd", "", cuenta)
            for rel, b64 in (datos.get("archivos_documentos") or {}).items():   # tras validar todo; escritura exclusiva (nunca sobrescribe)
                destino = self.carpeta_documentos / rel
                destino.parent.mkdir(parents=True, exist_ok=True)
                if not destino.exists():
                    destino.write_bytes(base64.b64decode(b64))
            cuenta["archivos_documentos"] = len(datos.get("archivos_documentos") or {})
        return cuenta
