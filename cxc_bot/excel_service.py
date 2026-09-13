"""Motor de Excel: toda la manipulacion real del archivo vive aqui.

Estrategia clave (y por que): openpyxl SI mueve el contenido de las celdas
cuando se usa insert_rows/delete_rows, pero NO reescribe el texto de las
formulas ni conserva el estilo/alto de las filas nuevas o desplazadas (esto
se verifico empiricamente antes de escribir este modulo). Por eso, en lugar
de tratar de "parchar" filas individuales, este motor:

  1. Lee el estado actual como una lista ordenada de ClientEntry (route_engine).
  2. Aplica las operaciones (agregar/eliminar/mover/reordenar) en memoria.
  3. Ajusta la cantidad de filas fisicas con insert_rows/delete_rows si el
     conteo de clientes cambio (esto mueve la fila de totales a su nueva
     posicion).
  4. Reescribe EXPLICITAMENTE cada fila del bloque de clientes (numero,
     codigo, formulas, factura, observacion) y le vuelve a aplicar una
     plantilla de estilo/alto capturada del archivo original ANTES de
     tocar nada.

Esto garantiza que ninguna formula quede apuntando a la fila equivocada y
que el formato quede identico sin importar cuantas filas se agreguen o
quiten.

Ademas: openpyxl NUNCA calcula formulas, y un archivo .xlsx recien escrito
por openpyxl guarda las celdas de formula con su <v> (valor en cache) vacio.
La mayoria de las veces Excel recalcula solo al abrir (por eso al inspeccionar
el archivo con Excel real suele verse bien), pero no todos los visores lo
hacen, y el encargo exige que el archivo se vea listo para imprimir sin que
nadie tenga que forzar un recalculo. Por eso, despues de guardar, este modulo
calcula el resultado real de cada VLOOKUP/SUM (con la misma logica de Excel:
coincidencia exacta contra Hoja2, #N/A si el codigo no aparece, y el total
se vuelve #N/A si algun saldo es #N/A) e inyecta ese valor directamente en el
<v> de la celda dentro del XML ya guardado, sin tocar nada mas del archivo
(por eso no se usa una libreria de recalculo genérica: reescribirian todo el
archivo y se perderia el formato).
"""

from __future__ import annotations

import copy
import logging
import shutil
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

from .models import ClientEntry, GenerationResult, RouteConfig, SapClientRecord
from .route_engine import Operation, apply_operations, validate_roster

logger = logging.getLogger("cxc_bot.excel_service")

_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_DOC_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL_PKG_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
ET.register_namespace("", _MAIN_NS)


def _qn(tag: str) -> str:
    return f"{{{_MAIN_NS}}}{tag}"

MAIN_COLUMNS = ["num_col", "code_col", "name_col", "factura_col", "saldo_col", "obs_col"]


class ExcelServiceError(Exception):
    pass


class OutputExistsError(ExcelServiceError):
    pass


class StructureError(ExcelServiceError):
    """El archivo base no tiene la forma esperada; se aborta antes de tocar nada."""


@dataclass
class _CellStyle:
    font: object
    border: object
    alignment: object
    number_format: str
    fill: object

    @classmethod
    def capture(cls, cell) -> "_CellStyle":
        return cls(
            font=copy.copy(cell.font),
            border=copy.copy(cell.border),
            alignment=copy.copy(cell.alignment),
            number_format=cell.number_format,
            fill=copy.copy(cell.fill),
        )

    def apply(self, cell) -> None:
        cell.font = self.font
        cell.border = self.border
        cell.alignment = self.alignment
        cell.number_format = self.number_format
        cell.fill = self.fill


@dataclass
class _RowTemplate:
    styles: Dict[str, _CellStyle]  # column letter -> style
    height: Optional[float]

    @classmethod
    def capture(cls, ws: Worksheet, row: int, columns: List[str]) -> "_RowTemplate":
        styles = {col: _CellStyle.capture(ws[f"{col}{row}"]) for col in columns}
        height = ws.row_dimensions[row].height if row in ws.row_dimensions else None
        return cls(styles=styles, height=height)

    def apply(self, ws: Worksheet, row: int) -> None:
        for col, style in self.styles.items():
            style.apply(ws[f"{col}{row}"])
        if self.height is not None:
            ws.row_dimensions[row].height = self.height


