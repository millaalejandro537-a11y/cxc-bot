"""Documentos operativos REUTILIZABLES (hojas de visita) en almacenamiento privado, con versiones inmutables, vinculo a salidas y cola de impresion.

Reglas: nunca se sobrescribe un archivo (escritura exclusiva 'xb'); una version nueva exige confirmacion y motivo; el hash SHA-256 identifica el
contenido; cada salida conserva el vinculo a la version que correspondia cuando se creo (historial). La cola de impresion solo REGISTRA solicitudes:
ningun servicio imprime todavia. Un futuro agente de impresion autenticado (token propio) tomara los pendientes.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

DDL_DOCS = """
CREATE TABLE IF NOT EXISTS documentos (
    id INTEGER PRIMARY KEY, route_id TEXT NOT NULL REFERENCES rutas(route_id), tipo TEXT NOT NULL, version INTEGER NOT NULL,
    nombre_original TEXT NOT NULL, almacenado TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, contenido_json TEXT,
    estado TEXT NOT NULL DEFAULT 'VIGENTE' CHECK (estado IN ('VIGENTE','HISTORICA')), motivo TEXT NOT NULL DEFAULT '',
    creado_por TEXT, creado_en TEXT NOT NULL, UNIQUE (route_id, tipo, version));
CREATE TABLE IF NOT EXISTS salida_documentos (
    salida_id INTEGER NOT NULL REFERENCES salidas(id), documento_id INTEGER NOT NULL REFERENCES documentos(id), vinculado_en TEXT NOT NULL, vinculado_por TEXT,
    PRIMARY KEY (salida_id, documento_id));
CREATE TABLE IF NOT EXISTS agentes_impresion (
    id INTEGER PRIMARY KEY, nombre TEXT NOT NULL UNIQUE, token_hash TEXT NOT NULL, activo INTEGER NOT NULL DEFAULT 1, creado_en TEXT NOT NULL, ultimo_contacto TEXT);
CREATE TABLE IF NOT EXISTS cola_impresion (
    id INTEGER PRIMARY KEY, tipo TEXT NOT NULL CHECK (tipo IN ('EXCEL_RUTA','HOJA_VISITA','OTRO')), route_id TEXT, salida_id INTEGER, documento_id INTEGER,
    referencia TEXT, copias INTEGER NOT NULL DEFAULT 1, destino TEXT,
    estado TEXT NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente','tomado','impreso','error','cancelado')),
    solicitado_por TEXT, solicitado_en TEXT NOT NULL, agente_id INTEGER, actualizado_en TEXT, detalle TEXT);
