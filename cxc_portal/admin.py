"""Administracion local del portal por linea de comandos (sin pasar por la red).

    python -m cxc_portal.admin crear-usuario --estado DIR --usuario ana --rol gerencia [--vendedor "Nombre"] [--generar-clave]
    python -m cxc_portal.admin sembrar --estado DIR --datos DIR
    python -m cxc_portal.admin usuarios --estado DIR
    python -m cxc_portal.admin integrar-ruta --estado DIR --ruta ID --archivo JSON [--aplicar]

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


def cmd_respaldar(a) -> int:
    destino = _estado(a).respaldar(Path(a.destino), a.conservar)
    print(f"Respaldo verificado: {destino} (+ .sha256)")
    return 0


def cmd_restaurar(a) -> int:
    try:
        d = Estado.restaurar(Path(a.archivo), Path(a.estado) / "portal.sqlite3", a.forzar)
    except ErrorNegocio as e:
        raise SystemExit(f"Error: {e.mensaje}")
    problemas = Estado(d).verificar_documentos()
    print(f"Restaurado en {d}. Documentos verificados contra sus hashes: {'todos coinciden' if not problemas else 'PROBLEMAS: ' + '; '.join(problemas)}. Reinicie el portal.")
    return 1 if problemas else 0


def cmd_exportar(a) -> int:
    import json
    destino = Path(a.destino)
    destino.write_text(json.dumps(_estado(a).exportar_json(), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    try:
        os.chmod(destino, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    print(f"Exportación escrita en {destino}. Contiene hashes de contraseña y semillas 2FA: trátela como secreto.")
    return 0


def cmd_importar(a) -> int:
    import json
    try:
        print(_estado(a).importar_json(json.loads(Path(a.archivo).read_text(encoding="utf-8"))))
    except ErrorNegocio as e:
        raise SystemExit(f"Error: {e.mensaje}")
    return 0


def cmd_restablecer_2fa(a) -> int:
    est = _estado(a)
    uid = next((u["id"] for u in est.listar_usuarios() if u["usuario"].lower() == a.usuario.lower()), None)
    if uid is None:
        raise SystemExit("Usuario no encontrado.")
    est.totp_restablecer(ACTOR, uid)
    print("Segundo factor restablecido: deberá volver a activarlo en su próximo acceso.")
    return 0


def cmd_importar_documento(a) -> int:
    from .store import PortalServicio
    est = _estado(a)
    sv = PortalServicio(est, None, Path(a.estado), None, Path(a.datos))
    try:
        d = sv.importar_documento(ACTOR, a.ruta.upper(), a.archivo, a.motivo or "", a.nueva_version, a.confirmar)
        if a.proponer_clientes:
            n = len(sv.proponer_desde_documento(ACTOR, a.ruta.upper(), d["id"]))
            print(f"{n} cliente(s) propuesto(s) como candidatos (pendientes de confirmación humana).")
    except ErrorNegocio as e:
        raise SystemExit(f"Error: {e.mensaje}")
    print(f"Documento v{d['version']} registrado ({d['contenido']['total_clientes']} clientes, {len(d['contenido']['hojas'])} hojas, sha256 {d['sha256'][:16]}…).")
    return 0


def cmd_integrar_ruta(a) -> int:
    from .integrar_ruta import ConflictoIntegracion, integrar
    try:
        print(integrar(_estado(a), ACTOR, Path(a.archivo), a.ruta.upper(), aplicar=a.aplicar))
    except ConflictoIntegracion as e:
        raise SystemExit("CONFLICTO (no se escribió nada):\n  - " + "\n  - ".join(e.conflictos))
    except ErrorNegocio as e:
        raise SystemExit(f"Error: {e.mensaje}")
    if not a.aplicar:
        print("Simulación: sin cambios. Repita con --aplicar para escribir.")
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
    for nombre, fn, extra in (("respaldar", cmd_respaldar, [("--destino", dict(required=True)), ("--conservar", dict(type=int, default=14))]),
                              ("restaurar", cmd_restaurar, [("--archivo", dict(required=True)), ("--forzar", dict(action="store_true"))]),
                              ("exportar", cmd_exportar, [("--destino", dict(required=True))]), ("importar", cmd_importar, [("--archivo", dict(required=True))]),
                              ("restablecer-2fa", cmd_restablecer_2fa, [("--usuario", dict(required=True))])):
        p = sub.add_parser(nombre)
        p.add_argument("--estado", required=True)
        for flag, kw in extra:
            p.add_argument(flag, **kw)
        p.set_defaults(func=fn)
    p = sub.add_parser("importar-documento")
    p.add_argument("--estado", required=True)
    p.add_argument("--datos", required=True)
    p.add_argument("--ruta", required=True)
    p.add_argument("--archivo", required=True, help="Nombre del .xlsx dentro de <datos>/01_Rutas")
    p.add_argument("--motivo")
    p.add_argument("--nueva-version", action="store_true")
    p.add_argument("--confirmar", action="store_true")
    p.add_argument("--proponer-clientes", action="store_true")
    p.set_defaults(func=cmd_importar_documento)
    p = sub.add_parser("integrar-ruta")
    p.add_argument("--estado", required=True)
    p.add_argument("--ruta", required=True)
    p.add_argument("--archivo", required=True, help="JSON de organización física aprobada")
    p.add_argument("--aplicar", action="store_true", help="Sin esta bandera solo simula y detecta conflictos")
    p.set_defaults(func=cmd_integrar_ruta)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
