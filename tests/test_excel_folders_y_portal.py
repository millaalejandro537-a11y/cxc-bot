"""Generador de rutas con folders por zona (opt-in) y generacion del Excel desde el portal conservando el formato operativo."""

import shutil
from datetime import date
from pathlib import Path

import openpyxl
import pytest

from cxc_bot import excel_service, validators
from cxc_bot.config import load_route_config
from cxc_bot.models import ClientEntry, SapClientRecord
from tests.portal_helpers import ACTOR, CLAVE_ADMIN, CLAVE_GER, CLAVE_VEN, Servidor, cliente_con_login, crear_entorno

FIXTURE = Path("tests/fixtures/rutas_reales/LAS VEGAS MAYO.xlsx")


def _sap(n=4):
    return [SapClientRecord(f"C9000{i}", f"CLIENTE {i} (ZONA)", 100.0 * i) for i in range(1, n + 1)]


def _roster(folders):
    return [ClientEntry(codigo=f"C9000{i}", folder=f) for i, f in enumerate(folders, 1)]


def test_modo_folder_escribe_folders_y_el_validador_lo_acepta(tmp_path):
    cfg = load_route_config("LAS VEGAS")
    out = tmp_path / "o.xlsx"
    excel_service.generate_route(cfg, FIXTURE, out, date(2026, 10, 13), _sap(), override_roster=_roster(["1 LV", "2 LV", "1 PB", "3 PB"]))
    ws = openpyxl.load_workbook(out)[cfg.main_sheet]
    assert [ws[f"A{r}"].value for r in range(8, 12)] == ["1 LV", "2 LV", "1 PB", "3 PB"]       # huecos permitidos: no se renumera
    assert validators.validate_workbook(out, cfg, expected_date=date(2026, 10, 13), folder_mode=True).ok
    assert not validators.validate_workbook(out, cfg, expected_date=date(2026, 10, 13)).ok       # modo clasico sigue exigiendo 1..n


def test_comportamiento_clasico_no_cambia_sin_folders(tmp_path):
    cfg = load_route_config("LAS VEGAS")
    out = tmp_path / "o.xlsx"
    excel_service.generate_route(cfg, FIXTURE, out, date(2026, 10, 13), _sap(), override_roster=[ClientEntry(codigo=f"C9000{i}") for i in range(1, 5)])
    assert [openpyxl.load_workbook(out)[cfg.main_sheet][f"A{r}"].value for r in range(8, 12)] == [1, 2, 3, 4]
    assert validators.validate_workbook(out, cfg, expected_date=date(2026, 10, 13)).ok


@pytest.mark.parametrize("folders,fragmento", [(["1 LV", "1 LV", "2 LV", "3 LV"], "duplicados"), (["1 LV", None, "2 LV", "3 LV"], "TODOS")])
def test_modo_folder_rechaza_duplicados_y_mezclas(tmp_path, folders, fragmento):
    cfg = load_route_config("LAS VEGAS")
    with pytest.raises(excel_service.ExcelServiceError, match=fragmento):
        excel_service.generate_route(cfg, FIXTURE, tmp_path / "o.xlsx", date(2026, 10, 13), _sap(), override_roster=_roster(folders))


def test_nombre_propio_de_ruta_se_escribe_literal_y_se_valida(tmp_path):
    cfg = load_route_config("LAS VEGAS")
    roster = _roster(["1 LV", "2 LV", "3 LV", "4 LV"])
    roster[1].nombre_ruta = "NOMBRE PROPIO DE RUTA"
    out = tmp_path / "o.xlsx"
    excel_service.generate_route(cfg, FIXTURE, out, date(2026, 10, 13), _sap(), override_roster=roster)
    ws = openpyxl.load_workbook(out)[cfg.main_sheet]
    assert ws["C9"].value == "NOMBRE PROPIO DE RUTA" and str(ws["C8"].value).startswith("=VLOOKUP")
    assert validators.validate_workbook(out, cfg, expected_date=date(2026, 10, 13), folder_mode=True, nombres_literales={"C90002": "NOMBRE PROPIO DE RUTA"}).ok
    assert not validators.validate_workbook(out, cfg, expected_date=date(2026, 10, 13), folder_mode=True).ok   # sin declararlo, un literal se rechaza


