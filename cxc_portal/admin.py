"""Administracion local del portal por linea de comandos (sin pasar por la red).

    python -m cxc_portal.admin crear-usuario --estado DIR --usuario ana --rol gerencia [--vendedor "Nombre"] [--generar-clave]
    python -m cxc_portal.admin sembrar --estado DIR --datos DIR
    python -m cxc_portal.admin usuarios --estado DIR

La clave se pide por teclado (no queda en el historial) o, con --generar-clave, se crea una aleatoria que se guarda en
<estado>/credenciales_iniciales.txt (permisos restringidos); el usuario debe cambiarla en su primer acceso.
"""

from __future__ import annotations

import argparse
import getpass
import os
import stat
from pathlib import Path

from . import security as sec
from .estado import ErrorNegocio, Estado
from .sembrar import sembrar

ACTOR = {"id": None, "usuario": "cli-admin", "rol": "admin", "nombre": "CLI admin"}


def _estado(a) -> Estado:
    return Estado(Path(a.estado) / "portal.sqlite3")


def cmd_crear_usuario(a) -> int:
    est = _estado(a)
    if a.generar_clave:
        clave = sec.password_aleatoria()
    else:
        clave = getpass.getpass("Contraseña (≥12, 3 de 4 clases): ")
        if clave != getpass.getpass("Repita la contraseña: "):
            raise SystemExit("Las contraseñas no coinciden.")
    try:
        u = est.crear_usuario(ACTOR, a.usuario, a.nombre or a.usuario, a.rol, clave, a.vendedor)
    except ErrorNegocio as e:
        raise SystemExit(f"Error: {e.mensaje}")
    if a.generar_clave:
        destino = Path(a.estado) / "credenciales_iniciales.txt"
        with open(destino, "a", encoding="utf-8") as f:
            f.write(f"{u['usuario']}\t{clave}\t(cambiar en el primer acceso)\n")
        try:
            os.chmod(destino, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        print(f"Usuario '{u['usuario']}' creado ({u['rol']}). Clave inicial guardada en {destino} (no se muestra aquí).")
    else:
        print(f"Usuario '{u['usuario']}' creado ({u['rol']}). Deberá cambiar la contraseña en su primer acceso.")
    return 0


def cmd_sembrar(a) -> int:
    print(sembrar(_estado(a), Path(a.datos), ACTOR))
    return 0


def cmd_usuarios(a) -> int:
    for u in _estado(a).listar_usuarios():
        print(f"{u['usuario']:20} {u['rol']:9} activo={bool(u['activo'])} vendedor={u['vendedor'] or '-'} último acceso={u['ultimo_acceso'] or '-'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for nombre, fn in (("crear-usuario", cmd_crear_usuario), ("sembrar", cmd_sembrar), ("usuarios", cmd_usuarios)):
        p = sub.add_parser(nombre)
        p.add_argument("--estado", required=True, help="Directorio de estado (BD propia)")
        p.set_defaults(func=fn)
        if nombre == "crear-usuario":
            p.add_argument("--usuario", required=True)
            p.add_argument("--nombre")
            p.add_argument("--rol", required=True, choices=sec.ROLES)
            p.add_argument("--vendedor")
            p.add_argument("--generar-clave", action="store_true")
        if nombre == "sembrar":
            p.add_argument("--datos", required=True)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
