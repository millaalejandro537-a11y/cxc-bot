# PROJECT-STATE — CXC BOT (Bot de Rutas CxC)

**Actualizado:** 2026-10-09 (PC). Repo: `millaalejandro537-a11y/cxc-bot`, rama `main`.
Complementa `HANDOFF_CXC_BOT.md` (estado al 2026-09-13/10-03), que sigue vigente salvo lo sustituido abajo.

## Decisiones vigentes (Hub Notion "Cosprobell CxC — Principal", 08/10/2026)

- SAP B1 = fuente contable oficial. Nuestros sistemas consultarán **Cosprobell BD (Railway) directamente, SOLO LECTURA**, con credencial SELECT dedicada (aún NO existe/validada). Prohibido tocar BD, esquemas, usuarios, variables, servicios o automatizaciones de BLC. Sin conexión implementada.
- Semáforo de pago, clasificación de cliente, reincidencia/mejora, numeración de folders por zona (LV/FB/PB/RL/SC) y estructura `Actual/Histórico`: ver el detalle en el Hub. La viñeta roja y cualquier restricción de crédito requieren aprobación de gerencia.
- Piloto Las Vegas (foránea): plazo supuesto de 60 días desde la factura, conservando el vencimiento SAP. No extender a rutas locales (30 días).

## Hecho (2026-10-09)

1. **Config de LAS VEGAS corregida** (`config/routes.json`): portada `LAS VEGAS`, datos SAP en `Hoja1`, prefijo de archivo `LAS VEGAS` (antes sintético `LASVEGAS`/`Hoja2`, con el que el resolvedor NO encontraba `LAS VEGAS SEPTIEMBRE.xlsx`). Pruebas: `tests/test_las_vegas_config.py`; `test_runner.py` y `test_real_routes_structural.py` actualizados (ya sin config ad-hoc).
2. **Módulo puro `cxc_bot/payment_behavior.py`** + `tests/test_payment_behavior.py`. Sin E/S: no lee SAP/BD/Excel ni escribe nada; solo propone.
   - Por factura: días de atraso, categoría A (≤5) / B (6–15) / C (16–60) / MALO (≥61), vencimiento SAP y usado (política `PILOT_LAS_VEGAS_60D`), excepciones. El importe no interviene.
   - Por cliente: EN_PRUEBA (<4 pagadas verificables), predominio (sin promedios), empate → REVISION_HUMANA, MALO → `requiere_gerencia`, `vineta_aprobada` siempre `None`.
   - Facturas abiertas vencidas (> tolerancia A) cuentan para categoría y alertas, no para el mínimo de 4. Alertas: `ABIERTA_MORA_RANGO_MALO`, `PAGADA_RANGO_MALO`, `REINCIDENCIA`, `MEJORA_SOSTENIDA`.
   - Reincidencia (≥6 pagadas consecutivas con >5 días); mejora (≥3 consecutivas a tiempo tras `fecha_conversacion`; pide reevaluación, no cambia categoría).
   - `definitivo=False` mientras haya excepciones pendientes (posible contado, liquidación no verificable, sin vencimiento, fecha inconsistente).

## Supuestos que NO estaban cerrados (confirmar con Alejandro; están en el docstring del módulo)

- **PENDIENTE DE DECISIÓN — facturas abiertas con 0–5 días de atraso (y no vencidas):** hoy NO cuentan (ni como A ni como evidencia; se marcan `ABIERTA_SIN_ATRASO_SIGNIFICATIVO`). Alternativa posible: contarlas como A. No asumir lo contrario sin confirmación.
- "Predominio" = estrictamente más facturas; empate en el máximo → revisión humana (sin umbral de proporción).
- "Consecutivas" se ordena por (vencimiento usado, fecha de factura, número). Mejora = pagada a más tardar en su vencimiento (días ≤ 0), no la tolerancia de 5.
- Vencimiento SAP = fecha de factura y `es_contado=None` → POSIBLE_CONTADO_PENDIENTE (se evalúa, pero provisional).
- Facturas cerradas sin fecha de liquidación confiable no cuentan como pagadas verificables.

## Fixtures: advertencia

`tests/fixtures/rutas_reales/LAS VEGAS MAYO.xlsx` (23 clientes, mayo) es un fixture HISTÓRICO solo para probar estructura. **No representa la lista vigente de 24 clientes de Las Vegas** (le falta C10595, Peña Blanca) ni su orden físico/zonas. La fuente vigente es la lista de 24 códigos confirmada por Alejandro el 2026-10-08 (Hub Notion). No usarlo como roster real ni como dato financiero.

## NO hecho / bloqueos

