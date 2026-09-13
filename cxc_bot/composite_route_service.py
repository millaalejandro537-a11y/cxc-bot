"""Genera una ruta OPERATIVA compuesta (hoy: Comayagua = Comayagua + Santa
Cruz de Yojoa) concatenando el roster de cada componente, en orden, y
delegando toda la escritura del Excel al motor existente (excel_service.py)
via `override_roster` -- no hay logica de composicion dispersa en el motor.

No modifica NINGUNO de los archivos base de los componentes: cada uno se
lee de solo lectura (peek_roster) desde su propia carpeta, y solo se copia
el archivo de la plantilla (el primer componente, normalmente) para
producir el archivo de salida.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from . import excel_service
from .composite_config import CompositeRouteConfig
from .config import load_route_config
from .file_resolver import resolve_base_file
from .models import ClientEntry, GenerationResult, SapClientRecord


class CompositeRouteError(Exception):
    pass


@dataclass
class CompositeGenerationResult:
    generation: GenerationResult
    combined_roster: List[ClientEntry]  # cada entry trae .componente asignado
    component_base_files: Dict[str, Path]  # nombre de componente -> archivo base usado


def generate_composite_route(
    composite: CompositeRouteConfig,
    routes_root: Path,
    output_path: Path,
    new_date,
    sap_records: List[SapClientRecord],
    force: bool = False,
) -> CompositeGenerationResult:
    routes_root = Path(routes_root)
    ordered = composite.sorted_components
    if not ordered:
        raise CompositeRouteError(f"La ruta compuesta '{composite.composite_id}' no tiene componentes configurados.")

    combined_roster: List[ClientEntry] = []
    component_base_files: Dict[str, Path] = {}
    template_base_path = None
    template_config = load_route_config(composite.template_route_id)

    for component in ordered:
        comp_config = load_route_config(component.route_id)
        base_path = resolve_base_file(
            routes_root, composite.vendor, component.folder_name, comp_config, reference_date=excel_service.normalize_date(new_date)
        )
        component_base_files[component.name] = base_path

        entries = excel_service.peek_roster(base_path, comp_config)
        for entry in entries:
            entry.componente = component.name
        combined_roster.extend(entries)

        if component.route_id == composite.template_route_id:
            template_base_path = base_path

    if template_base_path is None:
        raise CompositeRouteError(
            f"template_route_id='{composite.template_route_id}' de '{composite.composite_id}' no coincide con "
            f"el route_id de ningun componente: {[c.route_id for c in ordered]}."
        )

    generation = excel_service.generate_route(
        route_config=template_config,
        base_path=template_base_path,
        output_path=output_path,
        new_date=new_date,
        sap_records=sap_records,
        override_roster=combined_roster,
        force=force,
    )

    return CompositeGenerationResult(
        generation=generation, combined_roster=combined_roster, component_base_files=component_base_files
    )
