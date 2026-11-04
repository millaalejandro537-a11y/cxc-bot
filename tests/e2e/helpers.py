from playwright.sync_api import Page, expect

from tests.portal_helpers import CLAVE_ADMIN, CLAVE_GER, CLAVE_VEN, CLAVE_VEN2

USUARIOS = {"admin1": CLAVE_ADMIN, "ger1": CLAVE_GER, "ven1": CLAVE_VEN, "ven2": CLAVE_VEN2}


def login(page: Page, base: str, usuario: str, clave: str | None = None, codigo: str | None = None) -> None:
    page.goto(base)
    page.get_by_label("Usuario").fill(usuario)
    page.get_by_label("Contraseña").fill(clave or USUARIOS[usuario])
    page.get_by_role("button", name="Entrar").click()
    if codigo is not None:
        page.get_by_label("Código de verificación").fill(codigo)
        page.get_by_role("button", name="Verificar").click()
    expect(page.get_by_role("button", name="Salir")).to_be_visible()


def ir(page: Page, pestana: str) -> None:
    page.get_by_role("button", name=pestana, exact=True).click()


def pestanas(page: Page) -> list:
    return [b.inner_text().strip() for b in page.locator("header button, nav button").all() if b.inner_text().strip() not in ("Salir",)]
