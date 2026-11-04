"""Los textos que ve el usuario (API de reglas, avisos, interfaz) deben coincidir con las reglas VIGENTES del motor, no con las superadas:
contado = ROJO automatico (sin aprobacion adicional de gerencia) y el sistema prevalece sobre decisiones anteriores."""

from pathlib import Path

from cxc_bot import payment_semaphore as ps
from tests.portal_helpers import CLAVE_GER, Servidor, cliente_con_login, crear_entorno


def test_la_api_de_reglas_refleja_el_motor(tmp_path):
    ctx = crear_entorno(tmp_path)
    s = Servidor(ctx)
    try:
        r = cliente_con_login(s, "ger1", CLAVE_GER).get("/api/reglas")[2]
        assert r["vigentes"] == {"CONTADO_AUTOMATICO": ps.CONTADO_AUTOMATICO, "SISTEMA_PREVALECE": ps.SISTEMA_PREVALECE, "ANTICIPOS_RESPALDAN_PAGO": ps.ANTICIPOS_RESPALDAN_PAGO}
        assert ps.SOLO_CONTADO in r["umbrales_media_mora"] and ps.CANDIDATO_SOLO_CONTADO not in r["umbrales_media_mora"]      # contado ya no es "candidato"
        texto = " ".join(r["notas"])
        assert "la decide gerencia" not in texto and "sin aprobación adicional" in texto and "El sistema prevalece" in texto
        aviso = cliente_con_login(s, "ger1", CLAVE_GER).get("/api/avisos")[2]["nota"]
        assert "siempre requieren" not in aviso and "ROJO automático" in aviso
    finally:
        s.cerrar()


def test_la_interfaz_no_dice_que_el_contado_requiere_decision_de_gerencia():
    for f in Path("portal-web/src").glob("*.jsx"):
        assert "candidatos a solo contado siempre requieren" not in f.read_text(encoding="utf-8").lower(), f.name
