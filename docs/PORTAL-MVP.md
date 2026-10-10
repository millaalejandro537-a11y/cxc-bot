# Portal CxC — preparación para uso operativo (2026-10-10)

Aplicación web responsiva (Windows, Mac, iPhone, iPad, Android) que une **CXC BOT** (rutas, Excel, semáforo) y **cxc-reporting-core** (cartera y reportes gerenciales). **No escribe en SAP ni en Cosprobell BD**, no usa credenciales administrativas, **no está desplegada**: corre solo en `127.0.0.1:8780`. Fuente de datos actual: **snapshot SAP del 08/10/2026 (no en vivo)**.

## Arquitectura (un solo sistema, sin duplicar reglas)

```
portal-web/ (React 18 + Vite)  ──/api (cookie HttpOnly + CSRF)──►  cxc_portal/  (Python, solo biblioteca estándar)
                                                                    ├─ app.py        rutas HTTP, RBAC en la API, cabeceras, CSRF, TLS/proxy
                                                                    ├─ security.py   scrypt, sesiones, límites, política de claves
                                                                    ├─ estado.py     BD PROPIA SQLite: usuarios, rutas/zonas/clientes+orden, decisiones, visitas, auditoría
                                                                    ├─ store.py      PortalServicio: vistas de ruta, decisiones, importación y generación de Excel
                                                                    ├─ fuentes.py    interfaz FuenteDatos (snapshot hoy → fuente viva sin reescribir la app)
                                                                    ├─ reporting_bridge.py  RowProvider → pipeline de cxc-reporting-core (sin modificarlo)
                                                                    └─ validar_fuente.py    prueba de aceptación de solo lectura para el ingeniero
cxc_bot/payment_semaphore.py   ← ÚNICO motor del semáforo (CXC BOT y portal)
cxc_bot/excel_service.py       ← generador de Excel de ruta (formato operativo) usado también por el portal
cxc-reporting-core (rama feature/cosprobell-backend-adapter, sin fusionar) ← cartera, antigüedad, por vendedor, prioritarios, Excel gerencial
```

Integración real de los dos motores: el portal alimenta el pipeline de `cxc-reporting-core` (RAW→STG→modelo→validación→snapshot→reportes) con un `RowProvider` propio sobre la fuente; el core calcula cartera/antigüedad/vendedor/prioritarios y exporta su Excel. El **semáforo de comportamiento de pago** lo calcula solo `payment_semaphore` (el core no lo duplica). Un corte construido desde el snapshot es **NO VALIDADO** (gate contable del core) y así se rotula siempre.

## Roles (aplicados en la API, no solo en la interfaz)
| Rol | Puede | No puede |
|---|---|---|
| **admin** | Usuarios, vendedores, rutas/zonas/clientes/orden de folders, importar rutas, ver todo, reportes, generar Excel, auditoría | Decidir clasificaciones de gerencia (separación de funciones) |
| **gerencia** | Ver todo, reportes gerenciales, generar Excel, **registrar/anular decisiones**, visitas, auditoría | Administrar usuarios y catálogo |
| **vendedor** | **Solo sus rutas**: clientes, semáforo, facturas, alertas; registrar visitas de sus rutas | Cartera global, reportes, decisiones, otras rutas (ni se revela que existen) |

## Qué funciona SIN SAP en vivo (verificado)
Administrar vendedores, rutas, zonas, clientes y orden de folders · **alta de rutas nuevas sin tocar código** (formulario o importando una lista de Excel de `01_Rutas`; la siguiente alta toma el siguiente número **de su zona**, retirar no renumera) · semáforo, últimas 10 facturas con recibos y alertas (abiertas = cobro; pagadas = histórico) sobre el snapshot con su fecha de corte · decisiones de gerencia con cálculo automático intacto, historial y anulación · registro de visitas con trazabilidad (falta de firma ≠ no visita; 3 salidas consecutivas) · **Excel de ruta (borrador)** con el generador de CXC BOT: dos hojas, formato y folders idénticos al aprobado (Las Vegas y Yoro verificados celda por celda) · reportes gerenciales del core (NO VALIDADOS) y su Excel · usuarios, roles, auditoría.

## Bloqueado hasta tener datos vigentes y autorización
Saldos/facturas actuales (todo es del 08/10/2026 y envejece) · que las alertas de cobro reflejen hoy · prioridades de cobro confiables · cortes **validados** de cartera (requiere conciliación contable) · usar el Excel generado como ruta operativa vigente · vendedor/zona de los ~320 clientes fuera de las rutas cargadas (el snapshot no los trae). Ver `docs/INTEGRACION-SAP-VIVO.md`.

## Datos y estado (fuera de Git)
- Datos: `C:\Users\LTP\Documents\CxC-Datos-Privados\2026-10-08` (`01_Rutas/`, `02_SAP_08Oct2026/`, `decisiones_gerencia.json` exportado). Estado/BD propia: `...\CxC-Datos-Privados\estado\portal.sqlite3`. Salidas Excel: `...\estado\salidas\`.
- La BD del portal es la fuente de verdad de rutas, decisiones y visitas; `decisiones_gerencia.json` se **exporta** de ella para que `process-due-routes --semaforo-datos` (CXC BOT) use las mismas decisiones.

## Ejecutar (local)
```bash
# sembrar rutas y decisiones desde los Excel aprobados (idempotente) y crear usuarios (clave generada en credenciales_iniciales.txt)
python -m cxc_portal.admin sembrar --estado <estado> --datos <datos>
python -m cxc_portal.admin crear-usuario --estado <estado> --usuario <u> --rol admin|gerencia|vendedor [--vendedor "Nombre"] --generar-clave
# servidor + frontend construido -> http://127.0.0.1:8780
python -m cxc_portal.server --datos <datos> --estado <estado> --reporting-src <cxc-reporting-core>\src
cd portal-web && npm install && npm run build          # desarrollo: npm run dev
# aceptación de una fuente de datos (hoy snapshot; mañana la viva)
python -m cxc_portal.validar_fuente --datos <datos>
```
Python: `C:\Users\LTP\Documents\Codex\environments\cxc-bot-windows\.venv`. Despliegue y exposición: `docs/DESPLIEGUE-SEGURO.md`.

## Verificación
`python -m pytest tests -q` — con `CXC_DATOS_DIR` corre además la regresión con datos reales por HTTP autenticado: 55 clientes (orden de folders, clasificación = Excel aprobado, 7 decisiones, alertas, facturas, filtros, CSV), persistencia tras reinicio, Excel originales intactos por hash, y el Excel generado por el portal idéntico al aprobado (Las Vegas y Yoro). Seguridad: `tests/test_portal_seguridad.py` (scrypt, bloqueo, expiración, CSRF, RBAC, host/origen, TLS real con certificado de prueba verificado, proxy HTTPS).

## Falta para empleados reales
Decidir el acceso (A/B/C del despliegue) y aprobar su costo/instalación · crear cuentas reales y capacitar · 2.º factor (hoy solo contraseña) · respaldo/restauración probados · servicio de arranque automático · credencial SELECT + contrato de Gelsyn + prueba contable (datos vivos) · viñetas de color en el Excel operativo (`docs/PLAN-VINETAS-EXCEL.md`, aprobado como siguiente fase) · envío automático de reportes · alertas de visitas “3 salidas sin visitar” y oportunidades de clientes A en pantalla (la lógica base existe; falta presentarlas).
