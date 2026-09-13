import os
import time

import pytest

from cxc_bot.file_resolver import (
    AmbiguousBaseFileError,
    NoBaseFileFoundError,
    RouteFolderNotFoundError,
    VendorFolderNotFoundError,
    resolve_base_file,
)
from cxc_bot.models import RouteConfig


@pytest.fixture
def config():
    return RouteConfig(route_id="CORTES", vendor="Eric Brizuela", filename_prefix="CORTES")


def _touch(path, content=b"x", mtime=None):
    path.write_bytes(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def test_resolve_picks_most_recent_by_mtime(tmp_path, config):
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    _touch(route_dir / "CORTES agosto.xlsx", mtime=1000)
    _touch(route_dir / "CORTES septiembre.xlsx", mtime=2000)

    result = resolve_base_file(tmp_path, "Eric Brizuela", "Cortes", config)
    assert result.name == "CORTES septiembre.xlsx"


def test_resolve_is_not_alphabetical(tmp_path, config):
    """agosto > septiembre alfabeticamente, pero septiembre es mas reciente
    cronologicamente (y por mtime, que es lo que se usa)."""
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    _touch(route_dir / "CORTES septiembre.xlsx", mtime=5000)
    _touch(route_dir / "CORTES agosto.xlsx", mtime=1000)

    result = resolve_base_file(tmp_path, "Eric Brizuela", "Cortes", config)
    assert result.name == "CORTES septiembre.xlsx"


def test_resolve_ignores_unrelated_files(tmp_path, config):
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    _touch(route_dir / "CORTES septiembre.xlsx", mtime=1000)
    _touch(route_dir / "notas.txt", mtime=9999)
    _touch(route_dir / "~$CORTES septiembre.xlsx", mtime=9999)  # temporal de Excel abierto

    result = resolve_base_file(tmp_path, "Eric Brizuela", "Cortes", config)
    assert result.name == "CORTES septiembre.xlsx"


def test_resolve_normalizes_vendor_and_route_folder_names(tmp_path, config):
    """La carpeta en disco puede tener distinta capitalizacion/acentos que
    el nombre de negocio; se debe encontrar igual."""
    route_dir = tmp_path / "ERIC BRIZUELA" / "CORTES"
    route_dir.mkdir(parents=True)
    _touch(route_dir / "CORTES septiembre.xlsx", mtime=1000)

    result = resolve_base_file(tmp_path, "Eric Brizuela", "Cortés", config)
    assert result.name == "CORTES septiembre.xlsx"


def test_missing_vendor_folder_raises_specific_error(tmp_path, config):
    tmp_path.mkdir(exist_ok=True)
    with pytest.raises(VendorFolderNotFoundError):
        resolve_base_file(tmp_path, "Eric Brizuela", "Cortes", config)


def test_missing_route_folder_raises_specific_error(tmp_path, config):
    (tmp_path / "Eric Brizuela").mkdir(parents=True)
    with pytest.raises(RouteFolderNotFoundError):
        resolve_base_file(tmp_path, "Eric Brizuela", "Cortes", config)


def test_no_base_file_raises_specific_error(tmp_path, config):
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    with pytest.raises(NoBaseFileFoundError):
        resolve_base_file(tmp_path, "Eric Brizuela", "Cortes", config)


def test_two_candidates_with_same_month_are_ambiguous(tmp_path, config):
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    _touch(route_dir / "CORTES septiembre.xlsx", mtime=1000)
    # Nombre distinto en disco (para no colisionar en filesystems case-insensitive
    # como APFS) pero que normaliza al MISMO mes: duplicado real.
    _touch(route_dir / "CORTES  septiembre.xlsx", mtime=2000)  # doble espacio

    with pytest.raises(AmbiguousBaseFileError):
        resolve_base_file(tmp_path, "Eric Brizuela", "Cortes", config)


def test_two_candidates_with_near_identical_mtime_are_ambiguous(tmp_path, config):
    route_dir = tmp_path / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    _touch(route_dir / "CORTES agosto.xlsx", mtime=1000.0)
    _touch(route_dir / "CORTES septiembre.xlsx", mtime=1000.5)  # practicamente empatado

    with pytest.raises(AmbiguousBaseFileError):
        resolve_base_file(tmp_path, "Eric Brizuela", "Cortes", config)


def test_ambiguous_vendor_folder_names_raise_error(tmp_path, config):
    # Nombres distintos en disco (no colisionan ni en filesystems
    # case-insensitive) que normalizan al mismo vendedor: carpeta duplicada.
    (tmp_path / "Eric Brizuela").mkdir(parents=True)
    (tmp_path / "Eric  Brizuela").mkdir(parents=True)
    from cxc_bot.file_resolver import FileResolutionError

    with pytest.raises(FileResolutionError):
        resolve_base_file(tmp_path, "Eric Brizuela", "Cortes", config)
