# Auditoría de cierre del Portal CxC (2026-11-04, rama `feat/vinetas-digitales`, datos sintéticos)

Método: lectura de `PROJECT-STATE.md`, `docs/CIERRE-TECNICO-PRE-RAILWAY.md` e Issue #2; barrido automático de los 88 endpoints (autenticación, roles, robustez); recorrido de las 11 pantallas del frontend con el Portal local en modo `--demo`; suite completa. Sin datos privados, SAP, BLC ni Railway.

| Área | Estado | Evidencia / observación |
|---|---|---|
| Rutas, clientes, zonas, folders, orden, alta/retiro | **Terminado y probado** | Flujo sintético Occidente (119 clientes, 21 zonas); retiro sin renumerar |
| Semáforo y decisiones | **Terminado** (motor único) | `CONTADO_AUTOMATICO`, `SISTEMA_PREVALECE`; metodología de anticipos pendiente de Alejandro |
| Excel de ruta | **Terminado, siempre BORRADOR hasta fuente validada** | Correcciones del PR #1; requiere libro base privado en la PC |
| Viñetas digitales | **Terminado, opt-in y desactivado** | Fuente validada + clase con color; celda A es propuesta |
| Calendario, salidas, ciclo diario | **Terminado y probado** | Idempotente; feriados configurables |
| Hojas de visita (documentos) | **Terminado como sistema**; el formato aprobado es privado | Versiones inmutables, vínculo por salida, descarga, RBAC; no se generan por diseño |
| Impresión | **Cola y agente simulado terminados**; impresoras reales pendientes | Copias por salida y color ROJO corregidos en esta rama |
| Notificaciones | **Solo simuladas** (`ENVIO_EXTERNO_HABILITADO=False`) | Canal real depende de decisión y credenciales |
| Usuarios, 2FA, permisos | **Terminado y probado** | Barrido de 88 endpoints: sin sesión nadie entra; rol fuera de lista = 403; ningún 5xx (salvo 503 deliberado de reporting-core sin configurar) |
| Dashboard / cartera / reportes | **Dashboard terminado; cartera NO VALIDADA** | Depende de fuente vigente y conciliación contable |
| Frontend | **Compila y tiene pruebas E2E** (11, Playwright + Chrome) | Escritorio y móvil sin errores de JS ni desbordes; ver «Pruebas E2E» |
| Respaldos, contenedor, despliegue | **Terminado y ensayado** | Ver `docs/DESPLIEGUE-SEGURO.md`, `docs/RESPALDOS-CIFRADOS.md` |

## Defectos encontrados y corregidos en esta fase
1. Vigencia de la fuente se calculaba al cargar (datos viejos seguían "aptos") — `fuente_empresarial.py`, `store.py`.
2. Endpoint de impresión de una salida ignoraba `copias`; vista de impresión sin color ROJO y texto negro sobre AZUL/ROJO (4.1–4.3 : 1).
3. Textos superados en `/api/reglas`, avisos y pantalla de avisos ("candidatos a solo contado siempre requieren decisión de gerencia", clave `CANDIDATO_SOLO_CONTADO`, "el cálculo no se altera"): ahora reflejan `CONTADO_AUTOMATICO` y `SISTEMA_PREVALECE`; prueba que los ancla al motor (`tests/test_textos_reglas_vigentes.py`).

## Depende de terceros, datos reales o infraestructura (no se puede cerrar aquí)
- Fuente vigente: endpoints de solo lectura del Cosprobell Backend (Gelsyn/BLC) y su contrato final; conciliación contable.
- Libro base y hojas de visita aprobadas de Occidente, Excel aprobado para fijar la celda de la viñeta, diez rutas sin organización física.
- Decisiones de Alejandro: anticipos (C10061), operativizar Occidente, 15 contados, impresión en blanco y negro (`docs/VALIDACION-FINAL-IMPRESION.md`).
- Infraestructura: hosting/acceso, impresoras y agente real, canal real de notificaciones, cuentas reales, respaldo externo.

## Pruebas E2E de la interfaz (2026-11-04) — `tests/e2e/`
Herramienta: no existía ninguna; se añadió **Playwright (Python) usando el Chrome ya instalado** (no descarga navegadores; `playwright` en `requirements-dev.txt`). Cada prueba levanta el Portal en loopback con estado temporal y datos sintéticos (`crear_entorno` + una ruta de 119 clientes), sirve `portal-web/dist` y vigila errores de JavaScript, errores de consola y respuestas 5xx (único 503 admitido: `cxc-reporting-core` no configurado). Si falta Playwright, Chrome o `dist`, se **omiten solas**.
Ejecutar: `cd portal-web && npm ci && npm run build`, luego `python -m pytest tests/e2e -q`.

| Prueba | Cubre |
|---|---|
| credenciales / logout | contraseña mala no entra; el logout invalida la sesión en el servidor |
| segundo factor | activar 2FA desde la pantalla (clave, código, códigos de recuperación), volver a entrar exigiendo código, rechazo de código malo, sin reutilizar el intervalo |
| permisos ×2 | el vendedor ve solo su ruta y no `/api/admin/*`, dashboard global ni configuración; otro vendedor no ve rutas ni documentos ajenos (404/403) |
| ruta y clientes | semáforo, «Solo contado» (ROJO automático) y estado «provisional: fuente no validada» / «pendiente de gerencia» |
| calendario → salida → avisos | programar (el domingo se rechaza), ciclo de preparación, revisión de gerencia, estado, avisos vistos/cerrados, notificaciones simuladas |
| hojas de visita | 8 hojas (16×7 + 7), color ROJO con texto blanco, **PDF real del navegador: 8 páginas carta horizontal (792×612)**, descarga = libro original (SHA-256) |
| impresión simulada | solicitud → pendiente → cancelar; nueva → agente SIMULADO → «Impreso»; ninguna impresora real |
| barrido de pantallas | 12 pestañas de gerencia y las del vendedor, en escritorio (1280×800) y móvil (375×812): sin errores y sin desborde horizontal |

**Defecto real encontrado y corregido:** en móvil las 12 pestañas formaban una fila de 844 px dentro de un encabezado de 343 px sin scroll ni salto de línea, así que casi todas quedaban inalcanzables y la página se desbordaba. `portal-web/src/styles.css`: `nav` ahora hace `flex-wrap` (las pestañas pasan a otra línea). La prueba de barrido falla con el CSS anterior y pasa con el nuevo.
**Resultado:** 11/11 E2E; regresión completa 618 pasan, 17 omitidas (datos privados), 0 fallos.
**Verificación de paginación:** la capacidad de ~16 filas/hoja (estimación anterior) queda confirmada con un PDF real del navegador. Sigue pendiente el render en Excel y la impresora real.
