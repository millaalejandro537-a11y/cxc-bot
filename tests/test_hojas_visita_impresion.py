"""Formato imprimible de las hojas de visita (vista de impresion del navegador). Sin Excel/LibreOffice no hay render real: se verifican las reglas de
impresion declaradas, que la vista cubra TODOS los colores que el lector reconoce y su legibilidad (WCAG), y una estimacion de capacidad por pagina."""

import re
from pathlib import Path

from cxc_bot.route_markers import contraste
from cxc_portal.hojas_visita_excel import COLORES

CSS = Path("portal-web/src/styles.css").read_text(encoding="utf-8")
JSX = Path("portal-web/src/Documentos.jsx").read_text(encoding="utf-8")


def _colores_jsx():
    bloque = re.search(r"const COLOR = \{(.*?)\n\}", JSX, re.S).group(1)
    out = {}
    for nombre, fondo, texto in re.findall(r"(\w+):\s*\[?'(#[0-9a-fA-F]{3,6})'(?:,\s*'(#[0-9a-fA-F]{3,6})')?\]?", bloque):
        norm = lambda h: (h[1:] if len(h) == 7 else "".join(c * 2 for c in h[1:]))   # noqa: E731
        out[nombre] = (norm(fondo), norm(texto) if texto else "000000")
    return out


def test_la_vista_de_impresion_declara_carta_horizontal_una_pagina_por_hoja_y_oculta_los_controles():
    imp = CSS[CSS.index("@media print"):]
    assert re.search(r"@page\s*\{[^}]*size:\s*letter landscape", imp) and "margin: 10mm" in imp
    assert ".no-print" in imp and "display: none" in imp
    assert re.search(r"\.print-root \.hoja \{[^}]*break-after: page", imp) and "page-break-after: always" in imp
    assert re.search(r":last-child \{[^}]*break-after: auto", imp)                                  # sin pagina en blanco al final


def test_la_vista_imprime_una_seccion_por_hoja_con_los_campos_de_la_hoja_de_visita():
    for texto in ("c.hojas.map", 'className="hoja"', "window.print()", "FIRMA", "COBRO", "VENTA (L)", "CÓDIGO SAP", "FECHA DE SALIDA", "RESUMEN DE SALIDA"):
        assert texto in JSX


def test_todo_color_que_el_lector_reconoce_tiene_color_propio_en_la_vista_de_impresion():
    en_vista = _colores_jsx()
    # el lector normaliza a mayusculas SIN tilde; la vista usa el nombre tal cual figura en el documento
    faltan = [c for c in COLORES if c not in en_vista]
    assert faltan == [], f"colores del documento sin color en la vista de impresion (saldrian en blanco): {faltan}"


def test_el_texto_de_cada_celda_de_color_es_legible_para_11_px():
    for nombre, (fondo, texto) in _colores_jsx().items():
        assert contraste(fondo, texto) >= 4.5, f"{nombre}: contraste {contraste(fondo, texto):.1f}:1 < 4.5:1 (texto de 11 px)"


def test_estimacion_16_clientes_caben_en_una_pagina_carta_horizontal():
    """Estimacion (no render): alto util = 8.5in a 96 dpi menos margenes de 10 mm; filas de ~25 px (11 px + 2*5 px de relleno + bordes)."""
    util = 8.5 * 96 - 2 * (10 / 25.4 * 96)
    cabecera = 4 * 20 + 25 + 2 * 20                    # titulo, subtitulo, campos, pie x2 + fila de encabezados
    fila = 11 * 1.25 + 2 * 5 + 1
    capacidad = int((util - cabecera) // fila)
    assert capacidad >= 16, f"capacidad estimada {capacidad} filas < 16 por hoja"
    assert capacidad < 40                               # un documento con mas de ~{capacidad} clientes por hoja SI se partiria en dos paginas