# ----------------------------------------------------------------------------- desde el portal
@pytest.fixture()
def env(tmp_path):
    ctx = crear_entorno(tmp_path)
    ctx.estado.guardar_ruta(ACTOR, "LAS_VEGAS", "Las Vegas", "Vendedor Demo", "FORANEA", None, zonas={"LV": "Las Vegas", "PB": "Peña Blanca"},
                            clientes=[{"codigo": "C90001", "folder": "1 LV", "nombre": "SALON DEMO EXCELENTE (A)"},
                                      {"codigo": "C90002", "folder": "2 LV", "nombre": "NOMBRE DE RUTA DISTINTO"},
                                      {"codigo": "C90003", "folder": "1 PB", "nombre": "SALON DEMO REGULAR (C)"}])
    rutas = tmp_path / "datos" / "01_Rutas"
    rutas.mkdir(parents=True)
    shutil.copy2(FIXTURE, rutas / "base.xlsx")
    s = Servidor(ctx)
    yield s, ctx, tmp_path
    s.cerrar()


def test_generar_excel_desde_el_portal_conserva_formato_y_no_toca_la_base(env):
    s, ctx, tmp = env
    base = tmp / "datos" / "01_Rutas" / "base.xlsx"
    antes = base.read_bytes()
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    assert ger.get("/api/excel/bases")[2]["bases"] == ["base.xlsx"]
    st, cab, body = ger.post("/api/rutas/LAS_VEGAS/generar-excel", {"base": "base.xlsx", "fecha_salida": "2026-10-13"})
    assert st == 200 and body[:2] == b"PK" and "BORRADOR" in cab["content-disposition"] and "BORRADOR" in cab["x-cxc-aviso"]
    out = tmp / "salida.xlsx"
    out.write_bytes(body)
    cfg = load_route_config("LAS VEGAS")
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == [cfg.main_sheet, cfg.data_sheet]                          # exactamente las dos hojas operativas
    ws = wb[cfg.main_sheet]
    assert [ws[f"A{r}"].value for r in range(8, 11)] == ["1 LV", "2 LV", "1 PB"]
    assert [ws[f"B{r}"].value for r in range(8, 11)] == ["C90001", "C90002", "C90003"]
    assert str(ws["C8"].value).startswith("=VLOOKUP") and ws["C9"].value == "NOMBRE DE RUTA DISTINTO"   # nombre propio de la ruta, literal
    vals = openpyxl.load_workbook(out, data_only=True)[cfg.main_sheet]
    assert vals["E8"].value == 0 and vals["E9"].value == 0 and vals["E10"].value == 0 and vals["B5"].value.date() == date(2026, 10, 13)
    assert validators.validate_workbook(out, cfg, expected_date=date(2026, 10, 13), folder_mode=True, nombres_literales={"C90002": "NOMBRE DE RUTA DISTINTO"}).ok
    assert base.read_bytes() == antes                                                   # el Excel base no se modifica
    assert any(f.name.startswith("LAS_VEGAS_2026-10-13_BORRADOR") for f in (tmp / "estado" / "salidas").iterdir())
    assert any(e["accion"] == "excel.generar" for e in ctx.estado.listar_auditoria())


