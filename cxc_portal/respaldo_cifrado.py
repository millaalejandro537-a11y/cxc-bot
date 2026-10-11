"""Respaldos CIFRADOS del portal (BD + documentos) con cifrado de sobre (envelope encryption) y biblioteca `cryptography`.

Construccion (solo primitivas estandar, nada propio):
  * Contenido: AES-256-GCM (cifrado autenticado) con una clave de datos ALEATORIA por respaldo y nonce aleatorio de 96 bits.
  * Clave de datos: envuelta con RSA-OAEP (SHA-256) para cada destinatario (clave PUBLICA). Varios destinatarios = varias llaves de custodia.
  * Cabecera (version, algoritmos, nonce, destinatarios) autenticada como AAD: alterar cualquier byte invalida el respaldo.
  * La clave PRIVADA (cifrada con contraseña, PKCS#8) NO vive en el servidor: el trabajo programado solo conoce claves publicas, de modo que
    quien comprometa el servidor no puede leer respaldos anteriores. Restaurar exige la clave privada del custodio.
Reutiliza `Estado.respaldar` (copia consistente de SQLite + espejo de documentos) y `Estado.restaurar` (verifica suma e integridad, conserva
.pre-restauracion); aqui solo se empaqueta, cifra, rota y verifica.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import stat
import struct
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .estado import ErrorNegocio, Estado

MAGIC = b"CXCBAK1\x00"
EXT = ".cxcbak"
PATRON = re.compile(r"^portal-(\d{8})-(\d{6})\.cxcbak$")
MIN_BITS = 2048
MAX_CABECERA = 64 * 1024


def _cripto():
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:  # pragma: no cover - depende del entorno
        raise ErrorNegocio("Falta la biblioteca 'cryptography' (requirements.txt): no se pueden cifrar ni descifrar respaldos.", 500) from e
    return InvalidTag, hashes, serialization, padding, rsa, AESGCM


def _oaep(padding, hashes):
    return padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


def _kid(clave_publica) -> str:
    _, _, serialization, *_ = _cripto()
    der = clave_publica.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()[:16]


# ------------------------------------------------------------------ claves
def generar_par_claves(contrasena: str, bits: int = 4096) -> Tuple[bytes, bytes, str]:
    """(PEM publica, PEM privada cifrada con la contraseña, identificador). La contraseña protege el archivo de la clave privada."""
    _, _, serialization, _, rsa, _ = _cripto()
    if bits < MIN_BITS:
        raise ErrorNegocio(f"Clave RSA demasiado corta (mínimo {MIN_BITS} bits).")
    if len(contrasena) < 16:
        raise ErrorNegocio("La contraseña de la clave privada debe tener al menos 16 caracteres.")
    priv = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    pem_priv = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.BestAvailableEncryption(contrasena.encode("utf-8")))
    pub = priv.public_key()
    pem_pub = pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    return pem_pub, pem_priv, _kid(pub)


def _cargar_publica(pem: bytes):
    _, _, serialization, _, rsa, _ = _cripto()
    try:
        k = serialization.load_pem_public_key(pem)
    except ValueError as e:
        raise ErrorNegocio("Clave pública inválida.") from e
    if not isinstance(k, rsa.RSAPublicKey) or k.key_size < MIN_BITS:
        raise ErrorNegocio(f"La clave pública debe ser RSA de al menos {MIN_BITS} bits.")
    return k


def _cargar_privada(pem: bytes, contrasena: str):
    _, _, serialization, _, rsa, _ = _cripto()
    try:
        k = serialization.load_pem_private_key(pem, password=contrasena.encode("utf-8"))
    except (ValueError, TypeError) as e:
        raise ErrorNegocio("Contraseña de la clave privada incorrecta o archivo de clave inválido.", 400) from e
    if not isinstance(k, rsa.RSAPrivateKey):
        raise ErrorNegocio("La clave privada debe ser RSA.")
    return k


# ------------------------------------------------------------------ cifrado / descifrado
def _empaquetar(carpeta: Path) -> bytes:
    """tar sin compresion con manifest.json (sha256 de cada archivo) + la BD + documentos/."""
    bd = next(iter(sorted(carpeta.glob("portal-*.sqlite3"))), None)
    if bd is None:
        raise ErrorNegocio("No se encontró la copia de la BD a empaquetar.", 500)
    archivos: Dict[str, Path] = {"portal.sqlite3": bd}
    docs = carpeta / "documentos"
    if docs.is_dir():
        for f in sorted(docs.rglob("*")):
            if f.is_file():
                archivos["documentos/" + f.relative_to(docs).as_posix()] = f
    manifest = {"formato": "cxc-respaldo/1", "archivos": {n: hashlib.sha256(p.read_bytes()).hexdigest() for n, p in archivos.items()}}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        def agregar(nombre: str, datos: bytes):
            ti = tarfile.TarInfo(nombre)
            ti.size, ti.mtime, ti.mode = len(datos), 0, 0o600
            tar.addfile(ti, io.BytesIO(datos))
        agregar("manifest.json", json.dumps(manifest, sort_keys=True).encode("utf-8"))
        for n, p in archivos.items():
            agregar(n, p.read_bytes())
    return buf.getvalue()


def cifrar_carpeta(carpeta: Path, publicas: Iterable[bytes], ahora: datetime) -> bytes:
    InvalidTag, hashes, _, padding, _, AESGCM = _cripto()
    claves = [_cargar_publica(p) for p in publicas]
    if not claves:
        raise ErrorNegocio("Indique al menos una clave pública de destinatario.")
    if len({_kid(k) for k in claves}) != len(claves):
        raise ErrorNegocio("Clave pública repetida.")
    dek, nonce = AESGCM.generate_key(bit_length=256), os.urandom(12)
    slots = [{"kid": _kid(k), "clave": base64.b64encode(k.encrypt(dek, _oaep(padding, hashes))).decode("ascii")} for k in claves]
    cab = json.dumps({"v": 1, "alg": "AES-256-GCM", "wrap": "RSA-OAEP-SHA256", "creado": ahora.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "nonce": base64.b64encode(nonce).decode("ascii"), "slots": slots}, sort_keys=True).encode("utf-8")
    prefijo = MAGIC + struct.pack(">I", len(cab)) + cab
    return prefijo + AESGCM(dek).encrypt(nonce, _empaquetar(carpeta), prefijo)   # el prefijo completo viaja como AAD


def _descifrar(datos: bytes, privada) -> bytes:
    InvalidTag, hashes, _, padding, _, AESGCM = _cripto()
    if not datos.startswith(MAGIC) or len(datos) < len(MAGIC) + 4 + 28:
        raise ErrorNegocio("El archivo no es un respaldo cifrado válido.", 400)
    (n,) = struct.unpack(">I", datos[len(MAGIC):len(MAGIC) + 4])
    if n > MAX_CABECERA or len(MAGIC) + 4 + n >= len(datos):
        raise ErrorNegocio("Cabecera del respaldo inválida.", 400)
    fin = len(MAGIC) + 4 + n
    try:
        cab = json.loads(datos[len(MAGIC) + 4:fin])
        nonce = base64.b64decode(cab["nonce"])
        assert cab["v"] == 1 and cab["alg"] == "AES-256-GCM" and cab["wrap"] == "RSA-OAEP-SHA256" and len(nonce) == 12
    except Exception as e:  # noqa: BLE001
        raise ErrorNegocio("Cabecera del respaldo inválida.", 400) from e
    kid = _kid(privada.public_key())
    slot = next((s for s in cab["slots"] if s.get("kid") == kid), None)
    if slot is None:
        raise ErrorNegocio("Esta clave privada no corresponde a ningún destinatario del respaldo.", 400)
    try:
        dek = privada.decrypt(base64.b64decode(slot["clave"]), _oaep(padding, hashes))
        return AESGCM(dek).decrypt(nonce, datos[fin:], datos[:fin])
    except (InvalidTag, ValueError) as e:
        raise ErrorNegocio("El respaldo está alterado o dañado (falló la autenticación del cifrado).", 400) from e


def extraer(archivo: Path, pem_privada: bytes, contrasena: str, destino: Path) -> Path:
    """Descifra, valida el manifiesto (sha256 de cada archivo) y deja `destino` con el formato que entiende `Estado.restaurar`:
    portal-*.sqlite3 + .sha256 + documentos/. Devuelve la ruta de la BD."""
    claro = _descifrar(Path(archivo).read_bytes(), _cargar_privada(pem_privada, contrasena))
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(claro), mode="r") as tar:
            miembros = {m.name: m for m in tar.getmembers()}
            if "manifest.json" not in miembros or any(not m.isfile() for m in miembros.values()):
                raise ErrorNegocio("Contenido del respaldo inválido.", 400)
            manifest = json.loads(tar.extractfile(miembros["manifest.json"]).read())
            if set(manifest["archivos"]) != set(miembros) - {"manifest.json"} or "portal.sqlite3" not in manifest["archivos"]:
                raise ErrorNegocio("El manifiesto no coincide con el contenido del respaldo.", 400)
            for nombre, esperado in manifest["archivos"].items():
                if nombre.startswith("/") or ".." in Path(nombre).parts or not (nombre == "portal.sqlite3" or nombre.startswith("documentos/")):
                    raise ErrorNegocio(f"Nombre de archivo no permitido en el respaldo: {nombre}", 400)
                datos = tar.extractfile(miembros[nombre]).read()
                if hashlib.sha256(datos).hexdigest() != esperado:
                    raise ErrorNegocio(f"El hash de '{nombre}' no coincide con el manifiesto.", 400)
                if nombre == "portal.sqlite3":
                    bd = destino / "portal-restaurado.sqlite3"
                    bd.write_bytes(datos)
                    (destino / (bd.name + ".sha256")).write_text(f"{esperado}  {bd.name}\n", encoding="utf-8")
                else:
                    f = destino / nombre
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_bytes(datos)
    except (tarfile.TarError, json.JSONDecodeError, KeyError) as e:
        raise ErrorNegocio("Contenido del respaldo inválido.", 400) from e
    return destino / "portal-restaurado.sqlite3"


# ------------------------------------------------------------------ operaciones
def _privado(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def crear_respaldo(estado: Estado, publicas: Iterable[bytes], destino_dir: Path, ahora: Optional[datetime] = None) -> Path:
    """Respaldo consistente + cifrado. La copia en claro vive solo en un directorio temporal privado y se elimina siempre."""
    ahora = ahora or estado.reloj()
    publicas = list(publicas)
    destino_dir = Path(destino_dir)
    destino_dir.mkdir(parents=True, exist_ok=True)
    nombre = f"portal-{ahora.strftime('%Y%m%d-%H%M%S')}{EXT}"
    final = destino_dir / nombre
    if final.exists():
        raise ErrorNegocio(f"Ya existe {nombre}: no se sobrescribe un respaldo.", 409)
    tmp = Path(tempfile.mkdtemp(prefix="cxcbak-"))
    try:
        estado.respaldar(tmp, conservar=0)
        blob = cifrar_carpeta(tmp, publicas, ahora)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    parcial = destino_dir / (nombre + ".parcial")
    parcial.write_bytes(blob)
    _privado(parcial)
    os.replace(parcial, final)
    (destino_dir / (nombre + ".sha256")).write_text(f"{hashlib.sha256(blob).hexdigest()}  {nombre}\n", encoding="utf-8")
    return final


def rotar(destino_dir: Path, diarios: int = 14, mensuales: int = 6) -> List[str]:
    """Conserva los ultimos `diarios` respaldos y el MAS RECIENTE de cada uno de los ultimos `mensuales` meses; borra el resto. Nunca deja 0."""
    destino_dir = Path(destino_dir)
    todos = sorted(f.name for f in destino_dir.glob("portal-*" + EXT) if PATRON.match(f.name))
    conservar = set(todos[-max(1, diarios):])
    por_mes: Dict[str, str] = {}
    for n in todos:
        por_mes[PATRON.match(n).group(1)[:6]] = n          # el ultimo de cada mes (orden ascendente)
    meses = sorted(por_mes)
    for mes in meses[max(0, len(meses) - max(0, mensuales)):]:   # (no usar [-0:], que devolveria todos)
        conservar.add(por_mes[mes])
    borrados = [n for n in todos if n not in conservar]
    for n in borrados:
        (destino_dir / n).unlink(missing_ok=True)
        (destino_dir / (n + ".sha256")).unlink(missing_ok=True)
    return borrados


def verificar_estructura(archivo: Path) -> dict:
    """Comprobacion SIN clave privada (la que puede hacer el servidor): formato, cabecera, destinatarios y suma sha256 junto al archivo."""
    archivo = Path(archivo)
    datos = archivo.read_bytes()
    if not datos.startswith(MAGIC):
        raise ErrorNegocio("El archivo no es un respaldo cifrado válido.", 400)
    (n,) = struct.unpack(">I", datos[len(MAGIC):len(MAGIC) + 4])
    cab = json.loads(datos[len(MAGIC) + 4:len(MAGIC) + 4 + n])
    suma = archivo.with_name(archivo.name + ".sha256")
    if suma.is_file() and suma.read_text(encoding="utf-8").split()[0] != hashlib.sha256(datos).hexdigest():
        raise ErrorNegocio("La suma SHA-256 del respaldo cifrado no coincide.", 400)
    return {"archivo": archivo.name, "bytes": len(datos), "creado": cab["creado"], "destinatarios": [s["kid"] for s in cab["slots"]],
            "sha256": hashlib.sha256(datos).hexdigest()}


def verificar_restauracion(archivo: Path, pem_privada: bytes, contrasena: str) -> dict:
    """Prueba de restauracion COMPLETA en un directorio temporal (exige la clave privada): descifra, valida hashes, abre la BD restaurada,
    comprueba integridad y que todos los documentos coincidan con sus hashes. No toca ningun estado real."""
    tmp = Path(tempfile.mkdtemp(prefix="cxcverif-"))
    try:
        bd = extraer(archivo, pem_privada, contrasena, tmp / "extraido")
        Estado.restaurar(bd, tmp / "estado" / "portal.sqlite3")
        est = Estado(tmp / "estado" / "portal.sqlite3")
        problemas = est.verificar_documentos()
        with est.tx() as c:
            integridad = c.execute("PRAGMA integrity_check").fetchone()[0]
            cuenta = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("usuarios", "rutas", "clientes_ruta", "decisiones", "documentos")}
        if integridad != "ok" or problemas:
            raise ErrorNegocio(f"Restauración de prueba con problemas: integridad={integridad}; {problemas}", 500)
        return {"ok": True, "integridad": integridad, "tablas": cuenta, "documentos_verificados": cuenta["documentos"]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def restaurar_cifrado(archivo: Path, pem_privada: bytes, contrasena: str, destino_bd: Path, forzar: bool = False) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="cxcrestore-"))
    try:
        bd = extraer(archivo, pem_privada, contrasena, tmp)
        return Estado.restaurar(bd, destino_bd, forzar)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ejecutar_ciclo(estado: Estado, publicas: Iterable[bytes], destino_dir: Path, diarios: int = 14, mensuales: int = 6) -> dict:
    """Lo que ejecutara la tarea programada: respaldo cifrado + verificacion estructural + rotacion + estado legible por monitores."""
    f = crear_respaldo(estado, publicas, destino_dir)
    info = verificar_estructura(f)
    borrados = rotar(destino_dir, diarios, mensuales)
    estado_json = {**info, "rotados": borrados, "politica": {"diarios": diarios, "mensuales": mensuales}}
    tmp = Path(destino_dir) / "ultimo_respaldo.json.parcial"
    tmp.write_text(json.dumps(estado_json, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, Path(destino_dir) / "ultimo_respaldo.json")
    return estado_json
