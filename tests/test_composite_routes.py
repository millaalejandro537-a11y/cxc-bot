"""Prueba de la ruta compuesta Comayagua + Santa Cruz de Yojoa con los
archivos REALES de referencia (copias de solo lectura). Cubre lo pedido en
la seccion 33 del encargo: componentes separados historicamente, Comayagua
primero, Santa Cruz al final, numeracion, formulas, total, trazabilidad de
componente, y que ninguno de los dos originales se toque."""

import hashlib
import shutil
from datetime import date

import openpyxl
import pytest

from cxc_bot import validators
from cxc_bot.composite_config import CompositeRouteConfig, RouteComponent, load_composite_route
from cxc_bot.composite_route_service import CompositeRouteError, generate_composite_route
from cxc_bot.config import load_route_config
from cxc_bot.data_source import LocalMockDataSource
from cxc_bot.excel_service import peek_roster

FIXTURES = "tests/fixtures/rutas_reales"


def file_hash(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


@pytest.fixture
def comayagua_routes_root(tmp_path):
    root = tmp_path / "RUTA VENDEDORES"
    comayagua_dir = root / "José Carpio" / "Comayagua"
    sta_cruz_dir = root / "José Carpio" / "Santa Cruz de Yojoa"
    comayagua_dir.mkdir(parents=True)
    sta_cruz_dir.mkdir(parents=True)
    shutil.copy2(f"{FIXTURES}/COMAYAGUA JUNIO.xlsx", comayagua_dir / "COMAYAGUA junio.xlsx")
    shutil.copy2(f"{FIXTURES}/STA CRUZ MAYO.xlsx", sta_cruz_dir / "STA CRUZ mayo.xlsx")
    return root


@pytest.fixture
def comayagua_composite():
    composite = load_composite_route("COMAYAGUA")
    assert composite is not None, "config/route_composition.json deberia tener 'COMAYAGUA'"
    return composite


@pytest.fixture
def sap_records_for_comayagua():
    """Union de los clientes de ambos archivos reales (simula que SAP trae
    el catalogo completo, que incluye a los clientes de las dos zonas)."""
    a = LocalMockDataSource(f"{FIXTURES}/COMAYAGUA JUNIO.xlsx", sheet_name="Hoja2").fetch_clients()
    b = LocalMockDataSource(f"{FIXTURES}/STA CRUZ MAYO.xlsx", sheet_name="Hoja2").fetch_clients()
    seen = {}
    for rec in a + b:
        seen.setdefault(rec.codigo, rec)
    return list(seen.values())


def test_composite_config_loaded_from_json():
    composite = load_composite_route("COMAYAGUA")
    assert composite.vendor == "José Carpio"
    names = [c.name for c in composite.sorted_components]
    assert names == ["Comayagua", "Santa Cruz de Yojoa"]
    assert composite.vigente_desde is None  # no se invento una fecha historica


def test_generate_composite_route_combines_in_order(
    comayagua_routes_root, comayagua_composite, sap_records_for_comayagua, tmp_path
):
    comayagua_config = load_route_config("COMAYAGUA")
    sta_cruz_config = load_route_config("STA_CRUZ")

    comayagua_roster_len = len(
        peek_roster(comayagua_routes_root / "José Carpio" / "Comayagua" / "COMAYAGUA junio.xlsx", comayagua_config)
    )
    sta_cruz_roster_len = len(
        peek_roster(
            comayagua_routes_root / "José Carpio" / "Santa Cruz de Yojoa" / "STA CRUZ mayo.xlsx", sta_cruz_config
        )
    )
    assert comayagua_roster_len == 54
    assert sta_cruz_roster_len == 16

    output = tmp_path / "COMAYAGUA 15 octubre 2026.xlsx"
    result = generate_composite_route(
        composite=comayagua_composite,
        routes_root=comayagua_routes_root,
        output_path=output,
        new_date=date(2026, 10, 15),
        sap_records=sap_records_for_comayagua,
    )

    total_expected = comayagua_roster_len + sta_cruz_roster_len
    assert result.generation.client_count == total_expected

    # Comayagua primero, Santa Cruz al final, orden interno preservado.
    componentes = [e.componente for e in result.combined_roster]
    assert componentes[:comayagua_roster_len] == ["Comayagua"] * comayagua_roster_len
    assert componentes[comayagua_roster_len:] == ["Santa Cruz de Yojoa"] * sta_cruz_roster_len

    # Trazabilidad: se puede saber de que componente vino cada cliente.
    last_client = result.combined_roster[-1]
    assert last_client.componente == "Santa Cruz de Yojoa"

    wb = openpyxl.load_workbook(output)
    ws = wb["COMAYAGUA"]

    # Numeracion consecutiva de principio a fin, cruzando el limite entre componentes.
    for i, row in enumerate(range(8, 8 + total_expected), start=1):
        assert ws[f"A{row}"].value == i

    total_row = 8 + total_expected
    assert ws[f"E{total_row}"].value == "TOTAL"
    assert ws[f"F{total_row}"].value == f"=SUM(F8:F{total_row - 1})"

    report = validators.validate_workbook(output, comayagua_config, expected_date=date(2026, 10, 15))
    assert report.ok, report.errors


def test_composite_generation_never_touches_either_original(
    comayagua_routes_root, comayagua_composite, sap_records_for_comayagua, tmp_path
):
    comayagua_base = comayagua_routes_root / "José Carpio" / "Comayagua" / "COMAYAGUA junio.xlsx"
    sta_cruz_base = comayagua_routes_root / "José Carpio" / "Santa Cruz de Yojoa" / "STA CRUZ mayo.xlsx"
    h1_before, h2_before = file_hash(comayagua_base), file_hash(sta_cruz_base)

    generate_composite_route(
        composite=comayagua_composite,
        routes_root=comayagua_routes_root,
        output_path=tmp_path / "COMAYAGUA 15 octubre 2026.xlsx",
        new_date=date(2026, 10, 15),
        sap_records=sap_records_for_comayagua,
    )

    assert file_hash(comayagua_base) == h1_before
    assert file_hash(sta_cruz_base) == h2_before

    # Y el historico original de verdad (fuera de cualquier copia de trabajo)
    # tampoco se toco nunca durante esta fase.
    assert file_hash(f"{FIXTURES}/COMAYAGUA JUNIO.xlsx") == file_hash(
        "/Users/alejandrom/Documents/Cosprobell/Rutas de Cosprobell/COMAYAGUA JUNIO.xlsx"
    )
    assert file_hash(f"{FIXTURES}/STA CRUZ MAYO.xlsx") == file_hash(
        "/Users/alejandrom/Documents/Cosprobell/Rutas de Cosprobell/STA CRUZ MAYO.xlsx"
    )


def test_composite_generation_is_generic_not_hardcoded(
    comayagua_routes_root, sap_records_for_comayagua, tmp_path
):
    """Una composicion NUEVA definida solo en config (no en el encargo
    original) debe funcionar igual, sin tocar composite_route_service.py --
    confirma que el soporte es generico, no un caso especial de Comayagua."""

    synthetic = CompositeRouteConfig(
        composite_id="PRUEBA",
        vendor="José Carpio",
        template_route_id="COMAYAGUA",
        components=[
            RouteComponent(name="Zona B", route_id="STA_CRUZ", folder_name="Santa Cruz de Yojoa", order=1),
            RouteComponent(name="Zona A", route_id="COMAYAGUA", folder_name="Comayagua", order=2),
        ],
        vigente_desde=None,
    )

    output = tmp_path / "PRUEBA 15 octubre 2026.xlsx"
    result = generate_composite_route(
        composite=synthetic,
        routes_root=comayagua_routes_root,
        output_path=output,
        new_date=date(2026, 10, 15),
        sap_records=sap_records_for_comayagua,
    )
    # Orden invertido respecto al caso Comayagua: Santa Cruz (16) primero.
    componentes = [e.componente for e in result.combined_roster]
    assert componentes[0] == "Zona B"
    assert componentes[-1] == "Zona A"
    assert result.generation.client_count == 70


def test_composite_missing_template_match_raises_clear_error(comayagua_routes_root, sap_records_for_comayagua, tmp_path):
    bad = CompositeRouteConfig(
        composite_id="MAL_CONFIGURADA",
        vendor="José Carpio",
        template_route_id="CORTES",  # no coincide con el route_id de ningun componente
        components=[
            RouteComponent(name="Comayagua", route_id="COMAYAGUA", folder_name="Comayagua", order=1),
        ],
        vigente_desde=None,
    )
    with pytest.raises(CompositeRouteError):
        generate_composite_route(
            composite=bad,
            routes_root=comayagua_routes_root,
            output_path=tmp_path / "salida.xlsx",
            new_date=date(2026, 10, 15),
            sap_records=sap_records_for_comayagua,
        )
