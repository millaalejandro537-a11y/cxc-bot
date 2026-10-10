# Portal CxC — MVP (2026-10-09)

Aplicación web responsiva (gerencia y vendedores) sobre los motores existentes. **Solo lectura**: no escribe en SAP ni en Cosprobell BD, no usa credenciales, no está desplegada.

## Arquitectura

```
portal-web/ (React 18 + Vite) ──/api──► cxc_portal/server.py (Python stdlib, 127.0.0.1:8780, solo GET)
                                          ├─ store.py          rutas, clientes en orden de folders, conciliación con el Excel aprobado
                                          ├─ (cxc_bot/sap_exports.py: consultas SAP 1/2/3 -> facturas + recibos + decisiones)
                                          ├─ gerencia_excel.py Excel aprobados (lista/orden, decisiones de gerencia, oráculo de regresión)
                                          ├─ demo.py           ruta SINTÉTICA
                                          └─ bridge.py         puente OPCIONAL a cxc-reporting-core (solo detecta)
                       cxc_bot/payment_semaphore.py  ← ÚNICO motor del semáforo (CXC BOT y portal)
```

- **Un solo motor**: `cxc_bot/payment_semaphore.py` (reglas vigentes 09/10/2026). `payment_behavior.evaluate_client`/`evaluate_portfolio` (reglas superadas) quedan **deprecados** (emiten `DeprecationWarning`); solo se conservan sus primitivas. El portal no contiene reglas: solo presenta.
- **Backend Python** (no Node/Express): las reglas viven en Python y no se duplican. Sin dependencias nuevas en el venv. La propuesta de Helsing (Express 5/Prisma/Zod) no se adoptó; cambiar la capa HTTP no tocaría las reglas.
- **`cxc-reporting-core`** no se fusiona ni se modifica. Su adaptador será la vía de datos en vivo cuando haya contrato (Gelsyn) y credencial SELECT.

## Datos privados (fuera de Git)

Los datos reales viven en `C:\Users\LTP\Documents\CxC-Datos-Privados\2026-10-08` (paquete `Cosprobell_CxC_Datos_MVP_08-09Oct2026.zip`, hashes SHA-256 verificados) con `01_Rutas/` y `02_SAP_08Oct2026/`. El backend los recibe por `--datos` o `CXC_DATOS_DIR`; no se copian al repo, al frontend ni a `dist/`. Sin ese directorio solo carga la ruta DEMO.

| Tipo de fuente | Significado |
|---|---|
| `SNAPSHOT_SAP_HISTORICO` | Consultas SAP exportadas el 08/10/2026. **No es en vivo.** Cada vista muestra la fecha de corte. |
| `SINTETICO_DEMO` | Ruta DEMO inventada. Excluida de las prioridades. |
| `EN_VIVO` | No existe (sin credencial SELECT ni conexión SAP). |

## Reglas del semáforo (`payment_semaphore`)

Últimas 10 facturas (emisión DESC, número DESC) · ≥ 4 facturas en TODO el historial (pagadas y abiertas) o EN_PRUEBA · plazo de la ruta (foránea 60 / local 30) desde la emisión, no el vencimiento SAP · mora = max(0, último recibo contable —o corte si está abierta— − emisión − plazo) · a la media entran pagadas íntegramente respaldadas por recibos y abiertas con mora > 5 · cierres sin cobro integral (nota de crédito, recibos anteriores a la emisión) quedan como excepción · media sin redondear: A ≤ 5, B ≤ 15, C ≤ 60, > 60 candidato a solo contado (nunca rojo sin decisión) · alerta por cada factura con mora > 60, SEPARADA: abiertas = alerta operativa de cobro (con saldo pendiente); pagadas = alerta histórica de comportamiento (nunca cobro pendiente) · decisión de gerencia aparte del cálculo (autor, motivo, fecha).

## Decisiones de gerencia

Se registran en `decisiones_gerencia.json` dentro del directorio privado (los Excel originales NO se modifican). Si una decisión posterior difiere del Excel aprobado (p. ej. Espacio de Belleza y Antonella: candidato → SOLO CONTADO/roja), el portal muestra el cálculo automático, la decisión y la nota de conciliación `explicadas_por_decision_posterior`. El mismo archivo lo usa el generador de rutas.

## Integración con el generador de rutas de CXC BOT

