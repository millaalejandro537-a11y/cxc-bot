"""PRUEBA COMPLETA de una salida simulada, de la programacion a los documentos, evaluaciones, historial y alertas, por HTTP real con sesiones
autenticadas. Datos 100 % sinteticos: ningun saldo ni cliente es real. Ejecutar con `-s` para ver el informe paso a paso."""

import hashlib
import shutil
from datetime import date
from pathlib import Path

import pytest

from cxc_portal.agente_impresion import AgenteImpresion, ImpresoraSimulada, TransporteHttp
from cxc_portal.estado import Estado
from cxc_portal.flujo import FlujoOperativo
from tests.portal_helpers import ACTOR, CLAVE_ADMIN, CLAVE_GER, CLAVE_VEN, Servidor, cliente_con_login, crear_entorno

FIXTURE = Path("tests/fixtures/rutas_reales/LAS VEGAS MAYO.xlsx")
HOJA = b"PK-hoja-de-visita-sintetica-" + bytes(range(150))
PASOS = []


def paso(texto):
    PASOS.append(texto)
    print(f"  OK  {texto}".encode("ascii", "replace").decode())


@pytest.fixture()
def env(tmp_path):
    ctx = crear_entorno(tmp_path)
    ctx.estado.guardar_ruta(ACTOR, "LAS_VEGAS", "Las Vegas", "Vendedor Demo", "FORANEA", None, zonas={"LV": "Las Vegas", "PB": "Peña Blanca"},
                            clientes=[{"codigo": "C90001", "folder": "1 LV", "nombre": "SALON DEMO EXCELENTE (A)"},
                                      {"codigo": "C90002", "folder": "2 LV", "nombre": "SALON DEMO BUENO (B)"},
                                      {"codigo": "C90003", "folder": "1 PB", "nombre": "SALON DEMO REGULAR (C)"},
                                      {"codigo": "C90004", "folder": "2 PB", "nombre": "SALON DEMO CANDIDATO A SOLO CONTADO"}], requiere_hojas_visita=True)
    ctx.estado.registrar_documento(ACTOR, "LAS_VEGAS", "HOJA_VISITA", "hojas.xlsx", HOJA, {"hojas": [], "total_clientes": 4}, tmp_path / "estado" / "documentos")
    (tmp_path / "datos" / "01_Rutas").mkdir(parents=True)
    shutil.copy2(FIXTURE, tmp_path / "datos" / "01_Rutas" / "base.xlsx")
    hoy = {"d": date(2026, 10, 12)}
    ctx.flujo = FlujoOperativo(ctx.servicio, hoy=lambda: hoy["d"])
    s = Servidor(ctx)
    yield s, ctx, tmp_path, hoy
    s.cerrar()


