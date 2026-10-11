"""Flujo operativo de salidas: feriados configurables, alertas persistentes (deduplicadas), cola de notificaciones (solo SIMULADAS),
versiones de Excel de ruta (Actual / Historico, inmutables) y comparacion de evaluaciones entre salidas. Se mezcla en `Estado`.

Principios: nada se inventa; todo es idempotente (claves unicas); los archivos historicos jamas se sobrescriben; las notificaciones no salen
del sistema (el unico notificador implementado registra el envio como simulado)."""

from __future__ import annotations

import json
import os
import stat
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

DDL_FLUJO = """
CREATE TABLE IF NOT EXISTS feriados (fecha TEXT PRIMARY KEY, nombre TEXT NOT NULL, creado_por TEXT, creado TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alertas (
    id INTEGER PRIMARY KEY, clave TEXT NOT NULL UNIQUE, tipo TEXT NOT NULL, severidad TEXT NOT NULL CHECK (severidad IN ('info','media','alta')),
    route_id TEXT, salida_id INTEGER, codigo TEXT, titulo TEXT NOT NULL, detalle_json TEXT,
    estado TEXT NOT NULL DEFAULT 'nueva' CHECK (estado IN ('nueva','vista','cerrada')), creada TEXT NOT NULL,
    atendida_por TEXT, atendida_en TEXT, nota TEXT);
CREATE INDEX IF NOT EXISTS idx_alertas_ruta ON alertas(route_id, estado);
CREATE TABLE IF NOT EXISTS notificaciones (
    id INTEGER PRIMARY KEY, clave TEXT NOT NULL UNIQUE, tipo TEXT NOT NULL, destinatario TEXT NOT NULL, rol TEXT, asunto TEXT NOT NULL, cuerpo TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente','simulado','error')), creada TEXT NOT NULL, procesada_en TEXT, detalle TEXT);
CREATE TABLE IF NOT EXISTS excel_rutas (
    id INTEGER PRIMARY KEY, route_id TEXT NOT NULL REFERENCES rutas(route_id), salida_id INTEGER REFERENCES salidas(id), fecha_salida TEXT NOT NULL,
    version INTEGER NOT NULL, archivo TEXT NOT NULL, sha256 TEXT NOT NULL, huella TEXT NOT NULL, bytes INTEGER NOT NULL,
    estado TEXT NOT NULL DEFAULT 'ACTUAL' CHECK (estado IN ('BORRADOR','ACTUAL','HISTORICO')), borrador INTEGER NOT NULL DEFAULT 1,
    fuente_tipo TEXT, fuente_corte TEXT, clientes INTEGER, advertencias TEXT, motivos_borrador TEXT, creado_por TEXT, creado_en TEXT NOT NULL,
    UNIQUE (route_id, version));
CREATE TABLE IF NOT EXISTS configuracion (clave TEXT PRIMARY KEY, valor TEXT NOT NULL, actualizado_por TEXT, actualizado TEXT NOT NULL);
"""

SEVERIDADES = ("info", "media", "alta")


