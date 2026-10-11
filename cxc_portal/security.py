"""Seguridad del portal: contraseñas (scrypt, stdlib), tokens de sesion, CSRF, limite de intentos y politica de claves.

Sin dependencias externas. Nada de secretos en el repositorio: la base de usuarios vive en el directorio de ESTADO privado.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from typing import Dict, Optional, Tuple

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
MIN_PASSWORD = 12
SESION_IDLE_SEG = 30 * 60
SESION_MAX_SEG = 8 * 3600
MAX_INTENTOS = 5
BLOQUEO_SEG = 15 * 60

ROLES = ("admin", "gerencia", "vendedor")


class PasswordInvalida(ValueError):
    pass


def validar_password(password: str, usuario: str = "") -> None:
    if len(password) < MIN_PASSWORD:
        raise PasswordInvalida(f"La contraseña debe tener al menos {MIN_PASSWORD} caracteres.")
    if usuario and usuario.lower() in password.lower():
        raise PasswordInvalida("La contraseña no puede contener el nombre de usuario.")
    clases = sum(bool(any(f(c) for c in password)) for f in (str.islower, str.isupper, str.isdigit, lambda c: not c.isalnum()))
    if clases < 3:
        raise PasswordInvalida("Use al menos 3 de: minúsculas, mayúsculas, dígitos, símbolos.")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return "scrypt$%d$%d$%d$%s$%s" % (SCRYPT_N, SCRYPT_R, SCRYPT_P, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verificar_password(password: str, almacenado: str) -> bool:
    try:
        esquema, n, r, p, salt_b64, dk_b64 = almacenado.split("$")
        if esquema != "scrypt":
            return False
        salt, esperado = base64.b64decode(salt_b64), base64.b64decode(dk_b64)
        dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(esperado))
        return hmac.compare_digest(dk, esperado)
    except Exception:  # noqa: BLE001 - un hash malformado nunca autentica
        return False


# Hash de relleno para igualar tiempos cuando el usuario no existe (evita enumerar usuarios por tiempo de respuesta).
_DUMMY = hash_password("relleno-" + secrets.token_hex(8) + "Aa1!")


def verificar_inexistente(password: str) -> None:
    verificar_password(password, _DUMMY)


def nuevo_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def password_aleatoria() -> str:
    """Clave inicial aleatoria que cumple la politica (para altas administrativas; el usuario debe cambiarla)."""
    base = secrets.token_urlsafe(14)
    return base + "aA1!"


class LimitadorIntentos:
    """Limite en memoria por clave (usuario+IP): bloquea tras MAX_INTENTOS fallos durante BLOQUEO_SEG."""

    def __init__(self, maximo: int = MAX_INTENTOS, bloqueo_seg: int = BLOQUEO_SEG, reloj=time.time):
        self.maximo, self.bloqueo, self.reloj = maximo, bloqueo_seg, reloj
        self._fallos: Dict[str, Tuple[int, float]] = {}

    def bloqueado(self, clave: str) -> Optional[int]:
        n, hasta = self._fallos.get(clave, (0, 0.0))
        if n >= self.maximo and self.reloj() < hasta:
            return int(hasta - self.reloj()) + 1
        if n >= self.maximo and self.reloj() >= hasta:
            self._fallos.pop(clave, None)
        return None

    def fallo(self, clave: str) -> None:
        n, _ = self._fallos.get(clave, (0, 0.0))
        self._fallos[clave] = (n + 1, self.reloj() + self.bloqueo)

    def exito(self, clave: str) -> None:
        self._fallos.pop(clave, None)


# ----------------------------------------------------------------------------- segundo factor (TOTP, RFC 6238; compatible con Google/Microsoft Authenticator, Authy, 1Password)
import struct  # noqa: E402

TOTP_PERIODO = 30
TOTP_DIGITOS = 6
TOTP_VENTANA = 1          # tolera ±1 intervalo (reloj desfasado)
CODIGOS_RECUPERACION = 8


def totp_nuevo_secreto() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secreto: str, contador: int) -> str:
    clave = base64.b32decode(secreto + "=" * (-len(secreto) % 8), casefold=True)
    h = hmac.new(clave, struct.pack(">Q", contador), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    n = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** TOTP_DIGITOS)
    return str(n).zfill(TOTP_DIGITOS)


def totp_codigo(secreto: str, instante: float) -> str:
    return _hotp(secreto, int(instante // TOTP_PERIODO))


def totp_verificar(secreto: str, codigo: str, instante: float, ultimo_paso: int = 0) -> Optional[int]:
    """Devuelve el paso (intervalo) aceptado o None. `ultimo_paso` impide REUTILIZAR un codigo ya usado (anti-repeticion)."""
    codigo = "".join(ch for ch in str(codigo or "") if ch.isdigit())
    if len(codigo) != TOTP_DIGITOS:
        return None
    actual = int(instante // TOTP_PERIODO)
    for paso in range(actual - TOTP_VENTANA, actual + TOTP_VENTANA + 1):
        if paso > ultimo_paso and hmac.compare_digest(_hotp(secreto, paso), codigo):
            return paso
    return None


def totp_uri(secreto: str, usuario: str, emisor: str = "Portal CxC Cosprobell") -> str:
    from urllib.parse import quote
    return f"otpauth://totp/{quote(emisor)}:{quote(usuario)}?secret={secreto}&issuer={quote(emisor)}&digits={TOTP_DIGITOS}&period={TOTP_PERIODO}"


def codigos_recuperacion_nuevos() -> list:
    return [secrets.token_hex(4) + "-" + secrets.token_hex(4) for _ in range(CODIGOS_RECUPERACION)]


def hash_recuperacion(codigo: str) -> str:
    return hashlib.sha256(codigo.strip().lower().encode("utf-8")).hexdigest()