`runner.process_due_routes(..., payment_evidence=<fuente>, payment_decisions=...)` (CLI: `process-due-routes --semaforo-datos DIR`) agrega a cada ruta con `credit_days` definido (`config/routes.json`: Las Vegas y Yoro = 60) el semáforo de sus clientes en `RouteJobResult.semaphore`. Es opcional, no altera el Excel generado y un fallo se reporta en `semaphore_error` sin impedir la publicación. Sin plazo definido no se calcula (no se adivina). Escribir la viñeta/color dentro del Excel operativo queda para una fase posterior.

## Validación

El motor, alimentado solo con las consultas SAP, reproduce los dos Excel aprobados por gerencia: **55 de 55 clientes** sin diferencias (historial, ventana, pagadas, abiertas, no acreditadas, evaluables, suma de mora, media, alertas, categoría), y el estado/mora/“en promedio” de cada una de las facturas de Las Vegas. Distribución final: Las Vegas 6 A · 3 B · 4 C · 9 en prueba · 2 solo contado (aprobados por gerencia después del Excel); Yoro 11 A · 8 B · 9 C · 2 solo contado · 1 en prueba. Si el Excel y el motor difieren, el portal lo marca por cliente (`conciliacion_gerencia`) y en `/api/fuentes`.

## Ejecutar

```bash
# backend + frontend construido -> http://127.0.0.1:8780
python -m cxc_portal.server --datos "C:\Users\LTP\Documents\CxC-Datos-Privados\2026-10-08"
# desarrollo del frontend (proxy /api) -> http://localhost:5180
cd portal-web && npm install && npm run dev      # construir: npm run build
# pruebas (las de datos privados se omiten sin CXC_DATOS_DIR)
set CXC_DATOS_DIR=C:\Users\LTP\Documents\CxC-Datos-Privados\2026-10-08 && python -m pytest tests -q
```
Python: `C:\Users\LTP\Documents\Codex\environments\cxc-bot-windows\.venv`.

## Seguridad local

El servidor se niega a escuchar fuera de loopback (127.0.0.1/localhost/::1) hasta que exista autenticación real. Solo GET; sin escrituras.

## Qué funciona realmente y qué depende de SAP en vivo

**Funciona hoy (verificado 2026-10-09, solo lectura, sobre el snapshot SAP del 08/10/2026):** las 2 rutas y sus 55 clientes en orden de folders; clasificación automática = Excel aprobado en 55/55; decisiones de gerencia (7: 1 del Excel de Las Vegas, 2 posteriores de Las Vegas, 4 de Yoro) junto al cálculo; detalle de las últimas 10 facturas con recibos, mora y motivo; alertas separadas (cobro operativo vs. histórico); filtros por zona/clasificación/texto; reportes CSV por ruta; navegación en escritorio y móvil (sin desborde de página); reinicio del servidor sin pérdida de decisiones; servidor solo local.

**Depende de SAP/Cosprobell BD en vivo (hoy NO existe):** saldos y facturas actuales (todo es del 08/10/2026 y envejece), nuevas facturas/pagos, que las alertas de cobro reflejen la realidad de hoy, el top de prioridades de cobro "confiable", cualquier corte validado por contabilidad. Hasta tener credencial SELECT y contrato, el portal debe presentarse como **demostración sobre snapshot histórico**: toda vista lo indica con la fecha de corte.

**No existe todavía:** autenticación y roles reales (el selector es DEMO), registro de visitas/decisiones editable desde el portal (las decisiones se registran en un archivo privado), envío automático de reportes, viñetas dentro del Excel (ver `docs/PLAN-VINETAS-EXCEL.md`), deploy.

## Verificación automatizada

`tests/test_portal_e2e_privado.py` (con `CXC_DATOS_DIR`): HTTP real sobre los datos privados — 55 clientes, recálculo independiente de media/categoría/mora por factura, alertas, decisiones, filtros, CSV, equivalencia tras “reiniciar” (store nuevo desde disco), y que ni el portal ni las pruebas modifican nada en el directorio de datos (hashes SHA-256 de los Excel originales intactos). `tests/test_portal_api.py`: API con datos sintéticos, solo lectura, rechazo de hosts ajenos (DNS rebinding) y de `--host` fuera de loopback.

## Falta para producción

Credencial SELECT y adaptador de lectura (Cosprobell BD) · cortes validados de `cxc-reporting-core` · autenticación y roles reales (el selector es DEMO) · BD propia para visitas/decisiones/usuarios (alertas de visitas hoy es maqueta) · envío automático de reportes · escribir viñeta/semáforo dentro del Excel operativo de CXC BOT · numeración por zona y `Actual/Histórico` en CXC BOT · deploy.
