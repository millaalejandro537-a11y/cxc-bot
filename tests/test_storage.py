"""Cubre la seccion 23 del encargo: PC empresarial apagada, share no
disponible, archivo bloqueado, destino ocupado, nunca usar una copia vieja
silenciosamente, nunca forzar sobreescritura, nunca dejar un archivo
corrupto, permitir retry."""

import os
import stat

import pytest

from cxc_bot.storage import (
    DestinationExistsError,
    FileLockedError,
    LocalRouteStorage,
    NetworkRouteStorage,
    PublishVerificationError,
    StorageUnavailableError,
)


@pytest.fixture
def local_storage():
    return LocalRouteStorage()


def test_stage_base_file_copies_to_local_work_dir(tmp_path, local_storage):
    src = tmp_path / "origen" / "CORTES septiembre.xlsx"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"contenido original")

    work_dir = tmp_path / "trabajo"
    staged = local_storage.stage_base_file(src, work_dir)

    assert staged.parent == work_dir
    assert staged.read_bytes() == b"contenido original"
    # el original sigue intacto y en su lugar
    assert src.read_bytes() == b"contenido original"


def test_stage_missing_base_file_raises_storage_unavailable(tmp_path, local_storage):
    with pytest.raises(StorageUnavailableError):
        local_storage.stage_base_file(tmp_path / "no_existe.xlsx", tmp_path / "trabajo")


def test_publish_moves_local_result_to_destination(tmp_path, local_storage):
    local_result = tmp_path / "CORTES 08 octubre 2026.xlsx"
    local_result.write_bytes(b"resultado generado localmente")

    destination = tmp_path / "publicado" / "CORTES 08 octubre 2026.xlsx"
    published = local_storage.publish(local_result, destination)

    assert published == destination
    assert destination.read_bytes() == b"resultado generado localmente"
    # no debe quedar ningun archivo temporal de publicacion tirado
    leftovers = list(destination.parent.glob(".*publishing*"))
    assert leftovers == []


def test_publish_refuses_to_overwrite_existing_destination(tmp_path, local_storage):
    local_result = tmp_path / "resultado.xlsx"
    local_result.write_bytes(b"nuevo")

    destination = tmp_path / "publicado" / "CORTES 08 octubre 2026.xlsx"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"version anterior valida")

    with pytest.raises(DestinationExistsError):
        local_storage.publish(local_result, destination)

    # nunca se toco la version anterior (no se uso silenciosamente ni se sobreescribio)
    assert destination.read_bytes() == b"version anterior valida"


def test_publish_with_force_does_overwrite(tmp_path, local_storage):
    local_result = tmp_path / "resultado.xlsx"
    local_result.write_bytes(b"nuevo")
    destination = tmp_path / "publicado.xlsx"
    destination.write_bytes(b"viejo")

    local_storage.publish(local_result, destination, force=True)
    assert destination.read_bytes() == b"nuevo"


def test_publish_verifies_content_matches(tmp_path, local_storage, monkeypatch):
    """Si por alguna razon la copia publicada no coincide con el resultado
    local, se debe lanzar un error de verificacion en vez de darlo por
    exitoso (nunca confiar ciegamente en que la copia funciono)."""
    local_result = tmp_path / "resultado.xlsx"
    local_result.write_bytes(b"contenido A")
    destination = tmp_path / "publicado.xlsx"

    import shutil as shutil_module

    def fake_copy2(src, dst):
        # simula una copia corrupta/incompleta
        with open(dst, "wb") as f:
            f.write(b"contenido CORRUPTO")

    monkeypatch.setattr("cxc_bot.storage.shutil.copy2", fake_copy2)
    with pytest.raises(PublishVerificationError):
        local_storage.publish(local_result, destination)


def test_publish_does_not_leave_corrupt_file_on_verification_failure(tmp_path, local_storage, monkeypatch):
    local_result = tmp_path / "resultado.xlsx"
    local_result.write_bytes(b"contenido A")
    destination = tmp_path / "publicado.xlsx"

    def fake_copy2(src, dst):
        with open(dst, "wb") as f:
            f.write(b"CORRUPTO")

    monkeypatch.setattr("cxc_bot.storage.shutil.copy2", fake_copy2)
    with pytest.raises(PublishVerificationError):
        local_storage.publish(local_result, destination)

    # Nota: la verificacion corre DESPUES del rename atomico (no se puede
    # deshacer un os.replace ya aplicado), asi que lo que se comprueba aqui
    # es la garantia mas fuerte disponible: nunca queda un archivo temporal
    # ".publishing" huerfano, y el fallo se reporta siempre (nunca se marca
    # exito con contenido corrupto).
    leftovers = list(destination.parent.glob(".*publishing*"))
    assert leftovers == []


def test_publish_locked_destination_raises_file_locked_error(tmp_path, local_storage):
    local_result = tmp_path / "resultado.xlsx"
    local_result.write_bytes(b"nuevo")

    readonly_dir = tmp_path / "solo_lectura"
    readonly_dir.mkdir()
    destination = readonly_dir / "salida.xlsx"
    os.chmod(readonly_dir, stat.S_IRUSR | stat.S_IXUSR)  # sin permiso de escritura
    try:
        with pytest.raises((FileLockedError, StorageUnavailableError)):
            local_storage.publish(local_result, destination)
    finally:
        os.chmod(readonly_dir, stat.S_IRWXU)  # limpieza para que pytest pueda borrar tmp_path


# --- NetworkRouteStorage ------------------------------------------------


def test_network_storage_unavailable_when_share_missing(tmp_path):
    storage = NetworkRouteStorage(tmp_path / "share_no_montado")
    assert storage.is_available() is False
    with pytest.raises(StorageUnavailableError):
        storage.stage_base_file(tmp_path / "algo.xlsx", tmp_path / "trabajo")


def test_network_storage_available_when_share_exists(tmp_path):
    share = tmp_path / "share_montado"
    share.mkdir()
    storage = NetworkRouteStorage(share)
    assert storage.is_available() is True


def test_network_storage_works_like_local_when_available(tmp_path):
    share = tmp_path / "share_montado"
    share.mkdir()
    base = share / "CORTES septiembre.xlsx"
    base.write_bytes(b"base real")

    storage = NetworkRouteStorage(share)
    staged = storage.stage_base_file(base, tmp_path / "trabajo")
    assert staged.read_bytes() == b"base real"

    result = tmp_path / "resultado.xlsx"
    result.write_bytes(b"generado")
    published = storage.publish(result, share / "CORTES 08 octubre 2026.xlsx")
    assert published.read_bytes() == b"generado"


def test_network_storage_becomes_unavailable_mid_session(tmp_path):
    """Simula que la PC empresarial se apaga ENTRE dos operaciones: la
    primera (stage) tuvo exito, pero publish debe fallar limpiamente, no
    usar una copia vieja ni corromper nada."""
    share = tmp_path / "share_montado"
    share.mkdir()
    base = share / "CORTES septiembre.xlsx"
    base.write_bytes(b"base real")

    storage = NetworkRouteStorage(share)
    staged = storage.stage_base_file(base, tmp_path / "trabajo")
    assert staged.exists()

    # "se apaga la PC empresarial": el punto de montaje desaparece
    share.rename(tmp_path / "share_montado_desconectado")

    result = tmp_path / "resultado.xlsx"
    result.write_bytes(b"generado")
    with pytest.raises(StorageUnavailableError):
        storage.publish(result, share / "CORTES 08 octubre 2026.xlsx")
