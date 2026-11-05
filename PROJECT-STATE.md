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

## Occidente integrado (2026-10-10, sin push, sin deploy)
- Archivo aprobado: `OCCIDENTE_PORTAL_116_CLIENTES_ZONAS_60D.json` (de `OCCIDENTE OCTUBRE.xlsx`, sha256 `b585021f…`). Integrado con `python -m cxc_portal.admin integrar-ruta --estado DIR --ruta OCCIDENTE --archivo JSON [--aplicar]` (`cxc_portal/integrar_ruta.py`): simula por defecto, se detiene sin escribir ante cualquier contradicción y es idempotente.
- Resultado en la BD real: 116 clientes únicos confirmados como pertenecientes a la ruta (foránea, 60 días, José Carpio), 20 zonas, 116 folders y orden físico idénticos al JSON; folders marcados como usados (no se reutilizan). Los 116 candidatos pasaron a `confirmada`.
- Intactos: documento de hojas de visita v1 (8 hojas, mismo sha256 y archivo), decisiones (7, sin ninguna de Occidente), sin salidas ni hojas regeneradas. Respaldo previo: `CxC-Datos-Privados\respaldos-pre-occidente\`.
- SIGUE PENDIENTE de gerencia: los colores son PRELIMINARES; 15 candidatos a solo contado y 1 cliente sin evidencia. La ruta queda `EN_CONFIRMACION` (marcarla OPERATIVA es acción humana en Confirmar clientes). Colocación de viñetas físicas no confirmada. Sin saldos importados.
- Pruebas: 436 passed, 1 skipped (POSIX); las 12 de datos privados requieren `CXC_DATOS_DIR=...\2026-10-08`.

## Auditoría previa al despliegue (2026-10-11, sin push, sin deploy, sin Docker)
Informe completo, alternativas y costos: `docs/AUDITORIA-DESPLIEGUE.md`. Corregido: healthcheck bloqueado por validación de Host; export/respaldo omitían documentos (hojas de visita) y cola de impresión; IP falsificable vía X-Forwarded-For; sin timeout de socket; Dockerfile (propiedad del volumen, VOLUME, dependencias fijadas, LF en scripts). NO verificado: imagen en Linux/Docker (apagado). Falta: respaldo programado externo cifrado, carga de documentos/datos en la nube, decisión sobre datos financieros fuera de la PC. Pruebas: 445 passed, 1 skipped (con `CXC_DATOS_DIR`).

## Prueba real en contenedor (2026-10-11)
Imagen construida y probada en Docker 29.8.1 con datos sintéticos (detalle en `docs/AUDITORIA-DESPLIEGUE.md` §8): API/2FA/permisos, persistencia tras kill -9, respaldo+restauración con documentos en volumen nuevo, proceso UID 10001 sin capacidades, configuración endurecida (read-only, cap-drop ALL). Corregido: `ENV PORT=8080` faltante y `restaurar` sin documentos/verificación. Contenedores y volúmenes de prueba eliminados; la imagen `cxcprobe/portal:test` se conserva. Pruebas: 435 passed + 13 skipped (12 requieren `CXC_DATOS_DIR`, 1 POSIX).

## Respaldos cifrados y consistencia CLI↔servidor (2026-10-11, sin activar nada real)
Detalle y custodia de claves en `docs/RESPALDOS-CIFRADOS.md`. `cxc_portal/respaldo_cifrado.py`: AES-256-GCM + RSA-OAEP (envelope, `cryptography`), reutiliza `respaldar/restaurar`; solo claves públicas en el servidor; rotación 14 diarios + 6 mensuales; prueba de restauración completa con la privada. Plantillas NO activas: `deploy/respaldo.sh`, `deploy/respaldo-diario.ps1`. Consistencia: disparadores SQLite + contador `cambios` hacen que el servidor vea cambios hechos por CLI sin reiniciar. Probado con datos sintéticos y en contenedor. `cryptography` NO está instalado en el venv compartido (sus pruebas se omiten allí; se probó con una carpeta aislada). Pendiente: custodios, destino externo, autorización del primer respaldo real. Pruebas: 453 passed + 13 skipped con cryptography disponible.

## Reglas aprobadas (2026-10-11): mora ≥ 5 d configurable, oportunidad informativa y gestión por excepción
Implementadas y probadas (`tests/test_gestion_por_excepcion.py`): borrador siempre; publicación normal solo con fuente validada y sin excepciones que decidan; revisión de gerencia solo ante excepciones que requieren decisión; umbrales en `/api/configuracion` (auditado). Detalle en `docs/CIERRE-TECNICO-PRE-RAILWAY.md` §4.

## Cierre técnico pre-Railway (2026-10-11, sin push/deploy, solo datos sintéticos)
Estado por funcionalidad, parciales y supuestos: `docs/CIERRE-TECNICO-PRE-RAILWAY.md`; contrato de datos para BLC: `docs/CONTRATO-DATOS-BLC.md`. Nuevo: `flujo.py` (calendario hábil + ciclo diario idempotente, alertas, notificaciones simuladas, Excel Actual/Histórico, impresión), `dashboard.py`, `fuente_empresarial.py` (transporte simulado), `agente_impresion.py`, `estado_flujo.py`, frontend `Operacion.jsx`. Prueba completa de salida: `tests/test_salida_completa.py` (16 pasos). Pruebas: 492+ passed (con cryptography en carpeta aislada); las 12 de datos privados NO se ejecutaron (restricción de no usar bases financieras reales). Occidente sigue en confirmación (no operativa). Pendiente externo: credencial SELECT/contrato de BLC, datos reales validados, decisiones de custodia/destino de respaldos, acceso/hosting.

## Migración de Occidente a 119 y regla de contado (2026-10-12, sin tocar la BD original)
Informe y procedimiento: `docs/MIGRACION-OCCIDENTE-119.md`. Ensayada y verificada sobre copia aislada (respaldo previo en `CxC-Datos-Privadosespaldos-pre-migracion-119`; JSON v3 y copia en `migracion-119`): 3 clientes nuevos (23 LE, 1 SJ, 2 SJ), zona SJ, prefijos LL→LO y D→DN (etiquetas retiradas no se reutilizan), hojas de visita v2 (119). Nuevo: `migrar_ruta.py`, `verificar_migracion.py`, `Estado(solo_lectura=True)`. **Regla vigente: resultado Contado = ROJO automático sin aprobación de gerencia** (motor único, `CONTADO_AUTOMATICO`). Pendiente: aprobación para aplicar a la BD real, decisión sobre C10061 (revisión humana) y criterio de pago del documento en 4 clientes.

## Corrección previa a la migración de Occidente (2026-10-13, BD original intacta, sin push/deploy)
Detalle y cifras: `docs/MIGRACION-OCCIDENTE-119.md` §4–§6.
- **Precedencia (resuelto):** el resultado del sistema prevalece sobre decisiones anteriores de gerencia (`payment_semaphore.SISTEMA_PREVALECE`); las decisiones se conservan solo como trazabilidad (`decision_aplicada=false`) y resuelven únicamente revisión humana/sin evidencia. El Portal rechaza (409) decisiones sobre categorías que el sistema determina; la UI no las ofrece. Efecto en las 3 rutas: el cálculo no cambia; C10855 (LV), C10672 y C10758 (Yoro) pasan de C a ROJO.
- **C10270 / C10298 / C10502 (resuelto):** el documento solo contó recibos directos (consulta 3); el motor cuenta además los conciliados (consulta 2) y la suma = `ImporteAplicado` SAP al centavo. **C10266 (resuelto):** el documento usó el vencimiento SAP como base del plazo (regla descartada); correcto 51.56.
- **C10061 (causa determinada, decisión pendiente):** 3 facturas pagadas con *anticipos* (recibos anteriores a la emisión) + 1 cerrada con recibo y nota de crédito; la regla aprobada no acredita anticipos ⇒ revisión humana. Interruptor `ANTICIPOS_RESPALDAN_PAGO` (apagado): con `True` C10061 = A, pero Occidente tendría 10 cambios de clase a mejor (7 salen de ROJO). Decisión de metodología de Alejandro.
- Evidencia nueva en el motor/lector: `Recibo.conciliacion` (fecha de conciliación, informativa), `FacturaSAP.notas_credito`, `EvidenciaFactura.recibos_anteriores/notas_credito` y motivo explicativo. Con el interruptor apagado: 0 diferencias en las 1 148 cuentas del snapshot.
- Ensayo 2 sobre copia nueva (desde el respaldo previo): `verificar_migracion` 15 PASS / 0 FAIL; diferencia tabla por tabla: solo filas de Occidente; LV, Yoro, decisiones, usuarios idénticos. `verificar_migracion` ya no falla por diferencias de clasificación/media con el documento (informativas).
- La migración real sigue pendiente de aprobación de Alejandro (procedimiento en el documento §7).

## Migración de Occidente APLICADA a la BD real (2026-10-13, autorizada por Alejandro; sin push/deploy/SAP)
- Ejecutada desde el commit `e894efb` con `migrar-ruta --aplicar` (transaccional) y respaldo verificado `CxC-Datos-Privados\respaldos-migracion-real\portal-20261013-052210.sqlite3` (sha256 `f59f5537…`, integridad ok; se conserva también `respaldos-pre-migracion-119` y `respaldos-pre-occidente`). Ensayo previo contra la BD real: idéntico al de la copia, sin conflictos; segunda aplicación idempotente.
- Resultado verificado: Occidente 116 → **119 clientes únicos**, 119 folders únicos, **21 zonas** (nueva SJ; LL→LO y D→DN; etiquetas retiradas conservan su marca de agua), orden y folders == JSON v3 (C10773 `1 SJ`, C11156 `2 SJ`, C10266 `23 LE`). Hojas de visita: v1 HISTÓRICA (116) y **v2 VIGENTE** (119 clientes, 8 hojas, sin advertencias); archivos en `estado\documentos`.
- `verificar_migracion`: **15 PASS / 0 FAIL**; `PRAGMA integrity_check` ok, `foreign_key_check` vacío. Diferencia contra el respaldo fresco: solo filas de Occidente (clientes, zonas, folders, candidatos, documentos, 2 asientos de auditoría) y tablas nuevas del Portal; **Las Vegas (24) y Yoro (31) idénticas, decisiones (7), usuarios, vendedores, salidas y visitas sin cambios.**
- Occidente sigue **EN_CONFIRMACION (no operativa)**; sin decisiones, salidas ni hojas generadas. `ANTICIPOS_RESPALDAN_PAGO = False`. Clasificaciones del motor sin tocar: 31 A · 16 B · 39 C · 17 prueba · 15 ROJO · 1 revisión humana (C10061).
- Pendiente real: decisión de metodología sobre anticipos (C10061), confirmar clientes/marcar operativa (acción humana), datos vigentes de BLC. Archivo de origen de las hojas: `migracion-119\datos-real\01_Rutas`.

## Viñetas digitales (Issue #2) — rama `feat/vinetas-digitales`, 2026-11-04 (sin push, datos sintéticos)
- **Ya existía y se reutilizó:** `payment_semaphore.color_final` (motor único), API de rutas con `semaforo.color_final`, puntos de color en el frontend, resumen del runner. **Faltaba:** llevar el color al Excel de ruta y declarar su estado (definitivo/provisional/revisión).
- **Nuevo:** `cxc_bot/route_markers.py` (traduce el resultado del motor; sin reglas propias): DEFINITIVA solo con fuente validada y clase con color; PROVISIONAL con fuente no validada; REVISION (revisión humana/candidato) y SIN_DATOS nunca llevan color de crédito. `generate_route(markers=…, marker_col=…)` (opt-in; solo el relleno de una celda por cliente), `validators.validate_vinetas`, parámetro `vinetas_excel` (0 por defecto, auditado), `semaforo.vineta` en la API y etiqueta de estado en la interfaz.
- **Pendiente de confirmación humana:** la celda de la viñeta (A/Folder es propuesta), contraste de fuente sobre rellenos oscuros y comparación contra el Excel aprobado (privado). No hay viñetas para documentos operativos ni se afirma que un libro con datos actuales esté listo para imprimir.
- Documentación reconciliada: `docs/PLAN-VINETAS-EXCEL.md` (rojo ya no requiere aprobación) y `docs/CONTRATO-DATOS-BLC.md` (endpoints de solo lectura del Cosprobell Backend, no SELECT).

## Cierre previo al uso (2026-11-04, rama `feat/vinetas-digitales`, sin push, datos sintéticos)
- Auditoría por área y defectos corregidos: `docs/AUDITORIA-CIERRE-PORTAL.md`. Impresión en blanco y negro (AZUL/ROJO/BLANCO/VERDE indistinguibles) y puntos a validar con el Excel real: `docs/VALIDACION-FINAL-IMPRESION.md`.
- Nuevo: vigencia de la fuente evaluada en cada consulta (antes quedaba congelada al cargar); copias por salida; color ROJO y legibilidad en la vista de impresión de hojas de visita; textos de reglas alineados con `CONTADO_AUTOMATICO`/`SISTEMA_PREVALECE`; barrido de permisos y robustez de los 88 endpoints.
- E2E de interfaz (Playwright + Chrome instalado, 11 pruebas, `tests/e2e/`): autenticación/2FA, permisos, rutas, calendario, salidas, hojas de visita (PDF real: una página por hoja), impresión simulada, avisos y barrido escritorio/móvil. Corregido el desborde de las pestañas en móvil. Resultados en `docs/AUDITORIA-CIERRE-PORTAL.md`.

## Fuente Gelsyn — preparación de la conexión de pruebas (2026-11-04, rama `feat/fuente-gelsyn-conexion`, sin conexión ni consultas)
- `docs/CONEXION-GELSYN-PRUEBAS.md`: hallazgos de la referencia técnica de Gelsyn, decisión de vía (endpoints `GET /finanzas/*`, no SQL), credencial dedicada de solo lectura, requisitos exactos y plan de validación contra SAP B1. `gelsyn_config.py` + `gelsyn_preflight.py` + `.env.example`: configuración por variables de entorno protegidas, rechazo de URLs postgres, guardas pruebas/producción. Partidas abiertas y conciliaciones NO existen en la carga de Gelsyn: el adaptador de partidas del core no aplica tal cual.
- Mapeo `GET /finanzas/*` → transporte del Portal (`cxc_portal/transporte_gelsyn.py`, solo respuestas sintéticas): importes exactos, nulos conservados, fechas comerciales UTC−6, cancelaciones indefinidas apartadas, versión por entidad y corte; sin partidas ni conciliaciones la fuente es `EMPRESARIAL_PRUEBAS`, no viva y nunca apta para publicar. Decisión vigente: NO rotar la contraseña `postgres` existente (riesgo aceptado, documentado en `docs/CONEXION-GELSYN-PRUEBAS.md`). Dependencias externas: backend de pruebas de Gelsyn, API key de solo lectura, ejemplo real de respuestas.
- Herramienta de inspección READ ONLY de la copia `Cosprobell-pruebas` (`cxc_portal/inspeccion_pg_pruebas.py`, preparada y probada con conexión falsa; NO ejecutada: la URL no está disponible para el agente). Exige atestación humana de identidad, archivo de URL 0600 y no toca tablas de usuarios/sesiones.
- Inspección READ ONLY de `Cosprobell-pruebas` ejecutada (2026-11-04): coincide con la referencia de Gelsyn (67 tablas, conteos exactos, integridad perfecta). Hallazgos: cortes por entidad no simultáneos (clientes 27/10, aplicaciones 30/10, facturas/pagos 4/11: la regla `min` descartaría 48 facturas), 13,9 % de facturas cerradas sin aplicación y 19,8 % de pagos sin aplicar (conciliaciones no importadas), 178 clientes con saldo de control distinto. Detalle en `docs/CONEXION-GELSYN-PRUEBAS.md` §11.
- Corte de Gelsyn corregido (documentos = facturas/pagos; cortes por entidad registrados; saldo de control de otro corte no se emite) y corrida en seco del motor sobre `Cosprobell-pruebas` para las 3 rutas aprobadas (174 clientes): al corte 08/10 173 coinciden con la clasificación aprobada, 1 indeterminado (C10061, divergencia ya conocida), 0 cambian; deriva al 04/11: 11. Detalle en `docs/CONEXION-GELSYN-PRUEBAS.md` §12. Sigue sin publicarse nada: faltan partidas/conciliaciones, API/rol de lectura y validación contable.

- 2026-11-05 Issue #5: PARTIAL_CONTRACT_RECEIVED / NEEDS_SAFE_API_TEST_ACCESS_AND_SAP_RECONCILIATION. Transporte Gelsyn con nombres documentados, validado 174/174 vía servidor falso; ver docs/MATRIZ-CONTRATO-GELSYN.md. Rama feat/fuente-gelsyn-conexion local, sin push.
