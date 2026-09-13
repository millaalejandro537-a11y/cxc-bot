"""Genera una ruta OPERATIVA compuesta (hoy: Comayagua = Comayagua + Santa
Cruz de Yojoa) concatenando el roster de cada componente, en orden, y
delegando toda la escritura del Excel al motor existente (excel_service.py)
via `override_roster` -- no hay logica de composicion dispersa en el motor.

No modifica NINGUNO de los archivos base de los componentes: cada uno se
COPIA a una carpeta de trabajo local (storage.stage_base_file, igual que
una ruta simple) antes de leerse. Esta funcion NO valida ni publica -- deja
el resultado en `local_work_dir` y el llamador (runner.py) hace exactamente
lo mismo que con una ruta simple: validar localmente y solo publicar si es
valido. Asi la disciplina de "nunca procesar directamente sobre el storage,
nunca publicar algo invalido" vive en un solo lugar.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from . import excel_service, validators
from .composite_config import CompositeRouteConfig
from .config import load_route_config
from .file_resolver import resolve_base_file
from .models import ClientEntry, GenerationResult, RouteConfig, SapClientRecord
from .storage import LocalRouteStorage, RouteStorage


class CompositeRouteError(Exception):
    pass


@dataclass
class CompositeGenerationResult:
    generation: GenerationResult
    combined_roster: List[ClientEntry]  # cada entry trae .componente asignado
    component_base_files: Dict[str, Path]  # nombre de componente -> archivo base ORIGINAL (no la copia local)
    template_config: RouteConfig


def generate_composite_route_local(
    composite: CompositeRouteConfig,
    routes_root: Path,
    local_work_dir: Path,
    local_output_path: Path,
    new_date,
    sap_records: List[SapClientRecord],
    storage: Optional[RouteStorage] = None,
) -> CompositeGenerationResult:
    """Deja el resultado YA GENERADO (sin validar ni publicar) en
    `local_output_path`, dentro de `local_work_dir`. El llamador decide
    cuando validar y publicar (ver runner.py)."""

    routes_root = Path(routes_root)
    storage = storage or LocalRouteStorage()
    ordered = composite.sorted_components
    if not ordered:
        raise CompositeRouteError(f"La ruta compuesta '{composite.composite_id}' no tiene componentes configurados.")

    template_config = load_route_config(composite.template_route_id)

    combined_roster: List[ClientEntry] = []
    component_base_files: Dict[str, Path] = {}
    template_staged_path = None

    for component in ordered:
        comp_config = load_route_config(component.route_id)
        base_path = resolve_base_file(
            routes_root, composite.vendor, component.folder_name, comp_config,
            reference_date=excel_service.normalize_date(new_date),
        )
        component_base_files[component.name] = base_path

        staged = storage.stage_base_file(base_path, local_work_dir / component.name)
        entries = excel_service.peek_roster(staged, comp_config)
        for entry in entries:
            entry.componente = component.name
        combined_roster.extend(entries)

        if component.route_id == composite.template_route_id:
            template_staged_path = staged

    if template_staged_path is None:
        raise CompositeRouteError(
            f"template_route_id='{composite.template_route_id}' de '{composite.composite_id}' no coincide "
            f"con el route_id de ningun componente: {[c.route_id for c in ordered]}."
        )

    generation = excel_service.generate_route(
        route_config=template_config,
        base_path=template_staged_path,
        output_path=local_output_path,
        new_date=new_date,
        sap_records=sap_records,
        override_roster=combined_roster,
        force=True,  # local_output_path es siempre nuevo dentro del temporal; no hay nada que sobreescribir
    )

    return CompositeGenerationResult(
        generation=generation,
        combined_roster=combined_roster,
        component_base_files=component_base_files,
        template_config=template_config,
    )


def generate_composite_route(
    composite: CompositeRouteConfig,
    routes_root: Path,
    output_path: Path,
    new_date,
    sap_records: List[SapClientRecord],
    force: bool = False,
    storage: Optional[RouteStorage] = None,
) -> CompositeGenerationResult:
    """Pipeline completo (generar + validar + publicar) para uso directo/
    pruebas. runner.py usa generate_composite_route_local() en su lugar,
    porque necesita intercalar su propia logica de categorizacion de
    errores entre cada paso -- pero el comportamiento es el mismo."""

    output_path = Path(output_path)
    storage = storage or LocalRouteStorage()

    with tempfile.TemporaryDirectory(prefix="cxc_bot_composite_") as tmp_dir:
        local_work_dir = Path(tmp_dir)
        local_output_path = local_work_dir / f"__salida__{output_path.name}"

        result = generate_composite_route_local(
            composite, routes_root, local_work_dir, local_output_path, new_date, sap_records, storage=storage
        )

        report = validators.validate_workbook(local_output_path, result.template_config, expected_date=excel_service.normalize_date(new_date))
        if not report.ok:
            raise validators.ValidationError("Validacion fallida:\n- " + "\n- ".join(report.errors))

        published_path = storage.publish(local_output_path, output_path, force=force)

    result.generation.output_file = str(published_path)
    return result


def composite_output_folder_name(composite: CompositeRouteConfig) -> str:
    """La carpeta operativa de la ruta compuesta es la del componente que
    hace de plantilla (por convencion: donde ya vivia el archivo antes de
    componerse, p.ej. 'Comayagua')."""

    for component in composite.sorted_components:
        if component.route_id == composite.template_route_id:
            return component.folder_name
    raise CompositeRouteError(
        f"template_route_id='{composite.template_route_id}' no coincide con ningun componente de "
        f"'{composite.composite_id}'."
    )
