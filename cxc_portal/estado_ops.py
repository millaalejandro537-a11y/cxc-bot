"""Operacion por SALIDA: salidas, hojas de visita imprimibles, seguimiento de visitas/omisiones, candidatos de cliente con
confirmacion humana, numeracion PERMANENTE de folders y revision de excepciones. Se mezcla en `Estado` (misma BD propia).

Principios: nada se inventa (una ruta sin clientes confirmados no genera hojas ni salidas), todo queda auditado, y los folders
nunca se reutilizan (marca de agua por zona): la numeracion fisica se escribe sobre las viñetas, no hay etiquetas aparte.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

DDL_OPS = """
CREATE TABLE IF NOT EXISTS salidas (
    id INTEGER PRIMARY KEY, route_id TEXT NOT NULL REFERENCES rutas(route_id), fecha_salida TEXT NOT NULL, fecha_regreso TEXT,
    estado TEXT NOT NULL DEFAULT 'programada' CHECK (estado IN ('programada','preparada','en_curso','cerrada','cancelada')),
    notas TEXT NOT NULL DEFAULT '', creada_por INTEGER REFERENCES usuarios(id), creada TEXT NOT NULL,
    preparada_en TEXT, reporte_excepciones TEXT, revisada_por TEXT, revisada_en TEXT, revision_nota TEXT,
    UNIQUE (route_id, fecha_salida));
CREATE TABLE IF NOT EXISTS salida_resumen (
    salida_id INTEGER NOT NULL REFERENCES salidas(id), codigo TEXT NOT NULL, folder TEXT, calculado TEXT, final TEXT, promedio_mora REAL, saldo REAL,
    PRIMARY KEY (salida_id, codigo));
