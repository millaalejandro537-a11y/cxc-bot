"""Abstraccion de almacenamiento de rutas: de donde se lee el archivo base y
a donde se publica el resultado.

Hoy (DEV/TEST y mientras la maquina personal sea la unica involucrada):
`LocalRouteStorage`, que opera directamente sobre el filesystem local.

Cuando `RUTA VENDEDORES` viva en la PC empresarial (montada por SMB en
`/Volumes/...` desde el Mac): `NetworkRouteStorage`. No necesita logica de
archivo distinta de `LocalRouteStorage` (un share SMB montado se ve como
una carpeta mas), pero SI agrega lo que un montaje de red puede fallar:
que no este disponible, que estar "ocupado"/bloqueado, o que la escritura
final no se pueda verificar. Ninguna de las dos clases deja el motor
acoplado a rutas absolutas: todas reciben `Path` desde afuera.

Flujo de produccion (seccion 22 del encargo):

    leer base (storage) -> copiar a carpeta de trabajo LOCAL -> procesar
    localmente (motor de Fase 1) -> validar -> publicar (storage, atomico)
    -> verificar publicacion -> (solo entonces) snapshot/exito

Si la publicacion falla o no se puede verificar, NUNCA se marca exito ni se
crea un snapshot oficial -- el runner seguira reportando el fallo especifico
(ver categorias en storage y su mapeo en runner.py).
"""

from __future__ import annotations

import hashlib
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path


class StorageError(Exception):
    category = "storage"


class StorageUnavailableError(StorageError):
    """La PC empresarial / el share de red no responde: apagada, sin red,
    credenciales invalidas, o el punto de montaje no existe."""

    category = "storage_no_disponible"


class FileLockedError(StorageError):
    """El archivo esta abierto/bloqueado (p.ej. alguien lo tiene abierto en
    Excel) o el destino esta ocupado."""

    category = "archivo_bloqueado"


class DestinationExistsError(StorageError):
    """Ya existe un archivo valido en el destino: nunca se sobreescribe
    silenciosamente."""

    category = "archivo_destino_ya_existente"


class PublishVerificationError(StorageError):
    """La copia se escribio pero no se pudo verificar que quedo identica al
    resultado local generado: no se confia en ella como publicacion exitosa."""

    category = "publicacion_no_verificada"


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RouteStorage(ABC):
    @abstractmethod
    def is_available(self) -> bool:
        """Chequeo rapido de disponibilidad (existe/responde el punto de
        montaje). No garantiza que una operacion puntual no falle igual."""
        raise NotImplementedError

    @abstractmethod
    def stage_base_file(self, remote_base_path: Path, local_work_dir: Path) -> Path:
        """Copia el archivo base (donde viva) a una carpeta de trabajo LOCAL
        y devuelve esa ruta local. El motor SIEMPRE procesa sobre esta copia
        local, nunca directamente sobre el storage."""
        raise NotImplementedError

    @abstractmethod
    def publish(self, local_result_path: Path, destination_path: Path, force: bool = False) -> Path:
        """Publica un resultado YA GENERADO Y VALIDADO localmente hacia su
        destino final en el storage. Debe ser atomico (nunca deja un archivo
        a medio escribir en el destino) y verificar que la copia quedo
        identica antes de devolver exito. Lanza DestinationExistsError,
        FileLockedError, StorageUnavailableError o PublishVerificationError
        segun corresponda; nunca sobreescribe silenciosamente."""
        raise NotImplementedError


class LocalRouteStorage(RouteStorage):
    """Storage en el mismo filesystem (DEV/TEST, o PROD si 'RUTA VENDEDORES'
    ya es una carpeta local/montada de forma transparente)."""

    def is_available(self) -> bool:
        return True

    def stage_base_file(self, remote_base_path: Path, local_work_dir: Path) -> Path:
        remote_base_path = Path(remote_base_path)
        if not remote_base_path.exists():
            raise StorageUnavailableError(f"No se encontro el archivo base a copiar: {remote_base_path}")
        local_work_dir = Path(local_work_dir)
        local_work_dir.mkdir(parents=True, exist_ok=True)
        staged = local_work_dir / remote_base_path.name
        try:
            shutil.copy2(remote_base_path, staged)
        except PermissionError as e:
            raise FileLockedError(f"No se pudo leer el archivo base (¿esta abierto?): {remote_base_path}: {e}")
        except OSError as e:
            raise StorageUnavailableError(f"Error leyendo el archivo base {remote_base_path}: {e}")
        return staged

    def publish(self, local_result_path: Path, destination_path: Path, force: bool = False) -> Path:
        local_result_path = Path(local_result_path)
        destination_path = Path(destination_path)

        if destination_path.exists() and not force:
            raise DestinationExistsError(
                f"Ya existe un archivo en el destino de publicacion: {destination_path}. "
                "No se sobreescribe sin confirmacion explicita."
            )

        destination_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = destination_path.with_name(f".{destination_path.name}.publishing")
        try:
            shutil.copy2(local_result_path, tmp_path)
            os.replace(tmp_path, destination_path)  # atomico dentro del mismo filesystem
        except PermissionError as e:
            _cleanup(tmp_path)
            raise FileLockedError(f"No se pudo escribir en el destino (¿esta abierto/bloqueado?): {destination_path}: {e}")
        except OSError as e:
            _cleanup(tmp_path)
            raise StorageUnavailableError(f"Error publicando en {destination_path}: {e}")

        if _file_hash(destination_path) != _file_hash(local_result_path):
            raise PublishVerificationError(
                f"El archivo publicado en {destination_path} no coincide con el resultado generado localmente."
            )
        return destination_path


class NetworkRouteStorage(RouteStorage):
    """Para cuando 'RUTA VENDEDORES' viva en un share SMB de la PC
    empresarial, montado en el Mac (p.ej. /Volumes/RutaVendedores). No hace
    falta una implementacion de archivo distinta a LocalRouteStorage -- un
    share montado se opera igual que una carpeta local -- pero SI se agrega
    un chequeo explicito de disponibilidad antes de cada operacion y
    mensajes de error especificos para las fallas tipicas de una red: PC
    apagada, share no montado, o permisos invalidos.

    Nota de implementacion: macOS no expone un "timeout" configurable para
    operaciones de archivo sobre SMB desde Python puro; un share caido a
    mitad de una copia se reporta igual como OSError generico, que aqui se
    traduce a StorageUnavailableError.
    """

    def __init__(self, share_root: Path):
        self.share_root = Path(share_root)

    def is_available(self) -> bool:
        try:
            return self.share_root.is_dir()
        except OSError:
            return False

    def _require_available(self) -> None:
        if not self.is_available():
            raise StorageUnavailableError(
                f"El share de red no esta disponible ahora mismo: {self.share_root} "
                "(la PC empresarial puede estar apagada, dormida, o el share desmontado)."
            )

    def stage_base_file(self, remote_base_path: Path, local_work_dir: Path) -> Path:
        self._require_available()
        return LocalRouteStorage().stage_base_file(remote_base_path, local_work_dir)

    def publish(self, local_result_path: Path, destination_path: Path, force: bool = False) -> Path:
        self._require_available()
        return LocalRouteStorage().publish(local_result_path, destination_path, force=force)


def _cleanup(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
