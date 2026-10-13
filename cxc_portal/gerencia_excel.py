"""Lee los Excel de semaforo APROBADOS por gerencia (Las Vegas / Yoro, 09/10/2026).

Dos usos: (1) fuente de la LISTA de la ruta (folders y orden fisico confirmado) y de las decisiones de gerencia
(clasificacion final distinta de la calculada, con motivo); (2) ORACULO de regresion: el motor Python debe reproducir
el resumen por cliente (historial, ventana, pagadas, abiertas, no acreditadas, evaluables, suma/promedio de mora,
alertas, categoria calculada). Solo lectura de archivos locales privados.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import openpyxl

from cxc_bot import payment_semaphore as ps


def _n(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).upper().strip()


def _limpiar(texto) -> str:
    """El Excel de origen trae 'd�­as' (i con tilde corrupta + guion blando): se repara solo ese patron."""
    return str(texto or "").replace("�­", "í").replace("­", "")


def clase_desde_etiqueta(texto: str) -> str:
    """'Verde (A)', 'Amarilla - Cliente regular', 'Candidato a solo contado ...', 'Sin color (en prueba)' -> clave del motor."""
    t = _n(texto)
    if "CANDIDATO" in t:
        return ps.CANDIDATO_SOLO_CONTADO
    if "PRUEBA" in t or "BLANCA" in t or "SIN COLOR" in t:
        return ps.EN_PRUEBA
    if "ROJA" in t or "SOLO CONTADO" in t:
        return ps.SOLO_CONTADO
    if "VERDE" in t or "EXCELENTE" in t:
        return ps.A
    if "AZUL" in t or "BUENO" in t:
        return ps.B
    if "AMARILL" in t or "REGULAR" in t:
        return ps.C
    return ps.SIN_DATOS


def a_regla_vigente(clase: str) -> str:
    """Los Excel aprobados al 09/10 rotulan 'Candidato a solo contado' (pendiente de gerencia). Con la regla vigente desde 2026-10-12 el resultado Contado
    es ROJO sin aprobacion adicional: se lee con la regla vigente para que el oraculo y el motor hablen el mismo idioma."""
    return ps.SOLO_CONTADO if (clase == ps.CANDIDATO_SOLO_CONTADO and ps.CONTADO_AUTOMATICO) else clase


@dataclass
class FilaAprobada:
    folder: str
    zona: str
    codigo: str
    nombre: str
    historial: Optional[int] = None
    ventana: Optional[int] = None
    pagadas: Optional[int] = None
    abiertas: Optional[int] = None
    no_acreditadas: Optional[int] = None
    evaluables: Optional[int] = None
    suma_mora: Optional[float] = None
    promedio: Optional[float] = None
    alertas: Optional[int] = None
    calculado: str = ps.SIN_DATOS
    final: str = ps.SIN_DATOS
    motivo_gerencia: str = ""
    etiqueta_final: str = ""
    es_override: bool = False


def _int(v) -> Optional[int]:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _flt(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _hoja(wb, indice: int):
    return wb[wb.sheetnames[indice]]


def _filas(ws):
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _encabezado(filas: List[list], clave: str) -> Optional[int]:
    for i, r in enumerate(filas):
        if any(_n(c) == clave for c in r):
            return i
    return None


def leer_aprobado(path: Path, zona_por_prefijo: Optional[Dict[str, str]] = None) -> List[FilaAprobada]:  # zona_por_prefijo: compatibilidad
    wb = openpyxl.load_workbook(path, data_only=True)
    lista = _filas(_hoja(wb, 0))
    razones = _filas(_hoja(wb, 1))

    # --- hoja 1: lista (orden de folders confirmado)
    hi = _encabezado(lista, "FOLDER")
    cab = [_n(c) for c in lista[hi]]
    col = {h: i for i, h in enumerate(cab) if h}
    out: Dict[str, FilaAprobada] = {}
    orden: List[str] = []
    for r in lista[hi + 1:]:
        cod = str(r[col["CODIGO SAP"]] or "").strip().upper()
        if not re.match(r"^C\d+$", cod):
            continue
        folder = str(r[col["FOLDER"]]).strip()
        pref = (folder.split() + [""])[1]
        nombre = str(r[col["CLIENTE"]]).strip()
        out[cod] = FilaAprobada(folder, pref, cod, nombre)  # zona = prefijo del folder (LV, SY...); su nombre va en la config de la ruta
        orden.append(cod)
        cat_key = next((k for k in ("CLASIFICACION", "CATEGORIA / VINETA") if k in col), None)
        if cat_key:
            out[cod].etiqueta_final = str(r[col[cat_key]] or "")
            out[cod].final = clase_desde_etiqueta(out[cod].etiqueta_final)
        if "RAZON" in col and r[col["RAZON"]]:
            out[cod].motivo_gerencia = _limpiar(r[col["RAZON"]])

    # --- hoja 2: resumen por cliente (la ventana de calculo)
    hr = _encabezado(razones, "CODIGO SAP")
    cab2 = [_n(c) for c in razones[hr]]
    c2 = {h: i for i, h in enumerate(cab2) if h}

    def pick(r, *names):
        for n in names:
            for k, i in c2.items():
                if k.startswith(n):
                    return r[i]
        return None

    for r in razones[hr + 1:]:
        cod = str(r[c2["CODIGO SAP"]] or "").strip().upper()
        if cod not in out:
            continue
        f = out[cod]
        f.historial = _int(pick(r, "HISTORIAL TOTAL", "FACTURAS HISTORICAS"))
        f.ventana = _int(pick(r, "ULTIMAS 10"))
        f.pagadas = _int(pick(r, "PAGADAS"))
        f.abiertas = _int(pick(r, "ABIERTAS"))
        f.no_acreditadas = _int(pick(r, "NO ACREDITADAS", "CIERRE NO ACREDITADO"))
        f.evaluables = _int(pick(r, "EVALUABLES", "FACTURAS EVALUABLES"))
        f.suma_mora = _flt(pick(r, "SUMA MORA"))
        f.promedio = _flt(pick(r, "PROMEDIO MORA"))
        f.alertas = _int(pick(r, "ALERTAS"))
        calc = pick(r, "CATEGORIA CALCULADA") or pick(r, "CLASIFICACION")
        f.calculado = clase_desde_etiqueta(str(calc or ""))
        if "GERENCIA" in _n(calc):  # LV: la etiqueta ya trae el override; el calculo base se deduce de la media
            f.calculado = ps.clasificar_media(f.promedio) if (f.historial or 0) >= ps.MIN_HISTORIAL and f.promedio is not None else ps.EN_PRUEBA
            f.etiqueta_final = str(calc)
            f.final = clase_desde_etiqueta(str(calc))
        final = pick(r, "CLASIFICACION FINAL")  # solo Yoro; en Las Vegas la etiqueta de la hoja 2 ya es la final
        dec = pick(r, "DECISION DE GERENCIA")
        if final:
            f.etiqueta_final = str(final)
            f.final = clase_desde_etiqueta(str(final))
        if dec:
            f.motivo_gerencia = _limpiar(dec)
    # La hoja de LV marca la excepcion en la etiqueta final ("... - gerencia"): su calculado viene en la hoja 2 sin override.
    for f in out.values():
        f.calculado, f.final = a_regla_vigente(f.calculado), a_regla_vigente(f.final)
        if f.final == ps.SIN_DATOS:
            f.final = f.calculado
        # override = la clasificacion final difiere de la calculada por una decision de gerencia
        f.es_override = bool(f.final != f.calculado and f.final != ps.SIN_DATOS)
    return [out[c] for c in orden]