CREATE TABLE IF NOT EXISTS hojas_visita (
    id INTEGER PRIMARY KEY, salida_id INTEGER NOT NULL REFERENCES salidas(id), serie TEXT NOT NULL UNIQUE, version_formato TEXT NOT NULL,
    clientes_json TEXT NOT NULL, generada_por INTEGER REFERENCES usuarios(id), generada_en TEXT NOT NULL, impresiones INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS folder_hwm (route_id TEXT NOT NULL, zona TEXT NOT NULL, maximo INTEGER NOT NULL, PRIMARY KEY (route_id, zona));
CREATE TABLE IF NOT EXISTS candidatos (
    id INTEGER PRIMARY KEY, route_id TEXT NOT NULL REFERENCES rutas(route_id), codigo TEXT NOT NULL, nombre TEXT NOT NULL DEFAULT '',
    zona_sugerida TEXT, origen TEXT NOT NULL DEFAULT 'manual',
    estado TEXT NOT NULL DEFAULT 'propuesta' CHECK (estado IN ('propuesta','confirmada','rechazada')),
    propuesto_por TEXT, propuesto_en TEXT NOT NULL, resuelto_por TEXT, resuelto_en TEXT, folder_asignado TEXT, motivo_rechazo TEXT,
    UNIQUE (route_id, codigo));
"""

# columnas anadidas a tablas existentes (migracion para BD creadas antes)
COLUMNAS_NUEVAS = {
    "rutas": [("requiere_hojas_visita", "INTEGER NOT NULL DEFAULT 0"), ("numeracion_folders", "TEXT NOT NULL DEFAULT 'ZONA'"),
              ("implantacion", "TEXT NOT NULL DEFAULT 'OPERATIVA'")],
    "usuarios": [("totp_secret", "TEXT"), ("totp_activo", "INTEGER NOT NULL DEFAULT 0"), ("recovery_hashes", "TEXT"), ("totp_ultimo_paso", "INTEGER NOT NULL DEFAULT 0")],
}

IMPLANTACION = ("PENDIENTE_INFORMACION", "EN_CONFIRMACION", "OPERATIVA")
ESTADOS_SALIDA = ("programada", "preparada", "en_curso", "cerrada", "cancelada")
FORMATO_HOJA = "v1-provisional"   # se reemplaza al integrar el formato manual aportado por Alejandro
_FOLDER = re.compile(r"^\s*(\d+)\s+([A-Za-z]{1,4})\s*$")


def migrar(con) -> None:
    con.executescript(DDL_OPS)
    for tabla, cols in COLUMNAS_NUEVAS.items():
        existentes = {r[1] for r in con.execute(f"PRAGMA table_info({tabla})")}
        for nombre, tipo in cols:
            if nombre not in existentes:
                con.execute(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {tipo}")


class OperacionesMixin:
    # ------------------------------------------------------------------ folders permanentes
    def _hwm(self, con, route_id: str, zona: str) -> int:
        r = con.execute("SELECT maximo FROM folder_hwm WHERE route_id = ? AND zona = ?", (route_id, zona)).fetchone()
        existentes = [int(m.group(1)) for x in con.execute("SELECT folder FROM clientes_ruta WHERE route_id = ? AND zona = ?", (route_id, zona))
                      if (m := _FOLDER.match(x["folder"]))]
        return max([r["maximo"] if r else 0] + existentes)

    def _hwm_registrar(self, con, route_id: str, folder: str) -> None:
        m = _FOLDER.match(folder or "")
        if not m:
            return
        zona, n = m.group(2).upper(), int(m.group(1))
        con.execute("INSERT INTO folder_hwm(route_id, zona, maximo) VALUES (?,?,?) ON CONFLICT(route_id, zona) DO UPDATE SET maximo = MAX(maximo, excluded.maximo)",
                    (route_id, zona, n))

    def folders_usados(self, route_id: str) -> Dict[str, int]:
        with self.tx() as c:
            return {r["zona"]: r["maximo"] for r in c.execute("SELECT zona, maximo FROM folder_hwm WHERE route_id = ?", (route_id,))}

    def agregar_zona(self, actor: dict, route_id: str, prefijo: str, nombre: str) -> dict:
        from .estado import ErrorNegocio
        prefijo = (prefijo or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{1,4}", prefijo) or not (nombre or "").strip():
            raise ErrorNegocio("Zona inválida: prefijo de 1 a 4 letras y nombre.")
        with self.tx() as c:
            if not c.execute("SELECT 1 FROM rutas WHERE route_id = ?", (route_id,)).fetchone():
                raise ErrorNegocio("Ruta no encontrada.", 404)
            if c.execute("SELECT 1 FROM zonas WHERE route_id = ? AND prefijo = ?", (route_id, prefijo)).fetchone():
                raise ErrorNegocio("Ese prefijo de zona ya existe en la ruta.", 409)
            c.execute("INSERT INTO zonas(route_id, prefijo, nombre) VALUES (?,?,?)", (route_id, prefijo, nombre.strip()))
            self.auditar(c, actor, "zona.agregar", "ruta", route_id, {"prefijo": prefijo, "nombre": nombre})
        return {"route_id": route_id, "prefijo": prefijo, "nombre": nombre.strip()}

    # ------------------------------------------------------------------ implantacion de la ruta
    def fijar_implantacion(self, actor: dict, route_id: str, estado: str) -> dict:
        from .estado import ErrorNegocio
        if estado not in IMPLANTACION:
            raise ErrorNegocio(f"Estado de implantación inválido. Use: {', '.join(IMPLANTACION)}.")
        with self.tx() as c:
            r = c.execute("SELECT route_id FROM rutas WHERE route_id = ?", (route_id,)).fetchone()
            if not r:
                raise ErrorNegocio("Ruta no encontrada.", 404)
            n = c.execute("SELECT COUNT(*) AS n FROM clientes_ruta WHERE route_id = ?", (route_id,)).fetchone()["n"]
            if estado == "OPERATIVA" and n == 0:
                raise ErrorNegocio("No se puede marcar operativa una ruta sin clientes confirmados.", 409)
            pend = c.execute("SELECT COUNT(*) AS n FROM candidatos WHERE route_id = ? AND estado = 'propuesta'", (route_id,)).fetchone()["n"]
            if estado == "OPERATIVA" and pend:
                raise ErrorNegocio(f"Hay {pend} cliente(s) propuesto(s) sin confirmar o rechazar.", 409)
            c.execute("UPDATE rutas SET implantacion = ? WHERE route_id = ?", (estado, route_id))
            self.auditar(c, actor, "ruta.implantacion", "ruta", route_id, {"estado": estado})
        return self.obtener_ruta(route_id)

    # ------------------------------------------------------------------ candidatos (confirmacion humana)
    def proponer_candidatos(self, actor: dict, route_id: str, items: List[dict], origen: str = "manual") -> List[dict]:
        from .estado import ErrorNegocio
        out = []
        with self.tx() as c:
            if not c.execute("SELECT 1 FROM rutas WHERE route_id = ?", (route_id,)).fetchone():
                raise ErrorNegocio("Ruta no encontrada.", 404)
            for it in items:
                cod = str(it.get("codigo", "")).strip().upper()
                if not re.fullmatch(r"C\d{3,8}", cod):
                    raise ErrorNegocio(f"Código de cliente inválido: '{cod}'.")
                if c.execute("SELECT 1 FROM clientes_ruta WHERE route_id = ? AND codigo = ?", (route_id, cod)).fetchone():
                    continue  # ya confirmado en la ruta
                otra = c.execute("SELECT route_id FROM clientes_ruta WHERE codigo = ? AND route_id <> ?", (cod, route_id)).fetchone()
                prev = c.execute("SELECT estado FROM candidatos WHERE route_id = ? AND codigo = ?", (route_id, cod)).fetchone()
                if prev and prev["estado"] == "propuesta":
                    continue
                if prev:
                    c.execute("UPDATE candidatos SET estado='propuesta', nombre=?, zona_sugerida=?, origen=?, propuesto_por=?, propuesto_en=?, resuelto_por=NULL, resuelto_en=NULL, "
                              "motivo_rechazo=NULL, folder_asignado=NULL WHERE route_id=? AND codigo=?",
                              (str(it.get("nombre", "")).strip(), (it.get("zona") or "").strip().upper() or None, origen, actor.get("usuario"), self._t(), route_id, cod))
                else:
                    c.execute("INSERT INTO candidatos(route_id, codigo, nombre, zona_sugerida, origen, propuesto_por, propuesto_en) VALUES (?,?,?,?,?,?,?)",
                              (route_id, cod, str(it.get("nombre", "")).strip(), (it.get("zona") or "").strip().upper() or None, origen, actor.get("usuario"), self._t()))
                fila = dict(c.execute("SELECT * FROM candidatos WHERE route_id = ? AND codigo = ?", (route_id, cod)).fetchone())
                fila["tambien_en_ruta"] = otra["route_id"] if otra else None
                out.append(fila)
            if c.execute("SELECT implantacion FROM rutas WHERE route_id = ?", (route_id,)).fetchone()["implantacion"] == "PENDIENTE_INFORMACION" and out:
                c.execute("UPDATE rutas SET implantacion = 'EN_CONFIRMACION' WHERE route_id = ?", (route_id,))
            self.auditar(c, actor, "candidatos.proponer", "ruta", route_id, {"cantidad": len(out), "origen": origen})
        return out

    def listar_candidatos(self, route_id: str, estado: Optional[str] = None) -> List[dict]:
        with self.tx() as c:
            q, p = "SELECT * FROM candidatos WHERE route_id = ?", [route_id]
            if estado:
                q += " AND estado = ?"
                p.append(estado)
            return [dict(r) for r in c.execute(q + " ORDER BY id", p)]

    def confirmar_candidato(self, actor: dict, route_id: str, cand_id: int, zona: str, folder: Optional[str] = None) -> dict:
        """Confirmacion HUMANA: el cliente entra a la ruta con folder permanente (siguiente de su zona o uno explicito jamas usado)."""
        from .estado import ErrorNegocio
        zona = (zona or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{1,4}", zona):
            raise ErrorNegocio("Indique la zona (prefijo de 1 a 4 letras).")
        with self.tx() as c:
            cand = c.execute("SELECT * FROM candidatos WHERE id = ? AND route_id = ?", (cand_id, route_id)).fetchone()
            if not cand:
                raise ErrorNegocio("Candidato no encontrado.", 404)
            if cand["estado"] != "propuesta":
                raise ErrorNegocio("El candidato ya fue resuelto.", 409)
            if not c.execute("SELECT 1 FROM zonas WHERE route_id = ? AND prefijo = ?", (route_id, zona)).fetchone():
                raise ErrorNegocio(f"La zona '{zona}' no está definida en la ruta: defínala primero.", 409)
            if folder:
                m = _FOLDER.match(folder)
                if not m or m.group(2).upper() != zona:
                    raise ErrorNegocio("El folder debe tener la forma '<n> <zona>' de la misma zona.")
                n = int(m.group(1))
                if c.execute("SELECT 1 FROM clientes_ruta WHERE route_id = ? AND folder = ?", (route_id, f"{n} {zona}")).fetchone() or n <= 0:
                    raise ErrorNegocio("Ese folder ya está en uso.", 409)
                if n <= self._hwm(c, route_id, zona):
                    raise ErrorNegocio("Ese folder ya fue usado antes y no se reutiliza.", 409)
            else:
                n = self._hwm(c, route_id, zona) + 1
            folder_final = f"{n} {zona}"
            orden = (c.execute("SELECT COALESCE(MAX(orden),0) AS m FROM clientes_ruta WHERE route_id = ?", (route_id,)).fetchone()["m"]) + 1
            c.execute("INSERT INTO clientes_ruta(route_id, codigo, folder, zona, nombre, orden) VALUES (?,?,?,?,?,?)", (route_id, cand["codigo"], folder_final, zona, cand["nombre"], orden))
            self._hwm_registrar(c, route_id, folder_final)
            c.execute("UPDATE candidatos SET estado='confirmada', resuelto_por=?, resuelto_en=?, folder_asignado=?, zona_sugerida=? WHERE id=?",
                      (actor.get("usuario"), self._t(), folder_final, zona, cand_id))
            self.auditar(c, actor, "candidato.confirmar", "ruta", route_id, {"codigo": cand["codigo"], "folder": folder_final})
            return {"codigo": cand["codigo"], "folder": folder_final, "zona": zona}

    def rechazar_candidato(self, actor: dict, route_id: str, cand_id: int, motivo: str) -> dict:
        from .estado import ErrorNegocio
        if len((motivo or "").strip()) < 3:
            raise ErrorNegocio("Indique el motivo del rechazo.")
        with self.tx() as c:
            n = c.execute("UPDATE candidatos SET estado='rechazada', resuelto_por=?, resuelto_en=?, motivo_rechazo=? WHERE id=? AND route_id=? AND estado='propuesta'",
                          (actor.get("usuario"), self._t(), motivo.strip(), cand_id, route_id)).rowcount
            if not n:
                raise ErrorNegocio("Candidato no encontrado o ya resuelto.", 404)
            self.auditar(c, actor, "candidato.rechazar", "ruta", route_id, {"id": cand_id, "motivo": motivo})
            return dict(c.execute("SELECT * FROM candidatos WHERE id = ?", (cand_id,)).fetchone())

    # ------------------------------------------------------------------ salidas
    def crear_salida(self, actor: dict, route_id: str, fecha_salida: str, fecha_regreso: Optional[str] = None, notas: str = "") -> dict:
        from .estado import ErrorNegocio
        for f in (fecha_salida, fecha_regreso):
            if f:
                try:
                    datetime.strptime(f, "%Y-%m-%d")
                except ValueError as e:
                    raise ErrorNegocio("Las fechas deben ser AAAA-MM-DD.") from e
        if fecha_regreso and fecha_regreso < fecha_salida:
            raise ErrorNegocio("La fecha de regreso no puede ser anterior a la de salida.")
        with self.tx() as c:
            r = c.execute("SELECT implantacion, activa FROM rutas WHERE route_id = ?", (route_id,)).fetchone()
            if not r or not r["activa"]:
                raise ErrorNegocio("Ruta no encontrada.", 404)
            if r["implantacion"] != "OPERATIVA":
                raise ErrorNegocio("La ruta aún no está operativa: confirme sus clientes y márquela operativa antes de programar salidas.", 409)
            if c.execute("SELECT 1 FROM salidas WHERE route_id = ? AND fecha_salida = ?", (route_id, fecha_salida)).fetchone():
                raise ErrorNegocio("Ya existe una salida de esa ruta en esa fecha.", 409)
            sid = c.execute("INSERT INTO salidas(route_id, fecha_salida, fecha_regreso, notas, creada_por, creada) VALUES (?,?,?,?,?,?)",
                            (route_id, fecha_salida, fecha_regreso, notas.strip(), actor.get("id"), self._t())).lastrowid
            doc = self.vincular_vigente_a_salida(c, actor, sid, route_id)
            self.auditar(c, actor, "salida.crear", "salida", str(sid), {"ruta": route_id, "fecha": fecha_salida, "documento_hoja": doc})
            return self._salida(c, sid)

    def _salida(self, c, sid: int) -> dict:
        r = c.execute("SELECT * FROM salidas WHERE id = ?", (sid,)).fetchone()
        if not r:
            return {}
        d = dict(r)
        d["reporte_excepciones"] = json.loads(d["reporte_excepciones"]) if d.get("reporte_excepciones") else None
        d["hojas"] = [dict(h) for h in c.execute("SELECT id, serie, version_formato, generada_en, impresiones FROM hojas_visita WHERE salida_id = ? ORDER BY id", (sid,))]
        return d

    def obtener_salida(self, sid: int) -> dict:
        with self.tx() as c:
            return self._salida(c, sid)

    def listar_salidas(self, route_id: Optional[str] = None) -> List[dict]:
        with self.tx() as c:
            q, p = "SELECT id FROM salidas", []
            if route_id:
                q += " WHERE route_id = ?"
                p.append(route_id)
            return [self._salida(c, r["id"]) for r in c.execute(q + " ORDER BY fecha_salida DESC", p)]

    def cambiar_estado_salida(self, actor: dict, sid: int, estado: str) -> dict:
        from .estado import ErrorNegocio
        if estado not in ESTADOS_SALIDA:
            raise ErrorNegocio(f"Estado inválido. Use: {', '.join(ESTADOS_SALIDA)}.")
        with self.tx() as c:
            if not c.execute("UPDATE salidas SET estado = ? WHERE id = ?", (estado, sid)).rowcount:
                raise ErrorNegocio("Salida no encontrada.", 404)
            self.auditar(c, actor, "salida.estado", "salida", str(sid), {"estado": estado})
            return self._salida(c, sid)

    def guardar_preparacion(self, actor: dict, sid: int, resumen: List[dict], reporte: dict) -> dict:
        with self.tx() as c:
            c.execute("DELETE FROM salida_resumen WHERE salida_id = ?", (sid,))
            c.executemany("INSERT INTO salida_resumen(salida_id, codigo, folder, calculado, final, promedio_mora, saldo) VALUES (?,?,?,?,?,?,?)",
                          [(sid, r["codigo"], r["folder"], r["calculado"], r["final"], r["promedio_mora"], r["saldo"]) for r in resumen])
            c.execute("UPDATE salidas SET estado = CASE WHEN estado = 'programada' THEN 'preparada' ELSE estado END, preparada_en = ?, reporte_excepciones = ?, "
                      "revisada_por = NULL, revisada_en = NULL, revision_nota = NULL WHERE id = ?", (self._t(), json.dumps(reporte, ensure_ascii=False, default=str), sid))
            self.auditar(c, actor, "salida.preparar", "salida", str(sid), {"excepciones": reporte.get("total_excepciones")})
            return self._salida(c, sid)

    def resumen_salida_anterior(self, route_id: str, fecha_salida: str) -> Dict[str, dict]:
        with self.tx() as c:
            prev = c.execute("SELECT s.id FROM salidas s WHERE s.route_id = ? AND s.fecha_salida < ? AND s.preparada_en IS NOT NULL ORDER BY s.fecha_salida DESC LIMIT 1",
                             (route_id, fecha_salida)).fetchone()
            if not prev:
                return {}
            return {r["codigo"]: dict(r) for r in c.execute("SELECT * FROM salida_resumen WHERE salida_id = ?", (prev["id"],))}

    def revisar_salida(self, actor: dict, sid: int, nota: str) -> dict:
        from .estado import ErrorNegocio
        if len((nota or "").strip()) < 3:
            raise ErrorNegocio("Indique una nota de revisión.")
        with self.tx() as c:
            s = c.execute("SELECT reporte_excepciones FROM salidas WHERE id = ?", (sid,)).fetchone()
            if not s:
                raise ErrorNegocio("Salida no encontrada.", 404)
            if not s["reporte_excepciones"]:
                raise ErrorNegocio("La salida aún no fue preparada.", 409)
            c.execute("UPDATE salidas SET revisada_por = ?, revisada_en = ?, revision_nota = ? WHERE id = ?", (actor.get("usuario"), self._t(), nota.strip(), sid))
            self.auditar(c, actor, "salida.revisar", "salida", str(sid), {"nota": nota})
            return self._salida(c, sid)

    # ------------------------------------------------------------------ hojas de visita
    def hoja_de_salida(self, sid: int) -> dict:
        """La hoja de visita de una salida es el DOCUMENTO REUTILIZABLE vinculado (version vigente al crearla). NO se regenera por salida."""
        from .estado import ErrorNegocio
        with self.tx() as c:
            s = c.execute("SELECT s.*, r.nombre AS ruta_nombre, r.requiere_hojas_visita AS req, v.nombre AS vendedor FROM salidas s "
                          "JOIN rutas r ON r.route_id = s.route_id JOIN vendedores v ON v.id = r.vendedor_id WHERE s.id = ?", (sid,)).fetchone()
            if not s:
                raise ErrorNegocio("Salida no encontrada.", 404)
            if not s["req"]:
                raise ErrorNegocio("Esta ruta no usa hojas de visita.", 409)
            docs = [r["documento_id"] for r in c.execute("SELECT documento_id FROM salida_documentos WHERE salida_id = ? ORDER BY documento_id DESC", (sid,))]
            if not docs:
                raise ErrorNegocio("La salida no tiene un documento de hojas de visita vinculado: registre el documento aprobado de la ruta.", 409)
            doc = self._doc(c, docs[0])
            vigente = c.execute("SELECT id, version FROM documentos WHERE route_id = ? AND tipo = 'HOJA_VISITA' AND estado = 'VIGENTE'", (s["route_id"],)).fetchone()
        return {"salida_id": sid, "route_id": s["route_id"], "ruta": s["ruta_nombre"], "vendedor": s["vendedor"], "fecha_salida": s["fecha_salida"],
                "documento": doc, "es_version_vigente": bool(vigente and vigente["id"] == doc["id"]),
                "aviso": None if (vigente and vigente["id"] == doc["id"]) else f"Hay una versión más reciente (v{vigente['version']}); esta salida conserva la v{doc['version']}."}

    # ------------------------------------------------------------------ seguimiento
    def seguimiento_salida(self, sid: int) -> dict:
        from .estado import ESTADOS_VISITA
        with self.tx() as c:
            s = c.execute("SELECT * FROM salidas WHERE id = ?", (sid,)).fetchone()
            if not s:
                return {}
            clientes = [dict(x) for x in c.execute("SELECT folder, codigo, nombre FROM clientes_ruta WHERE route_id = ? ORDER BY orden", (s["route_id"],))]
            vis = {v["codigo_cliente"]: dict(v) for v in c.execute("SELECT * FROM visitas WHERE route_id = ? AND fecha_salida = ?", (s["route_id"], s["fecha_salida"]))}
        filas = []
        cuenta = {e: 0 for e in ESTADOS_VISITA}
        cuenta["pendiente"] = 0
        for cl in clientes:
            v = vis.get(cl["codigo"])
            est = v["estado"] if v else "pendiente"
            cuenta[est] += 1
            filas.append({**cl, "estado": est, "motivo": v["motivo"] if v else "", "observacion": v["observacion"] if v else ""})
        return {"salida_id": sid, "route_id": s["route_id"], "fecha_salida": s["fecha_salida"], "estado": s["estado"], "resumen": cuenta, "clientes": filas}

    def omisiones_consecutivas(self, route_id: str, n: int = 3) -> dict:
        """Clientes no visitados en las ULTIMAS n salidas (evidencia concluyente) y clientes sin registro en salidas cerradas (revisar).
        La falta de firma NO cuenta como no visita."""
        with self.tx() as c:
            salidas = [dict(r) for r in c.execute("SELECT * FROM salidas WHERE route_id = ? AND estado IN ('en_curso','cerrada') ORDER BY fecha_salida DESC LIMIT ?", (route_id, n))]
            clientes = [dict(x) for x in c.execute("SELECT folder, codigo, nombre FROM clientes_ruta WHERE route_id = ? ORDER BY orden", (route_id,))]
            visitas = {(v["fecha_salida"], v["codigo_cliente"]): v["estado"] for v in c.execute("SELECT fecha_salida, codigo_cliente, estado FROM visitas WHERE route_id = ?", (route_id,))}
        confirmadas, sin_registro = [], []
        if len(salidas) >= n:
            for cl in clientes:
                ests = [visitas.get((s["fecha_salida"], cl["codigo"])) for s in salidas]
                if all(e == "no_visitada" for e in ests):
                    confirmadas.append({**cl, "salidas": [s["fecha_salida"] for s in salidas]})
                elif all(e in (None, "no_visitada") for e in ests) and any(e is None for e in ests):
                    sin_registro.append({**cl, "salidas": [s["fecha_salida"] for s in salidas]})
        return {"ruta": route_id, "n": n, "salidas_consideradas": [s["fecha_salida"] for s in salidas], "omitidas_confirmadas": confirmadas,
                "sin_registro_en_salidas": sin_registro, "nota": "La falta de firma no es no visita. 'Sin registro' requiere revisión humana."}