def _all_columns(config: RouteConfig) -> List[str]:
    cols = {getattr(config, name) for name in MAIN_COLUMNS}
    cols.add(config.total_label_col)
    cols.add(config.total_value_col)
    cols.update(config.passthrough_cols)
    return sorted(cols)


def copy_base_file(base_path: Path, output_path: Path, force: bool = False) -> Path:
    """Duplica el archivo base de forma segura. Nunca sobrescribe el original
    (se lee con shutil.copy2, jamas se abre el base_path en modo escritura) y
    aborta si el destino ya existe, salvo que force=True."""

    base_path = Path(base_path)
    output_path = Path(output_path)

    if not base_path.exists():
        raise FileNotFoundError(f"Archivo base no encontrado: {base_path}")
    if base_path.resolve() == output_path.resolve():
        raise ExcelServiceError("El archivo de salida no puede ser el mismo que el archivo base/original.")
    if output_path.exists() and not force:
        raise OutputExistsError(
            f"Ya existe un archivo en '{output_path}'. Aborta por seguridad. "
            "Usa force=True para confirmar explicitamente el reemplazo."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(base_path, output_path)
    logger.info("Copia segura creada: origen=%s -> destino=%s", base_path, output_path)
    return output_path


def _require_sheet(wb, name: str) -> Worksheet:
    if name not in wb.sheetnames:
        raise StructureError(
            f"No se encontro la hoja '{name}' en el archivo. Hojas disponibles: {wb.sheetnames}"
        )
    return wb[name]


def find_block_end(ws: Worksheet, config: RouteConfig) -> int:
    """Devuelve el numero de fila donde termina el bloque de clientes (la fila
    de totales o la primera fila vacia despues del ultimo cliente)."""

    row = config.data_start_row
    while ws[f"{config.code_col}{row}"].value not in (None, ""):
        row += 1
    return row


def read_roster(ws: Worksheet, config: RouteConfig) -> List[ClientEntry]:
    total_row = find_block_end(ws, config)
    roster: List[ClientEntry] = []
    for row in range(config.data_start_row, total_row):
        codigo = ws[f"{config.code_col}{row}"].value
        no_factura = ws[f"{config.factura_col}{row}"].value
        observacion = ws[f"{config.obs_col}{row}"].value
        roster.append(
            ClientEntry(
                codigo=str(codigo).strip(),
                no_factura=no_factura,
                observacion=observacion,
            )
        )
    return roster


def _rebuild_client_block(
    ws: Worksheet,
    config: RouteConfig,
    new_roster: List[ClientEntry],
    data_row_template: _RowTemplate,
    total_row_template: _RowTemplate,
    old_total_row: int,
    clear_observaciones: bool,
) -> int:
    old_count = old_total_row - config.data_start_row
    new_count = len(new_roster)

    if new_count > old_count:
        ws.insert_rows(old_total_row, amount=new_count - old_count)
    elif new_count < old_count:
        ws.delete_rows(config.data_start_row + new_count, amount=old_count - new_count)

    new_total_row = config.data_start_row + new_count

    for i, entry in enumerate(new_roster):
        row = config.data_start_row + i
        ws[f"{config.num_col}{row}"] = i + 1
        ws[f"{config.code_col}{row}"] = entry.codigo
        ws[f"{config.name_col}{row}"] = config.name_formula_for(row)
        ws[f"{config.factura_col}{row}"] = entry.no_factura
        ws[f"{config.saldo_col}{row}"] = config.saldo_formula_for(row)
        ws[f"{config.obs_col}{row}"] = "" if clear_observaciones else (entry.observacion or "")
        for extra_col in config.passthrough_cols:
            ws[f"{extra_col}{row}"] = None  # sin fuente real; se deja en blanco, igual que Observacion
        data_row_template.apply(ws, row)

    total_row_template.apply(ws, new_total_row)
    ws[f"{config.total_label_col}{new_total_row}"] = config.total_label_text
    if new_count > 0:
        first_ref = f"{config.saldo_col}{config.data_start_row}"
        last_ref = f"{config.saldo_col}{new_total_row - 1}"
        ws[f"{config.total_value_col}{new_total_row}"] = config.total_formula.format(
            first_ref=first_ref, last_ref=last_ref
        )
    else:
        ws[f"{config.total_value_col}{new_total_row}"] = 0

    return new_total_row


def replace_sap_table(ws: Worksheet, records: List[SapClientRecord], config: RouteConfig) -> None:
    """Reemplaza COMPLETAMENTE los datos de Hoja2 (nunca un 'Actualizar todo':
    se borra todo lo anterior y se inserta la tabla nueva completa)."""

    header_row = 1
    columns = [
        config.sap_code_col,
        config.sap_name_col,
        config.sap_saldo_col,
        config.sap_dir_col,
        config.sap_activo_col,
        config.sap_contab_col,
    ]

    template_row = header_row + 1 if ws.max_row >= header_row + 1 else None
    template: Optional[_RowTemplate] = None
    if template_row is not None:
        template = _RowTemplate.capture(ws, template_row, columns)

    if ws.max_row > header_row:
        ws.delete_rows(header_row + 1, amount=ws.max_row - header_row)

    for i, rec in enumerate(records):
        row = header_row + 1 + i
        ws[f"{config.sap_code_col}{row}"] = rec.codigo
        ws[f"{config.sap_name_col}{row}"] = rec.nombre
        ws[f"{config.sap_saldo_col}{row}"] = rec.saldo
        ws[f"{config.sap_dir_col}{row}"] = rec.direccion_factura
        ws[f"{config.sap_activo_col}{row}"] = rec.activo_desde
        ws[f"{config.sap_contab_col}{row}"] = rec.contabilizacion_automatica
        if template is not None:
            template.apply(ws, row)

    logger.info("Hoja2 reemplazada por completo: %d registros de clientes.", len(records))


NA_ERROR = "#N/A"


def _lookup_key(codigo) -> str:
    """VLOOKUP en Excel compara texto SIN distinguir mayusculas/minusculas
    ('c11073' encuentra 'C11073'). Se normaliza aqui para que la
    replica en Python se comporte igual, en vez de fallar con #N/A
    por una diferencia de capitalizacion que Excel si resuelve."""

    return str(codigo).strip().upper()


def build_sap_lookup_map(sap_records: List[SapClientRecord]) -> Dict[str, Tuple[str, float]]:
    """Replica VLOOKUP(..., 0): coincidencia exacta (sin distinguir
    mayusculas/minusculas, igual que Excel), gana la PRIMERA aparicion del
    codigo (igual que Excel al buscar de arriba hacia abajo)."""

    lookup: Dict[str, Tuple[str, float]] = {}
    for rec in sap_records:
        key = _lookup_key(rec.codigo)
        if key not in lookup:
            lookup[key] = (rec.nombre, rec.saldo)
    return lookup


def sap_lookup_map_from_sheet(ws_sap: Worksheet, config: RouteConfig) -> Dict[str, Tuple[str, float]]:
    """Igual que build_sap_lookup_map pero leyendo Hoja2 directamente (se usa en
    edit_route, donde Hoja2 no cambia y no se reciben sap_records nuevos)."""

    from openpyxl.utils import column_index_from_string

    code_idx = column_index_from_string(config.sap_code_col)
    name_idx = column_index_from_string(config.sap_name_col)
    saldo_idx = column_index_from_string(config.sap_saldo_col)

    lookup: Dict[str, Tuple[str, float]] = {}
    for row in range(2, ws_sap.max_row + 1):
        codigo = ws_sap.cell(row=row, column=code_idx).value
        if codigo is None:
            continue
        key = _lookup_key(codigo)
        if key not in lookup:
            nombre = ws_sap.cell(row=row, column=name_idx).value
            saldo = ws_sap.cell(row=row, column=saldo_idx).value
            lookup[key] = (nombre or "", saldo if isinstance(saldo, (int, float)) else 0.0)
    return lookup


def compute_cached_values(
    config: RouteConfig,
    roster: List[ClientEntry],
    total_row: int,
    sap_lookup: Dict[str, Tuple[str, float]],
) -> Dict[str, Tuple[str, object]]:
    """Calcula, EXACTAMENTE como lo haria Excel, el resultado de cada formula
    de la hoja principal: VLOOKUP de nombre/saldo por fila y el SUM del total.
    Devuelve {celda: (tipo, valor)} con tipo en {'n', 'str', 'e'}."""

    cells: Dict[str, Tuple[str, object]] = {}
    total_is_error = False
    total_sum = 0.0

    for i, entry in enumerate(roster):
        row = config.data_start_row + i
        name_ref = f"{config.name_col}{row}"
        saldo_ref = f"{config.saldo_col}{row}"
        match = sap_lookup.get(_lookup_key(entry.codigo))
        if match is None:
            cells[name_ref] = ("e", NA_ERROR)
            cells[saldo_ref] = ("e", NA_ERROR)
            total_is_error = True
        else:
            nombre, saldo = match
            cells[name_ref] = ("str", nombre)
            cells[saldo_ref] = ("n", float(saldo) if isinstance(saldo, (int, float)) else 0.0)
            if not total_is_error:
                total_sum += float(saldo) if isinstance(saldo, (int, float)) else 0.0

    if roster:
        total_ref = f"{config.total_value_col}{total_row}"
        cells[total_ref] = ("e", NA_ERROR) if total_is_error else ("n", total_sum)

    return cells


def _format_cached_number(value) -> str:
    value = float(value)
    if value == int(value):
        return str(int(value))
    return repr(round(value, 6))


def _worksheet_part_for_sheet(zf: zipfile.ZipFile, sheet_name: str) -> str:
    wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
    sheets_el = wb_root.find(_qn("sheets"))
    rid = None
    for sheet_el in sheets_el.findall(_qn("sheet")):
        if sheet_el.get("name") == sheet_name:
            rid = sheet_el.get(f"{{{_REL_DOC_NS}}}id")
            break
    if rid is None:
        raise StructureError(f"No se encontro la hoja '{sheet_name}' en xl/workbook.xml")

    rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    for rel_el in rels_root.findall(f"{{{_REL_PKG_NS}}}Relationship"):
        if rel_el.get("Id") == rid:
            target = rel_el.get("Target").lstrip("/")
            return target if target.startswith("xl/") else f"xl/{target}"

    raise StructureError(f"No se pudo resolver la parte XML de la hoja '{sheet_name}' (rId={rid})")


def inject_cached_formula_values(
    xlsx_path: Path, sheet_name: str, cell_values: Dict[str, Tuple[str, object]]
) -> None:
    """Escribe el resultado calculado de cada formula directamente en el <v>
    de su celda, dentro del XML YA GUARDADO por openpyxl. No se toca ninguna
    otra parte del archivo (estilos, anchos, combinaciones, impresion, Hoja2),
    asi que el formato queda exactamente igual; solo dejan de verse vacias las
    formulas al abrir el archivo en cualquier programa."""

    if not cell_values:
        return

    xlsx_path = Path(xlsx_path)
    with zipfile.ZipFile(xlsx_path, "r") as zin:
        names = zin.namelist()
        contents = {n: zin.read(n) for n in names}
        part = _worksheet_part_for_sheet(zin, sheet_name)

    root = ET.fromstring(contents[part])
    sheet_data = root.find(_qn("sheetData"))
    pending = dict(cell_values)

    for row_el in sheet_data.findall(_qn("row")):
        for c_el in row_el.findall(_qn("c")):
            ref = c_el.get("r")
            if ref not in pending:
                continue
            f_el = c_el.find(_qn("f"))
            if f_el is None:
                raise ExcelServiceError(
                    f"La celda {ref} deberia contener una formula pero no la tiene; "
                    "se aborta la inyeccion de valores calculados por seguridad."
                )
            kind, value = pending.pop(ref)
            v_el = c_el.find(_qn("v"))
            if v_el is None:
                v_el = ET.SubElement(c_el, _qn("v"))
            if kind == "str":
                c_el.set("t", "str")
                v_el.text = str(value)
            elif kind == "e":
                c_el.set("t", "e")
                v_el.text = str(value)
            else:
                c_el.attrib.pop("t", None)
                v_el.text = _format_cached_number(value)

    if pending:
        raise ExcelServiceError(
            f"No se encontraron en '{sheet_name}' las celdas de formula esperadas: {sorted(pending)}"
        )

    contents[part] = ET.tostring(root, encoding="UTF-8", xml_declaration=True)

    tmp_path = xlsx_path.with_suffix(xlsx_path.suffix + ".tmp")
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in names:
            zout.writestr(n, contents[n])
    tmp_path.replace(xlsx_path)

    logger.info("Valores calculados inyectados en '%s': %d celdas.", sheet_name, len(cell_values))


def peek_roster(path: Path, config: RouteConfig) -> List[ClientEntry]:
    """Lee el roster de un archivo SIN escribirlo nunca (ni siquiera .save()).
    Se usa para validar operaciones antes de crear cualquier archivo de salida,
    para poder abortar de forma segura sin dejar artefactos a medio generar."""

    wb = openpyxl.load_workbook(path, read_only=True, data_only=False)
    try:
        ws = wb[config.main_sheet] if config.main_sheet in wb.sheetnames else None
        if ws is None:
            raise StructureError(
                f"No se encontro la hoja '{config.main_sheet}' en {path}. Hojas disponibles: {wb.sheetnames}"
            )
        return read_roster(ws, config)
    finally:
        wb.close()


def normalize_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.strptime(value, "%Y-%m-%d").date()
    raise ExcelServiceError(f"Formato de fecha no soportado: {value!r}")


def generate_route(
    route_config: RouteConfig,
    base_path: Path,
    output_path: Path,
    new_date,
    sap_records: List[SapClientRecord],
    operations: Optional[List[Operation]] = None,
    force: bool = False,
    override_roster: Optional[List[ClientEntry]] = None,
) -> GenerationResult:
    """Genera el Excel de un nuevo mes a partir del archivo base del mes anterior.

    `override_roster`, si se da, REEMPLAZA por completo el roster que se
    leeria del archivo base (y se ignoran `operations`): lo usa
    composite_route_service.py para rutas compuestas, donde el roster viene
    de concatenar varios archivos componente en vez de leerse del propio
    archivo base. `base_path` se sigue usando como plantilla estructural
    (estilos/formato) tal cual.
    """

    base_path = Path(base_path)
    output_path = Path(output_path)
    new_date = normalize_date(new_date)

    # Validar TODO (roster + operaciones) contra el archivo base antes de crear
    # ningun archivo de salida: si una operacion es invalida, se aborta aqui
    # sin dejar artefactos a medio generar.
    if override_roster is not None:
        new_roster = list(override_roster)
    else:
        old_roster_preview = peek_roster(base_path, route_config)
        new_roster = apply_operations(old_roster_preview, operations) if operations else list(old_roster_preview)
    validate_roster(new_roster)

    copy_base_file(base_path, output_path, force=force)

    wb = openpyxl.load_workbook(output_path)
    ws_main = _require_sheet(wb, route_config.main_sheet)
    ws_sap = _require_sheet(wb, route_config.data_sheet)

    columns = _all_columns(route_config)
    old_roster = read_roster(ws_main, route_config)
    old_total_row = route_config.data_start_row + len(old_roster)
    data_row_template = _RowTemplate.capture(ws_main, route_config.data_start_row, columns)
    total_row_template = _RowTemplate.capture(ws_main, old_total_row, columns)

    ws_main[route_config.fecha_cell] = new_date

    new_total_row = _rebuild_client_block(
        ws_main,
        route_config,
        new_roster,
        data_row_template,
        total_row_template,
        old_total_row,
        clear_observaciones=True,
    )

    replace_sap_table(ws_sap, sap_records, route_config)

    wb.calculation.fullCalcOnLoad = True
    wb.save(output_path)

    sap_lookup = build_sap_lookup_map(sap_records)
    cached_values = compute_cached_values(route_config, new_roster, new_total_row, sap_lookup)
    inject_cached_formula_values(output_path, route_config.main_sheet, cached_values)

    logger.info(
        "Ruta generada: base=%s salida=%s fecha=%s clientes=%d sap_registros=%d",
        base_path,
        output_path,
        new_date,
        len(new_roster),
        len(sap_records),
    )

    return GenerationResult(
        source_file=str(base_path),
        output_file=str(output_path),
        route_id=route_config.route_id,
        new_date=new_date,
        client_count=len(new_roster),
        operations_applied=[repr(op) for op in (operations or [])],
    )


def edit_route(
    route_config: RouteConfig,
    input_path: Path,
    output_path: Path,
    operations: List[Operation],
    force: bool = False,
) -> GenerationResult:
    """Aplica operaciones de agregar/eliminar/mover clientes sobre una ruta YA
    generada, sin tocar la fecha ni Hoja2. Las observaciones existentes de
    clientes que se conservan NO se pierden (esto no es un cambio de mes)."""

    input_path = Path(input_path)
    output_path = Path(output_path)

    if input_path.resolve() == output_path.resolve():
        raise ExcelServiceError(
            "edit_route no permite escribir sobre el mismo archivo de entrada; "
            "especifica un output_path distinto (edicion siempre sobre copia)."
        )

    # Igual que en generate_route: validar antes de crear cualquier archivo.
    old_roster_preview = peek_roster(input_path, route_config)
    new_roster = apply_operations(old_roster_preview, operations)

    copy_base_file(input_path, output_path, force=force)

    wb = openpyxl.load_workbook(output_path)
    ws_main = _require_sheet(wb, route_config.main_sheet)
    ws_sap = _require_sheet(wb, route_config.data_sheet)

    columns = _all_columns(route_config)
    old_roster = read_roster(ws_main, route_config)
    old_total_row = route_config.data_start_row + len(old_roster)
    data_row_template = _RowTemplate.capture(ws_main, route_config.data_start_row, columns)
    total_row_template = _RowTemplate.capture(ws_main, old_total_row, columns)

    current_date = ws_main[route_config.fecha_cell].value
    # Hoja2 no cambia en un edit_route; se lee ANTES de tocar el bloque de
    # clientes (aunque no se solapan, es mas claro capturarlo temprano).
    sap_lookup = sap_lookup_map_from_sheet(ws_sap, route_config)

    new_total_row = _rebuild_client_block(
        ws_main,
        route_config,
        new_roster,
        data_row_template,
        total_row_template,
        old_total_row,
        clear_observaciones=False,
    )

    wb.calculation.fullCalcOnLoad = True
    wb.save(output_path)

    cached_values = compute_cached_values(route_config, new_roster, new_total_row, sap_lookup)
    inject_cached_formula_values(output_path, route_config.main_sheet, cached_values)

    logger.info(
        "Ruta editada: origen=%s salida=%s operaciones=%d clientes_resultantes=%d",
        input_path,
        output_path,
        len(operations),
        len(new_roster),
    )

    return GenerationResult(
        source_file=str(input_path),
        output_file=str(output_path),
        route_id=route_config.route_id,
        new_date=normalize_date(current_date) if current_date else None,
        client_count=len(new_roster),
        operations_applied=[repr(op) for op in operations],
    )
