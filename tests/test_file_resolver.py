import os

import pytest

from cxc_bot.file_resolver import (
    AmbiguousBaseFileError,
    FileResolutionError,
    NoBaseFileFoundError,
    RouteFolderNotFoundError,
    VendorFolderNotFoundError,
    resolve_base_file,
)
from cxc_bot.models import RouteConfig
from datetime import date

REF_DATE = date(2026, 10, 13)  # "hoy"/fecha de salida usada como referencia en los tests


@pytest.fixture
def config():
    return RouteConfig(route_id="CORTES", vendor="Eric Brizuela", filename_prefix="CORTES")


def _touch(path, content=b"x", mtime=None):
    path.write_bytes(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def resolve(tmp_path, config, reference_date=REF_DATE, vendor="Eric Brizuela", route="Cortes"):
    return resolve_base_file(tmp_path, vendor, route, config, reference_date=reference_date)


# ---------------------------------------------------------------------------
# El nombre (mes) manda; mtime NUNCA decide entre meses distintos
# ---------------------------------------------------------------------------
def test_resolve_picks_the_later_month_regardless_of_mtime(tmp_path, config):
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    _touch(route_dir / "CORTES agosto.xlsx", mtime=2000)  # mtime mas nuevo...
    _touch(route_dir / "CORTES septiembre.xlsx", mtime=1000)  # ...pero mes mas viejo por mtime

    result = resolve(tmp_path, config)
    assert result.name == "CORTES septiembre.xlsx"


def test_old_month_file_recently_resaved_does_not_displace_the_newer_month(tmp_path, config):
    """Caso pedido explicitamente: alguien abre/guarda 'agosto' hoy mismo
    (mtime recentisimo) pero 'septiembre' sigue siendo el mes correcto."""
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    import time

    now = time.time()
    _touch(route_dir / "CORTES septiembre.xlsx", mtime=now - 1_000_000)  # viejo por mtime
    _touch(route_dir / "CORTES agosto.xlsx", mtime=now)  # resalvado justo ahora

    result = resolve(tmp_path, config)
    assert result.name == "CORTES septiembre.xlsx"


def test_resolve_is_not_alphabetical(tmp_path, config):
    """agosto > septiembre alfabeticamente, pero septiembre es cronologicamente
    mas reciente segun el nombre (que es lo que ahora manda)."""
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    _touch(route_dir / "CORTES septiembre.xlsx", mtime=1000)
    _touch(route_dir / "CORTES agosto.xlsx", mtime=1000)

    result = resolve(tmp_path, config)
    assert result.name == "CORTES septiembre.xlsx"


def test_handles_year_rollover_in_filenames_without_year(tmp_path, config):
    """'diciembre' visto con fecha de referencia en octubre de 2026 solo
    puede ser diciembre de 2025 (ya paso); no debe interpretarse como mas
    reciente que 'septiembre' de 2026 solo por el numero de mes."""
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    _touch(route_dir / "CORTES diciembre.xlsx", mtime=1000)  # diciembre 2025 (el unico que ya paso)
    _touch(route_dir / "CORTES septiembre.xlsx", mtime=1000)  # septiembre 2026

    result = resolve(tmp_path, config, reference_date=date(2026, 10, 13))
    assert result.name == "CORTES septiembre.xlsx"


def test_handles_multi_month_history_correctly(tmp_path, config):
    """Varios meses acumulados en la carpeta ('archivos historicos'): debe
    elegir el cronologicamente mas reciente, no el ultimo en iterdir()."""
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    for mes in ["junio", "julio", "agosto", "septiembre"]:
        _touch(route_dir / f"CORTES {mes}.xlsx", mtime=1000)

    result = resolve(tmp_path, config)
    assert result.name == "CORTES septiembre.xlsx"


# ---------------------------------------------------------------------------
# Meses validos / invalidos
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mes", [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
])
def test_all_valid_spanish_month_names_are_recognized(tmp_path, config, mes):
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    _touch(route_dir / f"CORTES {mes}.xlsx", mtime=1000)

    result = resolve(tmp_path, config)
    assert result.name == f"CORTES {mes}.xlsx"


def test_month_name_matching_is_case_and_accent_insensitive(tmp_path, config):
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    _touch(route_dir / "CORTES SEPTIEMBRE.xlsx", mtime=1000)

    result = resolve(tmp_path, config)
    assert result.name == "CORTES SEPTIEMBRE.xlsx"