- **Cosprobell BD:** sin credencial SELECT dedicada, sin inspección de esquema, sin adaptador. Si solo hay usuario administrador: detener y pedir credencial de lectura a BLC. Falta además el repo sin driver de BD (`requirements.txt`).
- **Mapeo BD → `InvoiceRecord`** pendiente (tablas vistas solo por nombre: `facturas`, `pagos_facturas`, `notas_credito`, …).
- **44 facturas con vencimiento el mismo día** (¿contado?), 10 cierres sin fecha reconstruible, 12 diferencias de aplicaciones (RCT2), fechas de recibo contables vs efectivas, fecha de corte del saldo L 32,600.03: sin resolver. Nada de Las Vegas está terminado ni hay viñetas aprobadas.
- `Las_Vegas_Orden_Por_Zonas_Corregido.xlsx` no está en el repo ni localizado; se necesita para la numeración por zona.
- **Numeración por zona** (hoy `excel_service._rebuild_client_block` escribe correlativo global `i+1`): sustituida como regla, no implementada.
- **`Actual/Histórico`:** no migrado; resolvedor y runner siguen en `<Vendedor>/<Ruta>/` plano.
- Persistencia de evaluaciones y detección de cambio de categoría: no implementada. Tipo de ruta (local/foránea) aún no está en `RouteConfig`.
- SAP Service Layer (`docs/SAP-INTEGRATION-TODO.md`) deja de ser el camino principal por la decisión de Cosprobell BD.

## Siguiente paso sugerido

Credencial SELECT de BLC → inspección de esquema solo lectura → adaptador de lectura de facturas/pagos a `InvoiceRecord` → conciliación de las 24 cuentas de Las Vegas contra las tres consultas SAP. Después: numeración por zona, persistencia de evaluaciones y, al final, `Actual/Histórico`.

## Pruebas

`python -m pytest tests/ -q` con el venv `C:\Users\LTP\Documents\Codex\environments\cxc-bot-windows\.venv`. 
Último resultado completo (2026-10-09, antes del commit): **290 passed, 1 skipped** (test POSIX conocido en Windows), 165.7 s. Antes de este trabajo: 229 passed, 1 skipped. El hash del commit está en `git log`.

## Portal CxC — preparación operativa (2026-10-10, sin deploy, sin push)

Detalle en `docs/PORTAL-MVP.md`, `docs/DESPLIEGUE-SEGURO.md`, `docs/INTEGRACION-SAP-VIVO.md`, `docs/PLAN-VINETAS-EXCEL.md`.
- **Un solo sistema:** `cxc_portal/` (API Python + BD propia SQLite con usuarios/roles/rutas/decisiones/visitas/auditoría) + `portal-web/` (React). Semáforo = único motor `cxc_bot/payment_semaphore.py`. Reportes gerenciales = `cxc-reporting-core` (rama feature, sin fusionar ni modificar) alimentado por un RowProvider del portal; cortes del snapshot NO VALIDADOS.
- **Seguridad:** autenticación obligatoria, RBAC en la API (admin/gerencia/vendedor), CSRF, scrypt, bloqueo, sesiones con expiración, cabeceras, host/origen, TLS directo o tras proxy HTTPS; fuera de loopback solo con TLS + hosts permitidos.
- **Generador de Excel:** `ClientEntry.folder` y `nombre_ruta` (opt-in), `validate_workbook(folder_mode=, nombres_literales=)`, plantilla `YORO FOLDERS` en `config/routes.json`; el Excel generado por el portal reproduce los aprobados de Las Vegas y Yoro celda por celda. Aún sin viñetas de color (plan aparte).
- **Fuente de datos:** interfaz `FuenteDatos` (snapshot hoy). `validar_fuente` = prueba de aceptación de solo lectura para el ingeniero.
- **Datos privados** (fuera de Git): `C:\Users\LTP\Documents\CxC-Datos-Privados\{2026-10-08, estado}`. Credenciales iniciales generadas en `estado\credenciales_iniciales.txt` (borrar tras el primer acceso).
- **Bloqueos reales:** decisión de acceso (Tailscale/Cloudflare/nube) y su costo/instalación; credencial SELECT + contrato Gelsyn + prueba contable; cuentas reales; 2.º factor; respaldo/restauración.

## Actualización 2026-10-11 (sin push, sin deploy)
- **Estabilizado:** los 5 fallos de `test_portal_robustez` eran del ayudante de pruebas (no guardaba la cookie del login en dos pasos); la autenticación no se tocó. Suite: 427 passed, 1 skipped.
- **Hojas de visita (Occidente):** documento aprobado de 8 hojas / 116 clientes registrado como v1 (sha256 en la BD), almacenado en privado, vinculado a salidas, con versiones inmutables y nueva versión solo con confirmación + motivo. Clientes cargados como candidatos SIN confirmar; colores preliminares (CANDIDATO ≠ decisión de gerencia). Ver `docs/HOJAS-DE-VISITA-E-IMPRESION.md`.
- **Operación por salida:** salidas, preparación con excepciones, seguimiento y omisiones consecutivas, candidatos con confirmación humana, folders permanentes, cola de impresión + agente (solo diseño/registro; no imprime).
- **Seguridad:** 2FA TOTP, token de perímetro, health con BD, respaldo/restauración/exportación probados.
- **Frontend:** 2FA, salidas, hojas de visita (vista de impresión), visitas, clientes por confirmar, cola de impresión.
- **Nube:** `docs/NUBE-ALOJAMIENTO.md` (propuesta Railway + costos), `Dockerfile`/`railway.toml` borradores NO construidos.