class FlujoMixin:
    # ------------------------------------------------------------------ configuracion (umbrales aprobados, editables y auditados)
    CONFIG_DEFECTO = {"umbral_aumento_mora_dias": 5, "oportunidad_ventana_dias": 180, "oportunidad_max_facturas": 2}
    CONFIG_RANGOS = {"umbral_aumento_mora_dias": (1, 90), "oportunidad_ventana_dias": (30, 730), "oportunidad_max_facturas": (0, 24)}

    def configuracion(self) -> Dict[str, int]:
        with self.tx() as c:
            guardado = {r["clave"]: r["valor"] for r in c.execute("SELECT clave, valor FROM configuracion")}
        return {k: int(guardado.get(k, v)) for k, v in self.CONFIG_DEFECTO.items()}

    def fijar_configuracion(self, actor: dict, clave: str, valor) -> Dict[str, int]:
        from .estado import ErrorNegocio
        if clave not in self.CONFIG_DEFECTO:
            raise ErrorNegocio(f"Parámetro desconocido. Use: {', '.join(self.CONFIG_DEFECTO)}.")
        try:
            v = int(valor)
        except (TypeError, ValueError) as e:
            raise ErrorNegocio("El valor debe ser un número entero.") from e
        lo, hi = self.CONFIG_RANGOS[clave]
        if isinstance(valor, bool) or not lo <= v <= hi:
            raise ErrorNegocio(f"Valor fuera de rango para {clave}: {lo} a {hi}.")
        anterior = self.configuracion().get(clave)   # fuera de la transaccion: el cerrojo de `tx` no es reentrante
        with self.tx() as c:
            c.execute("INSERT INTO configuracion(clave, valor, actualizado_por, actualizado) VALUES (?,?,?,?) "
                      "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor, actualizado_por=excluded.actualizado_por, actualizado=excluded.actualizado",
                      (clave, str(v), actor.get("usuario"), self._t()))
            self.auditar(c, actor, "configuracion.fijar", "configuracion", clave, {"antes": anterior, "ahora": v})
        return self.configuracion()

    def rol_de_usuario(self, usuario: Optional[str]) -> Optional[str]:
        if not usuario:
            return None
        with self.tx() as c:
            r = c.execute("SELECT rol FROM usuarios WHERE usuario = ?", (usuario,)).fetchone()
            return r["rol"] if r else None

    # ------------------------------------------------------------------ feriados
    def listar_feriados(self) -> List[dict]:
        with self.tx() as c:
            return [dict(r) for r in c.execute("SELECT fecha, nombre, creado_por FROM feriados ORDER BY fecha")]

    def agregar_feriado(self, actor: dict, fecha: str, nombre: str) -> dict:
        from .estado import ErrorNegocio
        try:
            datetime.strptime(fecha, "%Y-%m-%d")
        except ValueError as e:
            raise ErrorNegocio("La fecha debe ser AAAA-MM-DD.") from e
        if len((nombre or "").strip()) < 3:
            raise ErrorNegocio("Indique el nombre del feriado.")
        with self.tx() as c:
            if c.execute("SELECT 1 FROM feriados WHERE fecha = ?", (fecha,)).fetchone():
                raise ErrorNegocio("Ese feriado ya está registrado.", 409)
            c.execute("INSERT INTO feriados(fecha, nombre, creado_por, creado) VALUES (?,?,?,?)", (fecha, nombre.strip(), actor.get("usuario"), self._t()))
            self.auditar(c, actor, "feriado.agregar", "feriado", fecha, {"nombre": nombre})
        return {"fecha": fecha, "nombre": nombre.strip()}

    def quitar_feriado(self, actor: dict, fecha: str) -> None:
        from .estado import ErrorNegocio
        with self.tx() as c:
            if not c.execute("DELETE FROM feriados WHERE fecha = ?", (fecha,)).rowcount:
                raise ErrorNegocio("Feriado no encontrado.", 404)
            self.auditar(c, actor, "feriado.quitar", "feriado", fecha)

    # ------------------------------------------------------------------ alertas persistentes
    def registrar_alerta(self, clave: str, tipo: str, severidad: str, titulo: str, route_id: Optional[str] = None, salida_id: Optional[int] = None,
                         codigo: Optional[str] = None, detalle: Optional[dict] = None) -> bool:
        """Idempotente por `clave`: devuelve True solo si la alerta es NUEVA (una repeticion del proceso no duplica ni reabre)."""
        assert severidad in SEVERIDADES
        with self.tx() as c:
            return bool(c.execute("INSERT OR IGNORE INTO alertas(clave, tipo, severidad, route_id, salida_id, codigo, titulo, detalle_json, creada) VALUES (?,?,?,?,?,?,?,?,?)",
                                  (clave, tipo, severidad, route_id, salida_id, codigo, titulo, json.dumps(detalle, ensure_ascii=False, default=str) if detalle else None,
                                   self._t())).rowcount)

    def listar_alertas(self, route_ids: Optional[List[str]] = None, estado: Optional[str] = None, tipo: Optional[str] = None, salida_id: Optional[int] = None,
                       limite: int = 500) -> List[dict]:
        q, p = "SELECT * FROM alertas WHERE 1=1", []
        if route_ids is not None:
            if not route_ids:
                return []
            q += f" AND route_id IN ({','.join('?' * len(route_ids))})"
            p += route_ids
        for col, val in (("estado", estado), ("tipo", tipo), ("salida_id", salida_id)):
            if val is not None and val != "":
                q += f" AND {col} = ?"
                p.append(val)
        with self.tx() as c:
            filas = [dict(r) for r in c.execute(q + " ORDER BY CASE severidad WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END, id DESC LIMIT ?", p + [limite])]
        for f in filas:
            f["detalle"] = json.loads(f.pop("detalle_json")) if f.get("detalle_json") else None
        return filas

    def cerrar_alertas(self, salida_id: int, tipos: tuple, nota: str) -> int:
        """Cierra (por el sistema) los avisos abiertos de esos tipos para la salida: p. ej. 'preparacion atrasada' cuando la salida ya se preparo."""
        with self.tx() as c:
            n = c.execute(f"UPDATE alertas SET estado='cerrada', atendida_por='sistema', atendida_en=?, nota=? WHERE salida_id=? AND estado <> 'cerrada' AND tipo IN ({','.join('?' * len(tipos))})",
                          (self._t(), nota, salida_id, *tipos)).rowcount
            return n

    def atender_alerta(self, actor: dict, alerta_id: int, estado: str, nota: str = "") -> dict:
        from .estado import ErrorNegocio
        if estado not in ("vista", "cerrada"):
            raise ErrorNegocio("Estado inválido: use 'vista' o 'cerrada'.")
        if estado == "cerrada" and len((nota or "").strip()) < 3:
            raise ErrorNegocio("Indique una nota para cerrar la alerta.")
        with self.tx() as c:
            if not c.execute("UPDATE alertas SET estado=?, atendida_por=?, atendida_en=?, nota=? WHERE id=?", (estado, actor.get("usuario"), self._t(), nota.strip(), alerta_id)).rowcount:
                raise ErrorNegocio("Alerta no encontrada.", 404)
            self.auditar(c, actor, "alerta.atender", "alerta", str(alerta_id), {"estado": estado})
            return dict(c.execute("SELECT * FROM alertas WHERE id = ?", (alerta_id,)).fetchone())

    # ------------------------------------------------------------------ cola de notificaciones (solo simuladas)
    def encolar_notificacion(self, clave: str, tipo: str, destinatario: str, rol: str, asunto: str, cuerpo: str) -> bool:
        with self.tx() as c:
            return bool(c.execute("INSERT OR IGNORE INTO notificaciones(clave, tipo, destinatario, rol, asunto, cuerpo, creada) VALUES (?,?,?,?,?,?,?)",
                                  (clave, tipo, destinatario, rol, asunto, cuerpo, self._t())).rowcount)

    def listar_notificaciones(self, destinatario: Optional[str] = None, estado: Optional[str] = None, limite: int = 200) -> List[dict]:
        q, p = "SELECT * FROM notificaciones WHERE 1=1", []
        for col, val in (("destinatario", destinatario), ("estado", estado)):
            if val:
                q += f" AND {col} = ?"
                p.append(val)
        with self.tx() as c:
            return [dict(r) for r in c.execute(q + " ORDER BY id DESC LIMIT ?", p + [limite])]

    def pendientes_notificacion(self) -> List[dict]:
        with self.tx() as c:
            return [dict(r) for r in c.execute("SELECT * FROM notificaciones WHERE estado = 'pendiente' ORDER BY id")]

    def marcar_notificacion(self, nid: int, estado: str, detalle: str = "") -> None:
        with self.tx() as c:
            c.execute("UPDATE notificaciones SET estado=?, procesada_en=?, detalle=? WHERE id=?", (estado, self._t(), detalle, nid))

    # ------------------------------------------------------------------ Excel de ruta: versiones Actual / Historico
    @property
    def carpeta_excel(self) -> Path:
        return self.ruta.parent / "rutas_excel"

    def excel_actual(self, route_id: str) -> Optional[dict]:
        with self.tx() as c:
            r = c.execute("SELECT * FROM excel_rutas WHERE route_id = ? AND estado = 'ACTUAL'", (route_id,)).fetchone()
            return dict(r) if r else None

    def excel_borrador(self, route_id: str) -> Optional[dict]:
        with self.tx() as c:
            r = c.execute("SELECT * FROM excel_rutas WHERE route_id = ? AND estado = 'BORRADOR'", (route_id,)).fetchone()
            return dict(r) if r else None

    def listar_excel(self, route_id: str) -> List[dict]:
        with self.tx() as c:
            return [dict(r) for r in c.execute("SELECT * FROM excel_rutas WHERE route_id = ? ORDER BY version DESC", (route_id,))]

    def _mover_excel(self, c, fila: dict, carpeta: str, estado: str) -> str:
        """Mueve un archivo de version a otra carpeta (Borrador / Actual / Historico) sin sobrescribir nunca; el historico queda de solo lectura."""
        rel = f"{fila['route_id']}/{carpeta}/{Path(fila['archivo']).name}"
        origen, destino = self.carpeta_excel / fila["archivo"], self.carpeta_excel / rel
        destino.parent.mkdir(parents=True, exist_ok=True)
        if origen.is_file() and rel != fila["archivo"]:
            if destino.exists():
                raise FileExistsError(f"Ya existe {rel}: no se sobrescribe una version.")
            os.replace(origen, destino)
            if carpeta == "Historico":
                try:
                    os.chmod(destino, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)   # solo lectura
                except OSError:
                    pass
        c.execute("UPDATE excel_rutas SET estado = ?, archivo = ? WHERE id = ?", (estado, rel, fila["id"]))
        return rel

    def registrar_excel(self, actor: dict, route_id: str, salida_id: Optional[int], fecha_salida: str, nombre: str, datos: bytes, sha256: str, huella: str,
                        publicar: bool, fuente_tipo: str, fuente_corte: str, clientes: int, advertencias: List[str], motivos_borrador: Optional[List[str]] = None) -> dict:
        """Guarda una version del Excel. `publicar=True`: queda en `Actual/` (la anterior pasa a `Historico/`). `publicar=False`: queda como BORRADOR en
        `Borrador/` y NO desplaza a la publicada. Los archivos se mueven, jamas se sobrescriben ni se editan. Idempotente por huella de contenido:
        repetir no crea versiones; un borrador identico se PROMUEVE a Actual cuando ya puede publicarse."""
        with self.tx() as c:
            vig = c.execute("SELECT * FROM excel_rutas WHERE route_id = ? AND estado = 'ACTUAL'", (route_id,)).fetchone()
            bor = c.execute("SELECT * FROM excel_rutas WHERE route_id = ? AND estado = 'BORRADOR'", (route_id,)).fetchone()
            vig, bor = (dict(vig) if vig else None), (dict(bor) if bor else None)
            igual = lambda f: f is not None and f["huella"] == huella and f["fecha_salida"] == fecha_salida
            if publicar and igual(vig):
                return {**vig, "creada": False, "promovida": False}
            if not publicar and (igual(bor) or igual(vig)):
                dev = bor if igual(bor) else vig
                if igual(bor):   # el borrador identico se conserva con los motivos vigentes
                    c.execute("UPDATE excel_rutas SET motivos_borrador = ? WHERE id = ?", (json.dumps(motivos_borrador or [], ensure_ascii=False), bor["id"]))
                return {**dev, "creada": False, "promovida": False}
            if publicar and igual(bor):   # promocion: el mismo contenido pasa de Borrador a Actual
                if vig:
                    self._mover_excel(c, vig, "Historico", "HISTORICO")
                self._mover_excel(c, bor, "Actual", "ACTUAL")
                c.execute("UPDATE excel_rutas SET borrador = 0, motivos_borrador = NULL WHERE id = ?", (bor["id"],))
                self.auditar(c, actor, "excel.publicar", "ruta", route_id, {"version": bor["version"], "salida": salida_id})
                return {**dict(c.execute("SELECT * FROM excel_rutas WHERE id = ?", (bor["id"],)).fetchone()), "creada": False, "promovida": True}
            version = (c.execute("SELECT COALESCE(MAX(version),0) AS m FROM excel_rutas WHERE route_id = ?", (route_id,)).fetchone()["m"]) + 1
            carpeta = "Actual" if publicar else "Borrador"
            rel = f"{route_id}/{carpeta}/v{version:04d}_{nombre}"
            destino = self.carpeta_excel / rel
            destino.parent.mkdir(parents=True, exist_ok=True)
            with open(destino, "xb") as f:   # exclusivo: jamas sobrescribe
                f.write(datos)
            if publicar:
                if vig:
                    self._mover_excel(c, vig, "Historico", "HISTORICO")
                if bor:   # un borrador anterior queda superado por la publicacion
                    self._mover_excel(c, bor, "Historico", "HISTORICO")
            elif bor:
                self._mover_excel(c, bor, "Historico", "HISTORICO")
            eid = c.execute("INSERT INTO excel_rutas(route_id, salida_id, fecha_salida, version, archivo, sha256, huella, bytes, estado, borrador, fuente_tipo, fuente_corte, "
                            "clientes, advertencias, motivos_borrador, creado_por, creado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (route_id, salida_id, fecha_salida, version, rel, sha256, huella, len(datos), "ACTUAL" if publicar else "BORRADOR", 0 if publicar else 1,
                             fuente_tipo, fuente_corte, clientes, json.dumps(advertencias, ensure_ascii=False),
                             None if publicar else json.dumps(motivos_borrador or [], ensure_ascii=False), actor.get("usuario"), self._t())).lastrowid
            self.auditar(c, actor, "excel.publicar" if publicar else "excel.borrador", "ruta", route_id,
                         {"version": version, "salida": salida_id, "fecha_salida": fecha_salida, "sha256": sha256, "motivos": motivos_borrador})
            return {**dict(c.execute("SELECT * FROM excel_rutas WHERE id = ?", (eid,)).fetchone()), "creada": True, "promovida": False}

    def verificar_excel(self) -> List[str]:
        """Integridad de TODAS las versiones: el archivo existe, esta en la carpeta de su estado y su sha256 coincide."""
        import hashlib
        with self.tx() as c:
            filas = [dict(r) for r in c.execute("SELECT * FROM excel_rutas")]
        problemas = []
        for e in filas:
            f = self.carpeta_excel / e["archivo"]
            carpeta = {"ACTUAL": "Actual", "BORRADOR": "Borrador"}.get(e["estado"], "Historico")
            if f"/{carpeta}/" not in e["archivo"]:
                problemas.append(f"{e['route_id']} v{e['version']}: debería estar en {carpeta}/")
            if not f.is_file():
                problemas.append(f"{e['route_id']} v{e['version']}: falta el archivo {e['archivo']}")
            elif hashlib.sha256(f.read_bytes()).hexdigest() != e["sha256"]:
                problemas.append(f"{e['route_id']} v{e['version']}: el hash no coincide (archivo alterado)")
        return problemas

    # ------------------------------------------------------------------ evaluaciones por salida
    def comparar_salidas(self, sid_a: int, sid_b: int) -> dict:
        """Compara las evaluaciones persistidas de dos salidas de la MISMA ruta: A = anterior, B = posterior."""
        from .estado import ErrorNegocio
        with self.tx() as c:
            sa, sb = (c.execute("SELECT id, route_id, fecha_salida, preparada_en FROM salidas WHERE id = ?", (i,)).fetchone() for i in (sid_a, sid_b))
            if not sa or not sb:
                raise ErrorNegocio("Salida no encontrada.", 404)
            if sa["route_id"] != sb["route_id"]:
                raise ErrorNegocio("Solo se comparan salidas de la misma ruta.", 409)
            if not sa["preparada_en"] or not sb["preparada_en"]:
                raise ErrorNegocio("Ambas salidas deben estar preparadas (con evaluación persistida).", 409)
            a = {r["codigo"]: dict(r) for r in c.execute("SELECT * FROM salida_resumen WHERE salida_id = ?", (sid_a,))}
            b = {r["codigo"]: dict(r) for r in c.execute("SELECT * FROM salida_resumen WHERE salida_id = ?", (sid_b,))}
        cambios, sin_cambio = [], 0
        for cod in sorted(set(a) | set(b)):
            x, y = a.get(cod), b.get(cod)
            if x is None:
                cambios.append({"codigo": cod, "tipo": "NUEVO", "ahora": y["final"], "folder": y["folder"]})
            elif y is None:
                cambios.append({"codigo": cod, "tipo": "RETIRADO", "antes": x["final"], "folder": x["folder"]})
            else:
                dm = None if x["promedio_mora"] is None or y["promedio_mora"] is None else round(y["promedio_mora"] - x["promedio_mora"], 2)
                if x["final"] != y["final"] or x["calculado"] != y["calculado"] or (dm is not None and dm != 0):
                    cambios.append({"codigo": cod, "tipo": "CAMBIO", "folder": y["folder"], "antes": x["final"], "ahora": y["final"], "calculado_antes": x["calculado"],
                                    "calculado_ahora": y["calculado"], "mora_antes": x["promedio_mora"], "mora_ahora": y["promedio_mora"], "delta_mora": dm,
                                    "saldo_antes": x["saldo"], "saldo_ahora": y["saldo"]})
                else:
                    sin_cambio += 1
        return {"ruta": sa["route_id"], "salida_anterior": {"id": sa["id"], "fecha": sa["fecha_salida"]}, "salida_posterior": {"id": sb["id"], "fecha": sb["fecha_salida"]},
                "cambios": cambios, "sin_cambio": sin_cambio}