def test_generar_excel_permisos_y_validaciones(env):
    s, _, _ = env
    ven = cliente_con_login(s, "ven1", CLAVE_VEN)
    assert ven.post("/api/rutas/LAS_VEGAS/generar-excel", {"base": "base.xlsx", "fecha_salida": "2026-10-13"})[0] == 403
    assert ven.get("/api/excel/bases")[0] == 403
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    assert adm.post("/api/rutas/LAS_VEGAS/generar-excel", {"base": "base.xlsx", "fecha_salida": "13/10/2026"})[0] == 400
    for malo in ("../x.xlsx", "..\\x.xlsx", "base.txt", "no_existe.xlsx"):
        assert adm.post("/api/rutas/LAS_VEGAS/generar-excel", {"base": malo, "fecha_salida": "2026-10-13"})[0] == 400, malo
    assert adm.post("/api/rutas/NOPE/generar-excel", {"base": "base.xlsx", "fecha_salida": "2026-10-13"})[0] == 404
    # una ruta sin plantilla configurada en config/routes.json no se inventa
    st, _, js = adm.post("/api/rutas/DEMO/generar-excel", {"base": "base.xlsx", "fecha_salida": "2026-10-13"})
    assert st == 409 and "plantilla Excel" in js["error"]


# ----------------------------------------------------------------------------- las 14 rutas operativas: el generador desde el portal funciona con cada plantilla
RUTAS_Y_PLANTILLAS = [
    ("CORTES", "CORTES septiembre.xlsx"), ("LAS_VEGAS", "LAS VEGAS MAYO.xlsx"), ("LA_LIMA", "LIMA JUNIO 6.xlsx"), ("CHOLOMA", "CHOLOMA 19-5.xlsx"),
    ("PROGRESO", "PROGRESO 21-5.xlsx"), ("OLANCHO", "OLANCHO AGOSTO.xlsx"), ("YORO", "YOROABRIL.xlsx"), ("ROATAN", "ROATAN ABRIL.xlsx"),
    ("LITORAL", "LITORAL MARZO.xlsx"), ("OCCIDENTE", "OCCIDENTE MAYO.xlsx"), ("SANTA_BARBARA", "sta barbara Junio.xlsx"), ("VILLANUEVA", "VILLANUEVA MAYO 2.xlsx"),
    ("COMAYAGUA", "COMAYAGUA JUNIO.xlsx"), ("STA_CRUZ", "STA CRUZ MAYO.xlsx"),
]


@pytest.mark.parametrize("route_id,archivo", RUTAS_Y_PLANTILLAS)
def test_generar_excel_de_cada_ruta_operativa_con_dos_hojas_y_folders_por_zona(tmp_path, route_id, archivo):
    ctx = crear_entorno(tmp_path)
    ctx.estado.guardar_ruta(ACTOR, route_id, route_id.replace("_", " ").title(), "Vendedor Demo", "FORANEA", None, zonas={"ZA": "Zona A", "ZB": "Zona B"},
                            clientes=[{"codigo": "C90001", "folder": "1 ZA", "nombre": "SALON DEMO EXCELENTE (A)"}, {"codigo": "C90002", "folder": "2 ZA", "nombre": "SALON DEMO BUENO (B)"},
                                      {"codigo": "C90003", "folder": "1 ZB", "nombre": "SALON DEMO REGULAR (C)"}])
    rutas = tmp_path / "datos" / "01_Rutas"
    rutas.mkdir(parents=True)
    shutil.copy2(Path("tests/fixtures/rutas_reales") / archivo, rutas / "base.xlsx")
    datos, nombre, avisos = ctx.servicio.generar_excel(ACTOR, route_id, "base.xlsx", "2026-10-13")
    out = tmp_path / "o.xlsx"
    out.write_bytes(datos)
    from cxc_bot.config import RouteNotFoundError
    cfg = ctx.servicio._config_excel(ctx.estado.obtener_ruta(route_id))
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == [cfg.main_sheet, cfg.data_sheet]                       # exactamente dos hojas
    ws = wb[cfg.main_sheet]
    assert [ws[f"{cfg.num_col}{cfg.data_start_row + i}"].value for i in range(3)] == ["1 ZA", "2 ZA", "1 ZB"]
    assert [ws[f"{cfg.code_col}{cfg.data_start_row + i}"].value for i in range(3)] == ["C90001", "C90002", "C90003"]
