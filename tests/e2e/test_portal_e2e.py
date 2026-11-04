"""E2E del Portal CxC en Chrome (Playwright): autenticacion/2FA, permisos, rutas, calendario, salidas, hojas de visita (con paginacion REAL a PDF),
impresion SIMULADA, avisos y barrido de pantallas en escritorio y movil sin errores de JavaScript ni desbordes. Nada real: loopback, estado temporal."""

import re
import time

from playwright.sync_api import expect

from cxc_portal import security as sec
from cxc_portal.agente_impresion import AgenteImpresion, ImpresoraSimulada, TransporteHttp
from tests.e2e.helpers import USUARIOS, ir, login, pestanas
from tests.portal_helpers import CLAVE_ADMIN, Servidor, cliente_con_login

TODAS_GERENCIA = ["Panel", "Alertas", "Calendario", "Salidas", "Avisos", "Dashboard", "Visitas", "Hojas de visita", "Impresión", "Reportes", "Auditoría", "Seguridad"]


# ------------------------------------------------------------------------------------------------ autenticacion y 2FA
def test_credenciales_incorrectas_no_entran_y_el_logout_cierra_la_sesion(abrir, portal):
    p = abrir()
    p.goto(portal.base)
    p.get_by_label("Usuario").fill("ger1")
    p.get_by_label("Contraseña").fill("incorrecta-12345")
    p.get_by_role("button", name="Entrar").click()
    expect(p.locator(".error")).to_be_visible()
    expect(p.get_by_role("button", name="Salir")).to_have_count(0)
    login(p, portal.base, "ger1")
    expect(p.get_by_role("heading", name="Ruta DEMO (sintética)")).to_be_visible()
    p.get_by_role("button", name="Salir").click()
    expect(p.get_by_label("Usuario")).to_be_visible()
    assert p.request.get(portal.base + "/api/rutas").status == 401                # la sesion realmente se cerro


def test_segundo_factor_se_activa_desde_la_interfaz_y_se_exige_al_volver_a_entrar(abrir, portal):
    p = abrir()
    login(p, portal.base, "ger1")
    ir(p, "Seguridad")
    p.get_by_role("button", name="Comenzar").click()
    secreto = p.locator("code.clave").inner_text().strip()
    p.get_by_label(re.compile("código de 6 dígitos", re.I)).or_(p.locator("input[autocomplete=one-time-code]")).first.fill(sec.totp_codigo(secreto, portal.reloj["t"]))
    p.get_by_role("button", name="Activar").click()
    expect(p.get_by_text("Códigos de recuperación")).to_be_visible()
    assert len(p.locator("pre.rec").inner_text().split()) >= 8                          # se entregan los codigos de recuperacion
    p.get_by_role("button", name="Ya los guardé").click()
    p.get_by_role("button", name="Salir").click()
    # segundo inicio: contraseña correcta NO basta; codigo malo se rechaza; codigo bueno (otro intervalo, sin reutilizar) entra
    p.get_by_label("Usuario").fill("ger1")
    p.get_by_label("Contraseña").fill(USUARIOS["ger1"])
    p.get_by_role("button", name="Entrar").click()
    expect(p.get_by_label("Código de verificación")).to_be_visible()
    expect(p.get_by_role("button", name="Salir")).to_have_count(0)
    p.get_by_label("Código de verificación").fill("000000")
    p.get_by_role("button", name="Verificar").click()
    expect(p.locator(".error")).to_be_visible()
    portal.reloj["t"] += 90
    p.get_by_label("Código de verificación").fill(sec.totp_codigo(secreto, portal.reloj["t"]))
    p.get_by_role("button", name="Verificar").click()
    expect(p.get_by_role("button", name="Salir")).to_be_visible()


# ------------------------------------------------------------------------------------------------ permisos
def test_el_vendedor_solo_ve_su_ruta_y_nada_global(abrir, portal):
    p = abrir()
    login(p, portal.base, "ven1")
    nombres = pestanas(p)
    assert not {"Auditoría", "Reportes"} & set(nombres), nombres                       # el vendedor tiene su propio Dashboard (/dashboard/mio), no el global
    expect(p.get_by_role("heading", name="Ruta DEMO (sintética)")).to_be_visible()
    expect(p.get_by_role("heading", name="Ruta de Otro")).to_have_count(0)
    for ruta in ("/api/admin/auditoria", "/api/dashboard", "/api/admin/usuarios", "/api/configuracion"):
        assert p.request.get(portal.base + ruta).status == 403, ruta
    assert p.request.get(portal.base + "/api/rutas/OTRA/clientes").status == 404      # la ruta ajena ni se revela


def test_otro_vendedor_no_ve_la_ruta_ni_los_documentos_ajenos(abrir, portal):
    p = abrir()
    login(p, portal.base, "ven2")
    expect(p.get_by_role("heading", name="Ruta de Otro")).to_be_visible()
    expect(p.get_by_role("heading", name="Ruta DEMO (sintética)")).to_have_count(0)
    did = portal.ctx.estado.listar_documentos("DEMO")[0]["id"]
    assert p.request.get(f"{portal.base}/api/documentos/{did}/descargar").status in (403, 404)