@pytest.mark.parametrize("bad_name", [
    "CORTES setiembre.xlsx",       # mes mal escrito
    "CORTES 09.xlsx",              # mes numerico, no en español
    "CORTES septiembre2026.xlsx",  # trae año pegado, no coincide con ningun mes
    "CORTES.xlsx",                 # sin mes
    "CORTES resumen.xlsx",         # palabra que no es un mes
])
def test_invalid_or_unparseable_names_are_ignored_not_guessed(tmp_path, config, bad_name):
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    _touch(route_dir / bad_name, mtime=9999999)  # mtime muy "atractivo", no debe importar

    with pytest.raises(NoBaseFileFoundError):
        resolve(tmp_path, config)


def test_invalid_name_ignored_while_a_valid_one_is_still_picked(tmp_path, config):
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    _touch(route_dir / "CORTES resumen.xlsx", mtime=9999999)  # invalido, se ignora
    _touch(route_dir / "CORTES septiembre.xlsx", mtime=1)

    result = resolve(tmp_path, config)
    assert result.name == "CORTES septiembre.xlsx"


def test_resolve_ignores_unrelated_files(tmp_path, config):
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    _touch(route_dir / "CORTES septiembre.xlsx", mtime=1000)
    _touch(route_dir / "notas.txt", mtime=9999)
    _touch(route_dir / "~$CORTES septiembre.xlsx", mtime=9999)  # temporal de Excel abierto

    result = resolve(tmp_path, config)
    assert result.name == "CORTES septiembre.xlsx"


def test_resolve_normalizes_vendor_and_route_folder_names(tmp_path, config):
    """La carpeta en disco puede tener distinta capitalizacion/acentos que
    el nombre de negocio; se debe encontrar igual."""
    route_dir = tmp_path / "ERIC BRIZUELA" / "CORTES"
    route_dir.mkdir(parents=True)
    _touch(route_dir / "CORTES septiembre.xlsx", mtime=1000)

    result = resolve_base_file(tmp_path, "Eric Brizuela", "Cortés", config, reference_date=REF_DATE)
    assert result.name == "CORTES septiembre.xlsx"


def test_missing_vendor_folder_raises_specific_error(tmp_path, config):
    tmp_path.mkdir(exist_ok=True)
    with pytest.raises(VendorFolderNotFoundError):
        resolve(tmp_path, config)


def test_missing_route_folder_raises_specific_error(tmp_path, config):
    (tmp_path / "Eric Brizuela").mkdir(parents=True)
    with pytest.raises(RouteFolderNotFoundError):
        resolve(tmp_path, config)


def test_no_base_file_raises_specific_error(tmp_path, config):
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    with pytest.raises(NoBaseFileFoundError):
        resolve(tmp_path, config)


# ---------------------------------------------------------------------------
# Ambiguedad: solo puede pasar entre archivos del MISMO mes
# ---------------------------------------------------------------------------
def test_same_month_duplicate_resolved_via_mtime_tiebreak_when_clear(tmp_path, config):
    """Metadata (mtime) SOLO se usa como desempate, y solo entre archivos
    que ya comparten el mismo mes segun el nombre."""
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    _touch(route_dir / "CORTES septiembre.xlsx", mtime=1000)
    _touch(route_dir / "CORTES  septiembre.xlsx", mtime=5000)  # doble espacio: archivo distinto, mismo mes

    result = resolve(tmp_path, config)
    assert result.name == "CORTES  septiembre.xlsx"  # gana el mtime mas reciente


def test_same_month_duplicate_with_ambiguous_mtime_aborts(tmp_path, config):
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    _touch(route_dir / "CORTES septiembre.xlsx", mtime=1000.0)
    _touch(route_dir / "CORTES  septiembre.xlsx", mtime=1000.5)  # practicamente empatado

    with pytest.raises(AmbiguousBaseFileError):
        resolve(tmp_path, config)


def test_ambiguous_vendor_folder_names_raise_error(tmp_path, config):
    # Nombres distintos en disco (no colisionan ni en filesystems
    # case-insensitive) que normalizan al mismo vendedor: carpeta duplicada.
    (tmp_path / "Eric Brizuela").mkdir(parents=True)
    (tmp_path / "Eric  Brizuela").mkdir(parents=True)

    with pytest.raises(FileResolutionError):
        resolve(tmp_path, config)


# ---------------------------------------------------------------------------
# Año explicito en el nombre: "[RUTA] [mes] [año].xlsx"
# ---------------------------------------------------------------------------
def test_old_file_without_year_still_works(tmp_path, config):
    """Compatibilidad hacia atras: el formato historico sin año sigue
    resolviendo por inferencia, como siempre."""
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    _touch(route_dir / "CORTES septiembre.xlsx", mtime=1000)

    result = resolve(tmp_path, config)
    assert result.name == "CORTES septiembre.xlsx"