def test_salida_completa_simulada_de_extremo_a_extremo(env):
    s, ctx, tmp, hoy = env
    print("\nPRUEBA COMPLETA DE SALIDA SIMULADA — ruta LAS_VEGAS (sintética), vendedor 'Vendedor Demo'")
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    ven = cliente_con_login(s, "ven1", CLAVE_VEN)

    # 1. programacion con calendario laboral y feriado
    assert adm.post("/api/admin/feriados", {"fecha": "2026-10-30", "nombre": "Feriado de prueba"})[0] == 201
    st, _, r = ger.post("/api/salidas/serie", {"ruta": "LAS_VEGAS", "fechas": ["2026-10-20", "2026-10-25", "2026-10-27", "2026-10-30"]})
    assert st == 201 and [c["fecha_salida"] for c in r["creadas"]] == ["2026-10-20", "2026-10-27"]
    assert {x["fecha"] for x in r["rechazadas"]} == {"2026-10-25", "2026-10-30"}
    paso("Serie programada: 20/10 y 27/10 creadas; 25/10 (domingo) y 30/10 (feriado) rechazadas")
    cal = ger.get("/api/calendario?ruta=LAS_VEGAS")[2]["salidas"]
    prep = {c["fecha_salida"]: c["fecha_preparacion"] for c in cal}
    assert prep == {"2026-10-20": "2026-10-16", "2026-10-27": "2026-10-23"}              # 3 dias habiles antes (lunes a sabado)
    paso(f"Preparación calculada 3 días hábiles antes (lun–sáb): {prep}")
    sid1, sid2 = [c["id"] for c in cal]
    assert ctx.estado.documentos_de_salida(sid1)[0]["version"] == 1
    paso("Cada salida quedó vinculada a la hoja de visita v1 vigente (documento reutilizable)")

    # 2. ciclo automatico idempotente
    hoy["d"] = date(2026, 10, 15)
    assert ger.post("/api/salidas/ciclo", {})[2]["preparadas"] == []
    hoy["d"] = date(2026, 10, 16)
    r = ger.post("/api/salidas/ciclo", {})[2]
    assert [p["salida"] for p in r["preparadas"]] == [sid1]
    r2 = ger.post("/api/salidas/ciclo", {})[2]
    assert r2["preparadas"] == [] and r2["ya_preparadas"] == 1
    n_av, n_no = len(ven.get("/api/avisos")[2]["avisos"]), len(ven.get("/api/notificaciones")[2]["notificaciones"])
    ger.post("/api/salidas/ciclo", {})
    assert len(ven.get("/api/avisos")[2]["avisos"]) == n_av and len(ven.get("/api/notificaciones")[2]["notificaciones"]) == n_no
    paso(f"Ciclo del 16/10 prepara la salida 1; repetirlo no duplica nada ({n_av} aviso(s), {n_no} notificación(es) para el vendedor)")

    # 3. excepciones, avisos y candidatos: nada se decide solo
    sal = ger.get(f"/api/salidas/{sid1}")[2]["salida"]
    rep = sal["reporte_excepciones"]
    assert sal["estado"] == "preparada" and rep["total_excepciones"] > 0 and rep["pendientes_gerencia"]
    assert ctx.estado.decisiones_vigentes() == {}
    tipos = {a["tipo"] for a in ger.get(f"/api/avisos?salida={sid1}")[2]["avisos"]}
    assert {"CANDIDATO_PENDIENTE_GERENCIA", "DATOS_NO_VIVOS"} <= tipos
    paso(f"Evaluación persistida: {rep['total_excepciones']} excepción(es); avisos {sorted(tipos)}; 0 decisiones automáticas")

    # 4. el Excel no se publica sin revision humana ni con clientes sin confirmar
    assert ger.post(f"/api/salidas/{sid1}/excel", {"base": "base.xlsx"})[0] == 409
    paso("Excel bloqueado hasta revisar las excepciones")
    assert ger.post(f"/api/salidas/{sid1}/revisar", {"nota": "Excepciones revisadas por gerencia (prueba)."})[0] == 200
    st, _, e1 = ger.post(f"/api/salidas/{sid1}/excel", {"base": "base.xlsx"})
    assert st == 201 and e1["version"] == 1 and e1["estado"] == "ACTUAL" and e1["borrador"] == 1
    assert ger.post(f"/api/salidas/{sid1}/excel", {"base": "base.xlsx"})[0] == 200          # repetir no crea otra version
    paso("Excel v1 publicado en Actual (BORRADOR: fuente snapshot); repetir es idempotente")
    st, cab, cuerpo = ven.get(f"/api/excel/{e1['id']}/descargar")
    assert st == 200 and hashlib.sha256(cuerpo).hexdigest() == e1["sha256"]
    paso("El vendedor de la ruta descarga el Excel y su hash coincide")

    # 5. impresion preparada y agente simulado
    st, _, r = ger.post(f"/api/salidas/{sid1}/impresion", {})
    assert st == 200 and len(r["creadas"]) == 1 and r["omitidas"][-1]["tipo"] == "EXCEL_RUTA"
    token = adm.post("/api/admin/agentes-impresion", {"nombre": "Agente simulado"})[2]["token"]
    imp = ImpresoraSimulada()
    res = AgenteImpresion(TransporteHttp("127.0.0.1", s.puerto, token), imp).vaciar_cola()
    assert [x["estado"] for x in res] == ["impreso"] and imp.trabajos[0]["sha256"] == hashlib.sha256(HOJA).hexdigest()
    paso("Impresión: solicitud de la hoja v1 creada (Excel borrador omitido) y 'impresa' por el agente SIMULADO (nada se imprimió)")

    # 6. ejecucion de la salida: visitas, cobros y ventas
    ger.post(f"/api/salidas/{sid1}/estado", {"estado": "en_curso"})
    for cod, est, extra in (("C90001", "visitada_firmada", {"cobro": 2500, "venta": 4100}), ("C90002", "visitada_firmada", {"cobro": 800, "venta": 0}),
                            ("C90003", "visitada_sin_firma", {"motivo": "Sin lapicero", "cobro": 300}), ("C90004", "no_visitada", {"motivo": "Local cerrado"})):
        assert ven.post("/api/visitas", {"ruta": "LAS_VEGAS", "fecha_salida": "2026-10-20", "codigo": cod, "estado": est, **extra})[0] == 201
    seg = ger.get(f"/api/salidas/{sid1}")[2]["seguimiento"]
    assert seg["totales"]["visitados"] == 3 and seg["totales"]["cobro_declarado"] == 3600 and seg["totales"]["venta_declarada"] == 4100
    assert ven.post("/api/visitas", {"ruta": "LAS_VEGAS", "fecha_salida": "2026-10-20", "codigo": "C90004", "estado": "no_visitada", "motivo": "x", "cobro": 5})[0] == 400
    paso("Seguimiento: 3/4 visitados, cobro declarado L 3,600, venta declarada L 4,100; sin firma ≠ no visita; no visita con cobro rechazada")
    ger.post(f"/api/salidas/{sid1}/estado", {"estado": "cerrada"})

    # 7. gerencia decide (explicitamente) y se confirma un cliente nuevo
    st, _, d = ger.post("/api/decisiones", {"codigo": "C90004", "clasificacion_final": "SOLO_CONTADO", "motivo": "Decisión de gerencia sintética tras la salida 1."})
    assert st == 201 and d["actor"]
    assert ctx.estado.agregar_cliente(ACTOR, "LAS_VEGAS", "C90005", "SALON DEMO CANDIDATO CON ALERTA PAGADA", "LV")["folder"] == "3 LV"
    paso("Gerencia decide C90004 → SOLO_CONTADO (con motivo y autor); se confirma el cliente nuevo C90005 con folder permanente 3 LV")

    # 8. segunda salida: cambios, alertas, comparacion y Excel v2 / historico
    hoy["d"] = date(2026, 10, 23)
    r = ger.post("/api/salidas/ciclo", {})[2]
    assert [p["salida"] for p in r["preparadas"]] == [sid2]
    av2 = ger.get(f"/api/avisos?salida={sid2}")[2]["avisos"]
    tipos2 = {a["tipo"] for a in av2}
    assert "CAMBIO_CATEGORIA" in tipos2 and "CLIENTE_NUEVO" in tipos2
    cmp = ger.get(f"/api/comparar-salidas?a={sid1}&b={sid2}")[2]
    camb = {c["codigo"]: c for c in cmp["cambios"]}
    assert camb["C90004"]["antes"] == "CANDIDATO_SOLO_CONTADO" and camb["C90004"]["ahora"] == "SOLO_CONTADO" and camb["C90005"]["tipo"] == "NUEVO"
    paso(f"Salida 2 preparada el 23/10: avisos {sorted(tipos2)}; comparación con la salida 1: {len(cmp['cambios'])} cambio(s)")
    ger.post(f"/api/salidas/{sid2}/revisar", {"nota": "Revisión de la segunda salida (prueba)."})
    st, _, e2 = ger.post(f"/api/salidas/{sid2}/excel", {"base": "base.xlsx"})
    assert st == 201 and e2["version"] == 2 and e2["clientes"] == 5
    vers = {v["version"]: v for v in ger.get("/api/rutas/LAS_VEGAS/excel")[2]["versiones"]}
    assert vers[1]["estado"] == "HISTORICO" and vers[2]["estado"] == "ACTUAL"
    assert hashlib.sha256((ctx.estado.carpeta_excel / vers[1]["archivo"]).read_bytes()).hexdigest() == e1["sha256"]
    assert ger.get("/api/rutas/LAS_VEGAS/excel")[2]["integridad"] == []
    paso("Excel v2 (5 clientes) en Actual; la v1 pasó a Histórico con contenido idéntico (hash verificado)")

    # 9. dashboard, notificaciones simuladas y permisos
    d = ger.get("/api/dashboard")[2]
    fila = next(x for x in d["por_ruta"] if x["ruta"] == "LAS_VEGAS")
    assert fila["clientes"] == 5 and d["alertas"]["abiertas"] > 0 and any(c["salida"] == sid1 and c["visitados"] == 3 for c in d["cumplimiento"])
    assert ven.get("/api/dashboard")[0] == 403 and "core" not in ven.get("/api/dashboard/mio")[2]
    nots = ger.get("/api/notificaciones")[2]["notificaciones"]
    assert nots and all(n["estado"] == "simulado" for n in nots) and {n["tipo"] for n in nots} >= {"SALIDA_PREPARADA", "RESUMEN_SEMANAL"}
    paso(f"Dashboard coherente; {len(nots)} notificación(es), todas SIMULADAS (destinatarios del portal, nada enviado)")

    # 10. auditoria y recuperacion
    acciones = {a["accion"] for a in ctx.estado.listar_auditoria(500)}
    assert {"ciclo.diario", "salida.preparar", "salida.revisar", "excel.version", "decision.registrar", "visita.registrar", "impresion.solicitar", "impresion.resultado"} <= acciones
    paso("Auditoría registra ciclo, preparación, revisión, Excel, decisión, visitas e impresión")
    try:
        from cxc_portal import respaldo_cifrado as rc
    except Exception:  # noqa: BLE001
        rc = None
    pytest.importorskip("cryptography")
    pub, priv, _ = rc.generar_par_claves("contrasena-de-prueba-123", 2048)
    f = rc.crear_respaldo(ctx.estado, [pub], tmp / "bk")
    nuevo = tmp / "restaurado" / "portal.sqlite3"
    rc.restaurar_cifrado(f, priv, "contrasena-de-prueba-123", nuevo)
    r = Estado(nuevo)
    assert r.verificar_documentos() == [] and len(r.listar_excel("LAS_VEGAS")) == 2 and len(r.listar_salidas("LAS_VEGAS")) == 2
    assert r.decisiones_vigentes()["C90004"]["clasificacion_final"] == "SOLO_CONTADO"
    paso("Respaldo cifrado + restauración en carpeta nueva: salidas, 2 versiones de Excel, hojas y decisión íntegras")
    print(f"\nRESULTADO: {len(PASOS)} pasos verificados.")