# ------------------------------------------------------------------------------------------------ rutas, clientes y viñetas provisionales
def test_ruta_con_clientes_semaforo_y_estado_provisional_de_la_vineta(abrir, portal):
    p = abrir()
    login(p, portal.base, "ger1")
    p.locator(".card", has=p.get_by_role("heading", name="Ruta DEMO (sintética)")).get_by_role("button", name="Ver ruta").click()
    expect(p.get_by_text("C90001").first).to_be_visible()
    filas = p.locator("table tbody tr")
    assert filas.count() >= 9
    expect(p.locator("table").get_by_text("Solo contado").first).to_be_visible()                          # contado: ROJO automatico, sin decision de gerencia
    expect(p.get_by_text(re.compile("provisional: fuente no validada")).first).to_be_visible()   # fuente sintetica: ningun color definitivo
    expect(p.get_by_text("pendiente de gerencia").first).to_be_visible()                 # revision humana: sin color de credito


# ------------------------------------------------------------------------------------------------ calendario, salidas, avisos
def test_calendario_salidas_revision_y_avisos(abrir, portal):
    p = abrir()
    login(p, portal.base, "ger1")
    ir(p, "Calendario")
    expect(p.get_by_text("Hoy: 06/11/2026")).to_be_visible()
    p.locator("form", has=p.locator("input[placeholder^='Fechas']")).locator("select").select_option("DEMO")
    p.locator("input[placeholder^='Fechas']").fill("2026-11-10, 2026-11-15")           # martes y domingo
    p.get_by_role("button", name="Programar").click()
    expect(p.get_by_text(re.compile(r"1 creada\(s\).*1 rechazada\(s\)"))).to_be_visible()   # el domingo no es habil
    expect(p.locator("table tbody tr").filter(has_text="10/11/2026")).to_have_count(1)
    p.get_by_role("button", name="Ejecutar ciclo de preparación").click()
    expect(p.get_by_text(re.compile(r"Ciclo ejecutado: 1 preparada\(s\)"))).to_be_visible()
    # salida: excepciones y revision de gerencia
    ir(p, "Salidas")
    p.get_by_label("Ruta").select_option("DEMO")
    p.locator("tbody tr").filter(has_text="10/11/2026").first.click()
    expect(p.get_by_role("heading", name="Salida del 10/11/2026")).to_be_visible()
    expect(p.get_by_text(re.compile("requiere revisión", re.I)).first).to_be_visible()
    p.get_by_placeholder(re.compile("Nota de revisión")).fill("Revisión de gerencia de la prueba E2E sintética.")
    p.get_by_role("button", name="Registrar revisión").click()
    expect(p.get_by_text(re.compile("Revisada por ger1"))).to_be_visible()
    p.get_by_role("button", name=re.compile("Marcar en curso", re.I)).click()
    expect(p.get_by_role("heading", name="Salida del 10/11/2026")).to_contain_text("curso")
    # avisos: informan, se marcan vistos/cerrados y no cambian clasificaciones
    ir(p, "Avisos")
    filas = p.locator("table").first.locator("tbody tr")
    expect(filas.first).not_to_contain_text("Sin avisos")                                # espera a que carguen
    assert filas.count() >= 2                                                           # revision de gerencia + candidato pendiente
    n = filas.count()
    p.get_by_role("button", name="Visto").first.click()
    expect(filas).to_have_count(n - 1)                                                  # salio de "Nuevos"
    p.get_by_label("Estado").select_option("vista")
    expect(p.locator("table").first.locator("tbody tr").first).to_contain_text("vista")
    expect(p.get_by_text("Notificaciones (simuladas)")).to_be_visible()


# ------------------------------------------------------------------------------------------------ hojas de visita: vista de impresion y PDF real
def _pdf_info(pdf: bytes):
    paginas = len(re.findall(rb"/Type\s*/Page[^s]", pdf))
    caja = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", pdf)
    return paginas, (float(caja.group(1)), float(caja.group(2))) if caja else None


def test_hojas_de_visita_ver_color_y_paginar_a_pdf_una_pagina_por_hoja(abrir, portal):
    p = abrir()
    login(p, portal.base, "ger1")
    ir(p, "Hojas de visita")
    p.get_by_label("Ruta").select_option("OCC119")
    expect(p.get_by_text("v1").first).to_be_visible()
    p.get_by_role("button", name="Ver / imprimir").click()
    secciones = p.locator(".print-root section.hoja")
    expect(secciones).to_have_count(8)                                                # 119 clientes = 7 hojas de 16 + 1 de 7
    assert [secciones.nth(i).locator("tbody tr").count() for i in range(8)] == [16] * 7 + [7]
    # colores: ROJO con fondo rojo y texto blanco (contraste), como en la prueba estatica
    celda = p.locator(".print-root td", has_text=re.compile(r"^ROJO$")).first
    fondo, texto = celda.evaluate("e => [getComputedStyle(e).backgroundColor, getComputedStyle(e).color]")
    assert fondo == "rgb(220, 38, 38)" and texto == "rgb(255, 255, 255)"
    # impresion: en medio 'print' se ocultan los controles y cada hoja es una pagina carta horizontal
    p.emulate_media(media="print")
    expect(p.locator(".no-print").first).to_be_hidden()
    pdf = p.pdf(prefer_css_page_size=True, print_background=True)
    paginas, caja = _pdf_info(pdf)
    assert paginas == 8, f"se esperaban 8 paginas (una por hoja) y salieron {paginas}"
    assert caja == (792.0, 612.0), caja                                               # carta horizontal
    p.emulate_media(media="screen")
    p.get_by_role("button", name="Cerrar").click()
    expect(p.get_by_text("Documentos y hojas de visita")).to_be_visible()