def test_new_file_with_explicit_year_is_recognized(tmp_path, config):
    """Formato preferido para archivos nuevos: '[RUTA] [mes] [año].xlsx'."""
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    _touch(route_dir / "CORTES octubre 2026.xlsx", mtime=1000)

    result = resolve(tmp_path, config, reference_date=date(2026, 11, 10))
    assert result.name == "CORTES octubre 2026.xlsx"


def test_same_month_different_explicit_years_picks_the_later_year(tmp_path, config):
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    _touch(route_dir / "CORTES septiembre 2025.xlsx", mtime=5000)  # mtime mas nuevo...
    _touch(route_dir / "CORTES septiembre 2026.xlsx", mtime=1000)  # ...pero año mas nuevo por nombre

    result = resolve(tmp_path, config, reference_date=date(2026, 10, 13))
    assert result.name == "CORTES septiembre 2026.xlsx"


def test_december_to_january_year_rollover_with_explicit_years(tmp_path, config):
    """Cambio de diciembre a enero: enero 2027 es mas reciente que
    diciembre 2026 aunque el numero de mes sea menor."""
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    _touch(route_dir / "CORTES diciembre 2026.xlsx", mtime=1000)
    _touch(route_dir / "CORTES enero 2027.xlsx", mtime=1000)

    result = resolve(tmp_path, config, reference_date=date(2027, 1, 15))
    assert result.name == "CORTES enero 2027.xlsx"


@pytest.mark.parametrize("bad_name", [
    "CORTES septiembre 999.xlsx",     # solo 3 digitos
    "CORTES septiembre 20266.xlsx",   # 5 digitos
    "CORTES septiembre 1899.xlsx",    # fuera de rango razonable
    "CORTES septiembre abcd.xlsx",    # no numerico
    "CORTES septiembre 20 26.xlsx",   # roto en tres tokens
])
def test_invalid_explicit_year_is_ignored_not_guessed(tmp_path, config, bad_name):
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    _touch(route_dir / bad_name, mtime=1000)

    with pytest.raises(NoBaseFileFoundError):
        resolve(tmp_path, config)


def test_invalid_explicit_year_ignored_while_valid_file_is_still_picked(tmp_path, config):
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    _touch(route_dir / "CORTES septiembre 20266.xlsx", mtime=9999999)  # invalido, se ignora
    _touch(route_dir / "CORTES agosto 2026.xlsx", mtime=1)

    result = resolve(tmp_path, config)
    assert result.name == "CORTES agosto 2026.xlsx"


def test_old_format_and_new_format_coexist_explicit_year_wins(tmp_path, config):
    """Coexistencia de 'septiembre.xlsx' (formato historico, año inferido)
    y 'septiembre 2026.xlsx' (formato nuevo, año explicito) para el MISMO
    mes/año: el año explicito prevalece, sin importar mtime."""
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    # El archivo SIN año tiene mtime mucho mas reciente -- no debe importar.
    _touch(route_dir / "CORTES septiembre.xlsx", mtime=9_000_000_000)
    _touch(route_dir / "CORTES septiembre 2026.xlsx", mtime=1)

    result = resolve(tmp_path, config, reference_date=date(2026, 10, 13))
    assert result.name == "CORTES septiembre 2026.xlsx"


def test_explicit_year_prevails_over_inference_and_mtime_even_when_inference_would_differ(tmp_path, config):
    """Ademas de mtime, el año explicito tambien le gana a la INFERENCIA aunque
    la inferencia normalmente hubiera apuntado a otro año."""
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    # Con reference_date en 2027, "septiembre.xlsx" (sin año) se infiere como
    # septiembre 2026 (la ultima ocurrencia antes de la fecha de referencia).
    # "septiembre 2026.xlsx" declara el mismo año explicitamente: deben
    # coincidir en el mismo grupo y el explicito debe ganar igual.
    _touch(route_dir / "CORTES septiembre.xlsx", mtime=9_000_000_000)
    _touch(route_dir / "CORTES septiembre 2026.xlsx", mtime=1)

    result = resolve(tmp_path, config, reference_date=date(2027, 3, 1))
    assert result.name == "CORTES septiembre 2026.xlsx"


def test_two_explicit_years_same_month_ambiguous_mtime_aborts(tmp_path, config):
    """Cuando AMBOS candidatos tienen año explicito e identico mes/año, el
    desempate sigue siendo por mtime -- y si esta tambien empatado, se aborta."""
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    _touch(route_dir / "CORTES septiembre 2026.xlsx", mtime=1000.0)
    _touch(route_dir / "CORTES  septiembre 2026.xlsx", mtime=1000.5)  # doble espacio: otro archivo

    with pytest.raises(AmbiguousBaseFileError):
        resolve(tmp_path, config)
