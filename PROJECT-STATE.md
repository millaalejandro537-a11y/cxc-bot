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
