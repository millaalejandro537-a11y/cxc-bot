"""Normalizacion de texto compartida por catalog/config/file_resolver.

Los nombres de vendedores y rutas llegan de fuentes distintas (el catalogo
oficial con acentos, config/routes.json en mayusculas sin acentos, carpetas
reales en disco con quien sabe que capitalizacion). Para no tener que
confiar en coincidencias de texto exactas, todo se compara normalizado:
mayusculas, sin acentos, espacios de sobra colapsados.
"""

from __future__ import annotations

import unicodedata


def normalize_key(text: str) -> str:
    if text is None:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = " ".join(text.upper().split())
    return text