def test_descarga_de_la_hoja_desde_la_interfaz_es_el_libro_original(abrir, portal):
    p = abrir()
    login(p, portal.base, "ger1")
    ir(p, "Hojas de visita")
    p.get_by_label("Ruta").select_option("DEMO")
    expect(p.get_by_text("v1").first).to_be_visible()
    with p.expect_download() as d:
        p.get_by_role("link", name="Descargar Excel").first.click()
    ruta = d.value.path()
    import hashlib
    doc = portal.ctx.estado.listar_documentos("DEMO")[0]
    assert hashlib.sha256(open(ruta, "rb").read()).hexdigest() == doc["sha256"]


# ------------------------------------------------------------------------------------------------ impresion SIMULADA
def test_cola_de_impresion_simulada_pendiente_cancelar_e_impreso_por_el_agente_simulado(abrir, portal):
    p = abrir()
    login(p, portal.base, "ger1")
    ir(p, "Hojas de visita")
    p.get_by_label("Ruta").select_option("DEMO")
    expect(p.get_by_text("v1").first).to_be_visible()
    p.get_by_role("button", name="A cola de impresión").first.click()
    expect(p.get_by_text(re.compile("Solicitud registrada en la cola"))).to_be_visible()
    ir(p, "Impresión")
    fila = p.locator("tbody tr").filter(has_text="HOJA VISITA").first
    expect(fila).to_contain_text("Pendiente")
    fila.get_by_role("button", name="Cancelar").click()
    expect(p.locator("tbody tr").filter(has_text="HOJA VISITA").first).to_contain_text("Cancelado")
    # nueva solicitud + agente SIMULADO (ninguna impresora real): termina como 'Impreso' y la interfaz lo refleja
    ir(p, "Hojas de visita")
    p.get_by_label("Ruta").select_option("DEMO")
    p.get_by_role("button", name="A cola de impresión").first.click()
    expect(p.get_by_text(re.compile("Solicitud registrada en la cola"))).to_be_visible()
    srv = type("S", (), {"puerto": portal.puerto})()
    token = cliente_con_login(srv, "admin1", CLAVE_ADMIN).post("/api/admin/agentes-impresion", {"nombre": "Agente E2E simulado"})[2]["token"]
    imp = ImpresoraSimulada()
    res = AgenteImpresion(TransporteHttp("127.0.0.1", portal.puerto, token), imp).vaciar_cola()
    assert [r["estado"] for r in res] == ["impreso"] and len(imp.trabajos) == 1
    ir(p, "Panel")
    ir(p, "Impresión")
    expect(p.locator("tbody tr").filter(has_text="Impreso")).to_have_count(1)


# ------------------------------------------------------------------------------------------------ barrido de pantallas: escritorio y movil
def _sin_desborde(p, etiqueta):
    anchos = p.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert anchos[0] <= anchos[1] + 1, f"{etiqueta}: desborde horizontal de la pagina ({anchos[0]} > {anchos[1]})"


def test_todas_las_pantallas_cargan_sin_errores_ni_desbordes_en_escritorio_y_movil(abrir, portal):
    for modo in ("escritorio", "movil"):
        p = abrir(modo)
        login(p, portal.base, "ger1")
        assert pestanas(p) == TODAS_GERENCIA or set(TODAS_GERENCIA) <= set(pestanas(p)), pestanas(p)
        for nombre in TODAS_GERENCIA:
            ir(p, nombre)
            p.wait_for_load_state("networkidle")
            expect(p.locator("main, .app").first).to_be_visible()
            _sin_desborde(p, f"{modo}/{nombre}")
            assert not p.locator(".error").count() or nombre in ("Reportes",), f"{modo}/{nombre}: {p.locator('.error').first.inner_text()}"   # Reportes: reporting-core opcional
        p.get_by_role("button", name="Salir").click()


def test_el_vendedor_recorre_sus_pantallas_sin_errores_en_escritorio_y_movil(abrir, portal):
    for modo in ("escritorio", "movil"):
        p = abrir(modo)
        login(p, portal.base, "ven1")
        nombres = pestanas(p)
        assert nombres and "Auditoría" not in nombres and "Reportes" not in nombres
        for nombre in nombres:
            ir(p, nombre)
            p.wait_for_load_state("networkidle")
            _sin_desborde(p, f"vendedor/{modo}/{nombre}")
            assert p.locator(".error").count() == 0, f"vendedor/{modo}/{nombre}: {p.locator('.error').first.inner_text()}"
        p.get_by_role("button", name="Salir").click()