"""

TIPOS_DOC = ("HOJA_VISITA",)
TIPOS_IMPRESION = ("EXCEL_RUTA", "HOJA_VISITA", "OTRO")


class DocumentosMixin:
    # ------------------------------------------------------------------ versiones
    def registrar_documento(self, actor: dict, route_id: str, tipo: str, nombre_original: str, datos: bytes, contenido: dict, carpeta: Path,
                            motivo: str = "", nueva_version: bool = False, confirmado: bool = False) -> dict:
        from .estado import ErrorNegocio
        if tipo not in TIPOS_DOC:
            raise ErrorNegocio(f"Tipo de documento inválido. Use: {', '.join(TIPOS_DOC)}.")
        sha = hashlib.sha256(datos).hexdigest()
        with self.tx() as c:
            r = c.execute("SELECT requiere_hojas_visita AS req FROM rutas WHERE route_id = ?", (route_id,)).fetchone()
            if not r:
                raise ErrorNegocio("Ruta no encontrada.", 404)
            if tipo == "HOJA_VISITA" and not r["req"]:
                raise ErrorNegocio("Esta ruta no usa hojas de visita.", 409)
            vig = c.execute("SELECT * FROM documentos WHERE route_id = ? AND tipo = ? AND estado = 'VIGENTE'", (route_id, tipo)).fetchone()
            if c.execute("SELECT 1 FROM documentos WHERE route_id = ? AND tipo = ? AND sha256 = ?", (route_id, tipo, sha)).fetchone():
                raise ErrorNegocio("Ese contenido ya está registrado (mismo hash): no se crea otra versión.", 409)
            if vig and not nueva_version:
                raise ErrorNegocio("La ruta ya tiene un documento vigente: para reemplazarlo cree una NUEVA VERSIÓN (con confirmación y motivo).", 409)
            if vig:
                if not confirmado:
                    raise ErrorNegocio("Crear una nueva versión requiere confirmación expresa.", 409)
                if len((motivo or "").strip()) < 10:
                    raise ErrorNegocio("Indique el motivo (cambio de clientes, zonas, orden o diseño): mínimo 10 caracteres.")
            version = (c.execute("SELECT COALESCE(MAX(version),0) AS m FROM documentos WHERE route_id = ? AND tipo = ?", (route_id, tipo)).fetchone()["m"]) + 1
            rel = f"{route_id}/{tipo}/v{version}_{sha[:10]}.xlsx"
            destino = Path(carpeta) / rel
            destino.parent.mkdir(parents=True, exist_ok=True)
            with open(destino, "xb") as f:  # exclusivo: jamas sobrescribe un historico
                f.write(datos)
            if vig:
                c.execute("UPDATE documentos SET estado = 'HISTORICA' WHERE id = ?", (vig["id"],))
            did = c.execute("INSERT INTO documentos(route_id, tipo, version, nombre_original, almacenado, sha256, bytes, contenido_json, estado, motivo, creado_por, creado_en) "
                            "VALUES (?,?,?,?,?,?,?,?,'VIGENTE',?,?,?)",
                            (route_id, tipo, version, nombre_original, rel, sha, len(datos), json.dumps(contenido, ensure_ascii=False), (motivo or "").strip(),
                             actor.get("usuario"), self._t())).lastrowid
            self.auditar(c, actor, "documento.nueva_version" if vig else "documento.registrar", "ruta", route_id,
                         {"id": did, "version": version, "sha256": sha, "tipo": tipo, "clientes": contenido.get("total_clientes")})
            return self._doc(c, did)

    def _doc(self, c, did: int, con_contenido: bool = True) -> dict:
        r = c.execute("SELECT * FROM documentos WHERE id = ?", (did,)).fetchone()
        if not r:
            return {}
        d = dict(r)
        d["contenido"] = json.loads(d.pop("contenido_json") or "{}") if con_contenido else None
        d.pop("contenido_json", None)
        d["salidas"] = [x["salida_id"] for x in c.execute("SELECT salida_id FROM salida_documentos WHERE documento_id = ? ORDER BY salida_id", (did,))]
        return d

    def obtener_documento(self, did: int) -> dict:
        with self.tx() as c:
            return self._doc(c, did)

    def listar_documentos(self, route_id: str, tipo: Optional[str] = None) -> List[dict]:
        with self.tx() as c:
            q, p = "SELECT id FROM documentos WHERE route_id = ?", [route_id]
            if tipo:
                q += " AND tipo = ?"
                p.append(tipo)
            return [self._doc(c, r["id"], con_contenido=False) for r in c.execute(q + " ORDER BY version DESC", p)]

    def documento_vigente(self, route_id: str, tipo: str = "HOJA_VISITA") -> Optional[dict]:
        with self.tx() as c:
            r = c.execute("SELECT id FROM documentos WHERE route_id = ? AND tipo = ? AND estado = 'VIGENTE'", (route_id, tipo)).fetchone()
            return self._doc(c, r["id"]) if r else None

    def vincular_vigente_a_salida(self, c, actor: Optional[dict], salida_id: int, route_id: str) -> Optional[int]:
        """Al crear una salida de una ruta con hojas se vincula la version VIGENTE; el vinculo se conserva aunque despues haya otra version."""
        r = c.execute("SELECT id FROM documentos WHERE route_id = ? AND tipo = 'HOJA_VISITA' AND estado = 'VIGENTE'", (route_id,)).fetchone()
        if not r:
            return None
        c.execute("INSERT OR IGNORE INTO salida_documentos(salida_id, documento_id, vinculado_en, vinculado_por) VALUES (?,?,?,?)",
                  (salida_id, r["id"], self._t(), (actor or {}).get("usuario")))
        return r["id"]

    def documentos_de_salida(self, salida_id: int) -> List[dict]:
        with self.tx() as c:
            return [self._doc(c, r["documento_id"], con_contenido=False) for r in c.execute(
                "SELECT documento_id FROM salida_documentos WHERE salida_id = ? ORDER BY documento_id", (salida_id,))]

    # ------------------------------------------------------------------ cola de impresion (SOLO registro; no imprime)
    def solicitar_impresion(self, actor: dict, tipo: str, route_id: Optional[str] = None, salida_id: Optional[int] = None, documento_id: Optional[int] = None,
                            referencia: Optional[str] = None, copias: int = 1, destino: Optional[str] = None) -> dict:
        from .estado import ErrorNegocio
        if tipo not in TIPOS_IMPRESION:
            raise ErrorNegocio(f"Tipo inválido. Use: {', '.join(TIPOS_IMPRESION)}.")
        if not 1 <= int(copias) <= 20:
            raise ErrorNegocio("Copias fuera de rango (1 a 20).")
        with self.tx() as c:
            if tipo == "HOJA_VISITA":
                if not documento_id or not c.execute("SELECT 1 FROM documentos WHERE id = ? AND route_id = ?", (documento_id, route_id)).fetchone():
                    raise ErrorNegocio("Indique un documento de la ruta para imprimir.", 400)
            if tipo == "EXCEL_RUTA" and not (referencia and re.fullmatch(r"[\w\-. ]{3,120}\.xlsx", referencia)):
                raise ErrorNegocio("Indique el archivo Excel generado (nombre .xlsx).", 400)
            if salida_id and not c.execute("SELECT 1 FROM salidas WHERE id = ?", (salida_id,)).fetchone():
                raise ErrorNegocio("Salida no encontrada.", 404)
            iid = c.execute("INSERT INTO cola_impresion(tipo, route_id, salida_id, documento_id, referencia, copias, destino, solicitado_por, solicitado_en) VALUES (?,?,?,?,?,?,?,?,?)",
                            (tipo, route_id, salida_id, documento_id, referencia, int(copias), destino, actor.get("usuario"), self._t())).lastrowid
            self.auditar(c, actor, "impresion.solicitar", "impresion", str(iid), {"tipo": tipo, "ruta": route_id, "documento": documento_id, "copias": copias})
            return dict(c.execute("SELECT * FROM cola_impresion WHERE id = ?", (iid,)).fetchone())

    def listar_impresiones(self, route_ids: Optional[List[str]] = None, estado: Optional[str] = None) -> List[dict]:
        with self.tx() as c:
            q, p = "SELECT * FROM cola_impresion WHERE 1=1", []
            if estado:
                q += " AND estado = ?"
                p.append(estado)
            filas = [dict(r) for r in c.execute(q + " ORDER BY id DESC LIMIT 300", p)]
        return [f for f in filas if route_ids is None or f["route_id"] in route_ids]

    def cancelar_impresion(self, actor: dict, iid: int) -> dict:
        from .estado import ErrorNegocio
        with self.tx() as c:
            if not c.execute("UPDATE cola_impresion SET estado = 'cancelado', actualizado_en = ? WHERE id = ? AND estado = 'pendiente'", (self._t(), iid)).rowcount:
                raise ErrorNegocio("Solo se cancelan solicitudes pendientes.", 409)
            self.auditar(c, actor, "impresion.cancelar", "impresion", str(iid))
            return dict(c.execute("SELECT * FROM cola_impresion WHERE id = ?", (iid,)).fetchone())

    # agente de impresion (futuro): alta por admin, autenticacion por token propio, toma de pendientes y reporte de resultado
    def registrar_agente(self, actor: dict, nombre: str, token_hash: str) -> dict:
        from .estado import ErrorNegocio
        if not re.fullmatch(r"[\w\- ]{3,60}", nombre or ""):
            raise ErrorNegocio("Nombre de agente inválido.")
        with self.tx() as c:
            if c.execute("SELECT 1 FROM agentes_impresion WHERE nombre = ?", (nombre,)).fetchone():
                raise ErrorNegocio("Ya existe un agente con ese nombre.", 409)
            aid = c.execute("INSERT INTO agentes_impresion(nombre, token_hash, creado_en) VALUES (?,?,?)", (nombre, token_hash, self._t())).lastrowid
            self.auditar(c, actor, "agente_impresion.registrar", "agente", str(aid), {"nombre": nombre})
            return {"id": aid, "nombre": nombre}

    def agente_por_token(self, token_hash: str) -> Optional[dict]:
        with self.tx() as c:
            r = c.execute("SELECT id, nombre FROM agentes_impresion WHERE token_hash = ? AND activo = 1", (token_hash,)).fetchone()
            if r:
                c.execute("UPDATE agentes_impresion SET ultimo_contacto = ? WHERE id = ?", (self._t(), r["id"]))
            return dict(r) if r else None

    def agente_tomar_pendiente(self, agente_id: int) -> Optional[dict]:
        with self.tx() as c:
            r = c.execute("SELECT * FROM cola_impresion WHERE estado = 'pendiente' ORDER BY id LIMIT 1").fetchone()
            if not r:
                return None
            c.execute("UPDATE cola_impresion SET estado = 'tomado', agente_id = ?, actualizado_en = ? WHERE id = ?", (agente_id, self._t(), r["id"]))
            return dict(c.execute("SELECT * FROM cola_impresion WHERE id = ?", (r["id"],)).fetchone())

    def agente_reportar(self, agente_id: int, iid: int, ok: bool, detalle: str = "") -> dict:
        from .estado import ErrorNegocio
        with self.tx() as c:
            if not c.execute("UPDATE cola_impresion SET estado = ?, detalle = ?, actualizado_en = ? WHERE id = ? AND agente_id = ? AND estado = 'tomado'",
                             ("impreso" if ok else "error", detalle[:500], self._t(), iid, agente_id)).rowcount:
                raise ErrorNegocio("Solicitud no encontrada o no asignada a este agente.", 404)
            self.auditar(c, {"usuario": f"agente:{agente_id}", "rol": "agente"}, "impresion.resultado", "impresion", str(iid), {"ok": ok})
            return dict(c.execute("SELECT * FROM cola_impresion WHERE id = ?", (iid,)).fetchone())
