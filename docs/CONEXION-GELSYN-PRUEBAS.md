# Conexión de la fuente empresarial — Cosprobell Backend de PRUEBAS (Gelsyn)

Estado (2026-11-04): **preparada, sin ninguna conexión ni consulta realizada.** `main` = `9e7dd56` (PR #1 y #4 fusionados); rama de trabajo `feat/fuente-gelsyn-conexion`.
Fuente: *Referencia técnica del backend Cosprobell* (Gelsyn, 4/11/2026, código revisado `ed8fe53`; 59 páginas). El documento no contiene claves ni URLs; la URL de PostgreSQL llegó aparte y **no se usa ni se guarda** aquí.

## 1. Lo que dice el documento y condiciona el diseño
| Hecho | Consecuencia para el Portal |
|---|---|
| `SAP Service Layer → puente → API de integración → PostgreSQL propio`. Extracción financiera **histórica y puntual**, no periódica. | Los datos son un corte, no tiempo real: se rotulan con su corte (como ya hace el Portal). |
| La BD `Cosprobell-pruebas` es una **copia del respaldo del 4/11/2026** de 67 tablas (incluye tablas de **acceso, sesiones y configuración**). 12 entidades financieras normalizadas. | No es producción ni dato vivo. Una cuenta con acceso a toda la BD vería también credenciales y sesiones: de ahí que el Portal **no use SQL directo**. |
| Lectura financiera: `GET /finanzas/{entidad}` (clientes, facturas, pagos, aplicacionesPagos, notasCredito, cuotas, movimientos…) → `{data, meta}`; `meta` = `version`, `ultimaCargaCompleta`, `monedas`, `siguienteCursor`, `advertencias`. `limit` 1–100, paginación por **cursor opaco**, mantener `version` durante la paginación. `GET /finanzas/estado` = control por entidad. | Es el contrato de lectura. Paginar con el cursor exacto devuelto; leer y conservar `version` y `ultimaCargaCompleta`. |
| Acceso "Aplicación financiera": cabecera `X-API-Key` de **alcance completo** + el nombre de la app debe estar en `FINANZAS_APPS_AUTORIZADAS`. **No existe SQL libre.** | La credencial del Portal es una API key (no una cuenta de BD). Ver §4: pedir clave dedicada y acotada. |
| **Partidas abiertas y conciliaciones NO se importaron**; `/finanzas/cartera` "no disponible en esta carga". `total − pagado` es el pendiente del documento, **no certifica el saldo abierto** tras conciliaciones; no se reconstruye con certeza toda aplicación manual de notas de crédito. | El saldo por cliente **no** puede venir de partidas. Se usa `clientes.currentAccountBalance` (control SAP) y se **reporta** su diferencia con Σ(docTotal − paidToDate); nunca se corrige en silencio (regla ya vigente en el contrato BLC). |
| `clientes.saldoFC/saldoSC/zonaFinanciera` = 100 % nulos; `uZona` 17 nulos de 1 170. Nulo ≠ cero. | Zona: `uZona`; saldo: `currentAccountBalance`. Nulos se reportan, no se rellenan. |
| Moneda local LPS y USD "de sistema"; FC no siempre USD. No sumar monedas distintas. | El motor asume una moneda: filas en otra moneda se apartan y se reportan (no se mezclan). |
| Cancelaciones: `cancelled`, `cancelStatus`, `canceladoSap`; "`Cancelled undefined` nunca significa no cancelado". | Una factura/pago cancelado o con estado indeterminado **no cuenta** como pagado ni como crédito vigente; se aparta y se reporta. |
| Importes `numeric(24,6)`; SAP devolvió residuos de coma flotante. Fechas: timestamps UTC, Honduras = UTC−6; `date` = fecha comercial. | Convertir fechas de documento a fecha comercial (no a UTC); no sumar con float en reportes financieros. |
| Hay que filtrar por `finVersion = finanzas_control.activoId` y confirmar `finanzas_normalizacion`. | Lo hace el endpoint (registros publicados); el preflight exige `ultimaCargaCompleta` y versión por entidad. |

## 2. Cómo usar `Cosprobell-pruebas` sin confundirla con producción
1. **Entorno explícito, sin valor por defecto:** `CXC_GELSYN_ENTORNO=pruebas|produccion`. Con `pruebas` el Portal **rechaza** hosts listados en `CXC_GELSYN_HOSTS_PRODUCCION` o con una etiqueta de producción (`prod`, `produccion`, `production`, `prd`); con `produccion` exige `CXC_GELSYN_CONFIRMAR_PRODUCCION=SI-ES-PRODUCCION-SOLO-LECTURA` y rechaza hosts de pruebas (`cxc_portal/gelsyn_config.py`, probado).
2. Todo rótulo de la interfaz y de los documentos sigue indicando "corte del 04/11/2026, NO en vivo": la fuente de pruebas **nunca** es apta para publicar (`apta_para_publicar=False` mientras no exista validación contable).
3. **Hoy no existe un backend HTTP de pruebas**: el documento (§5.2) dice que antes de conectar un backend de pruebas hay que definir sus credenciales y mantenerlo separado de usuarios reales y de la tarea de producción. Lo que Gelsyn debe levantar es **un servicio de backend apuntando a `Cosprobell-pruebas`** (su `DATABASE_URL` interna), no darnos la BD.

## 3. Por qué el Portal NO usa la URL de PostgreSQL (ni con un rol de solo lectura, como vía principal)
* Decisión canónica (Issue #2): el Portal consume **endpoints de solo lectura** del backend.
* La copia contiene tablas de acceso/sesiones/configuración; incluso un rol `SELECT` sobre `public` las expondría. Un rol acotado (§7) es la **vía de reserva**, solo si Gelsyn no puede levantar el backend de pruebas, y obliga a reimplementar en SQL la lógica de versión publicada (`activoId`) que hoy resuelve el backend.
* El cliente y la configuración rechazan cualquier URL `postgres://` / `postgresql://` (también si se pega en la variable de la API key).
* La URL recibida usa la cuenta administrativa `postgres`: **no debe usarse** para nada del Portal. Si viajó por chat/correo, considérese expuesta y pídase su **rotación** a Gelsyn.

## 4. Credencial: qué pedir y cómo guardarla
**Pedir a Gelsyn (una API key por entorno):** nombre de app dedicado (p. ej. `cxc-portal-pruebas`) incluido en `FINANZAS_APPS_AUTORIZADAS` del backend de pruebas; **alcance limitado a `GET /finanzas/*` y `/health`** (el documento solo describe claves "de alcance completo", que también abren endpoints operativos con `POST`; si no existe un alcance menor, pedirlo o aceptar el riesgo por escrito — el cliente del Portal solo emite `GET`, pero la clave en sí podría hacer más); sin permisos de puente (`BRIDGE_API_KEY` jamás); expiración/rotación definida; clave distinta de la de producción.
**Guardarla** (macOS; no queda en el historial ni en archivos del repo):
```bash
security add-generic-password -a "$USER" -s cxc-gelsyn-pruebas -w          # pide la clave de forma interactiva
export CXC_GELSYN_ENTORNO=pruebas
export CXC_GELSYN_BASE_URL="https://<backend-de-pruebas>"                    # sin credenciales ni parámetros
export CXC_GELSYN_API_KEY="$(security find-generic-password -s cxc-gelsyn-pruebas -w)"
export CXC_REPORTING_SRC="$HOME/Documents/Trabajo-Sync/Cosprobell/cxc-reporting-core/src"
python -m cxc_portal.gelsyn_preflight                                         # ≤ 7 GET mínimos; nunca imprime valores de datos
```
En Windows: variables de entorno del proceso o *Credential Manager*; en Railway: variables del servicio. **Nunca** en `.env` versionado (`.gitignore` ya excluye `.env*` salvo `.env.example`, que solo lista nombres), ni en argumentos de línea de comandos, prompts o logs. Toda representación de la configuración muestra solo `sha256(clave)[:8]`.

## 4 bis. Qué se reutiliza de `cxc-reporting-core` (y qué no)
* **Sí, tal cual:** `ReadOnlyApiClient` (solo `GET`, HTTPS, reintentos solo ante 429/5xx/timeout, errores sin secretos). Configurado con `X-API-Key` y sin esquema `Bearer` (`cxc_portal/gelsyn_config.cliente_solo_lectura`). No se modificó el core.
* **No aplica el contrato de su `CosprobellBackendAdapter`:** exige *partidas abiertas con `saldoAbierto` firmado* y *estado de sincronización*; **Gelsyn no ofrece ninguno de los dos** (las partidas no se importaron). Reutilizar ese adaptador tal cual haría fallar el corte. Opciones (decisión pendiente): (a) mapear facturas/pagos/aplicaciones/notas de crédito a la `FuenteEmpresarial` del Portal (`docs/CONTRATO-DATOS-BLC.md`), que es lo que necesita el semáforo; (b) más adelante, partidas abiertas cuando Gelsyn las importe, para la cartera del core.
* La paginación de Gelsyn es por cursor en `meta.siguienteCursor` (anidado), distinta del `nextCursor` de primer nivel que espera el iterador del core: el bucle de páginas del transporte del Portal usará `get_json` del cliente y leerá el cursor de `meta`.

## 5. Mapeo previsto Gelsyn → contrato del Portal (se confirma con el preflight; NO implementado aún)
| Portal (`CONTRATO-DATOS-BLC` §2) | Gelsyn (columna de la BD) | Observación |
|---|---|---|
| cliente: `codigo_sap`, `nombre`, `activo`, `vendedor_codigo`, `zona`, `moneda` | `cardCode`, `cardName`, `finActivo`/`valid`/`frozen`, `salesPersonCode`, `uZona`, `currency` | zona: solo `uZona` (las `*Financiera` son nulas) |
| factura: `id_factura`, `numero`, `fecha_factura`, `fecha_vencimiento`, `total`, `aplicado`, `estado`, `moneda`, `tipo_documento` | `docEntry`, `docNum`, `docDate`, `docDueDate`, `docTotal`, `paidToDate`, `documentStatus`, `docCurrency`, `docType`/`uTipo` | excluir canceladas; valores de `documentStatus` por confirmar |
| recibo: `fecha_contable`, `monto`, `fecha_efectiva`? | `pagos.docDate`, `pagos.total`, `cancelled` | fecha efectiva/conciliación **no disponible** hoy |
| aplicación | `pagos_facturas`: `pagoDocEntry`, `facturaDocEntry`, `sumApplied`, `cancelado` | `facturaDocEntry` nulo ≠ aplicación inválida |
| nota de crédito | `notas_credito.facturaBaseEntry`, `docTotal` | no son pago |
| saldo de control | `clientes.currentAccountBalance` | no se recalcula |

## 6. Requisitos exactos para conectar el entorno de pruebas y validar contra SAP B1
**A pedir a Gelsyn (bloqueantes):**
1. Un **servicio de backend de pruebas** (URL HTTPS) conectado a `Cosprobell-pruebas`, separado de producción, con su propia API key y su `FINANZAS_APPS_AUTORIZADAS` (§4).
2. Una API key **dedicada, de solo lectura** y, si existe, con alcance limitado a `/finanzas/*` y `/health`; confirmación del nombre de la app autorizada y de su expiración.
3. Un ejemplo **real (sin valores sensibles) de la respuesta** de `GET /finanzas/{clientes,facturas,pagos,aplicacionesPagos,notasCredito}?limit=1` y de `/finanzas/estado`: nombres exactos de campos, valores de `documentStatus`, semántica de `cancelled`/`cancelStatus`, formato de fechas y de importes (¿cadenas decimales?).
4. Confirmar: ¿las fechas de documento son comerciales o instantes UTC?; ¿`docDate` del pago es la fecha contable?; ¿hay fecha de conciliación/aplicación?
5. Rotar la credencial `postgres` si se compartió por un canal no seguro.
**Por nuestra parte:** guardar la clave como en §4; ejecutar `python -m cxc_portal.gelsyn_preflight` (≤ 7 GET, sin imprimir datos); si el veredicto es `APTO_PARA_MAPEO`, implementar el transporte (`Transporte` del contrato) con el mapeo de §5 y pasarlo por `python -m cxc_portal.validar_fuente`.
**Validación contra SAP Business One (contabilidad, fuera del repo):**
1. Controles por cliente desde SAP B1: saldo de cuenta y reporte de antigüedad (`controles_sap.json`: `por_cliente`, `total_global`).
2. Comparar por cliente: `currentAccountBalance` (Gelsyn) vs saldo de cuenta SAP vs Σ(`docTotal − paidToDate`) de facturas abiertas; toda diferencia se lista (esperables: notas de crédito, anticipos, pagos sin aplicar).
3. Muestra contable de 6 casos (pagada, parcial, varias facturas, NC aplicada, pago no aplicado, saldo a favor) y las **24 cuentas de Las Vegas**, contra las tres consultas SAP del snapshot del 08/10 y contra el corte de pruebas del 04/11.
4. Criterio: sin diferencias sin explicar → aprobación humana de contabilidad. **Aun así, la BD de pruebas es un respaldo del 04/11: sirve para validar el mapeo y los datos, no para publicar rutas** (`saldo_fuente_validada` y `apta_para_publicar` siguen en falso hasta una fuente vigente).

## 7. Reserva: rol de solo lectura en PostgreSQL (SOLO propuesta para Gelsyn; no ejecutado por nosotros)
Si Gelsyn no puede levantar el backend de pruebas, un rol dedicado en la **BD de pruebas** (nunca en producción), creado por quien administra la BD:
```sql
CREATE ROLE portal_cxc_ro LOGIN PASSWORD '<generada y entregada por canal seguro>' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 3;
ALTER ROLE portal_cxc_ro SET default_transaction_read_only = on;
ALTER ROLE portal_cxc_ro SET statement_timeout = '30s';
GRANT CONNECT ON DATABASE <base_de_pruebas> TO portal_cxc_ro;
GRANT USAGE ON SCHEMA public TO portal_cxc_ro;
GRANT SELECT ON clientes, vendedores, condiciones_pago, facturas, facturas_cuotas, notas_credito, pagos, pagos_facturas,
                finanzas_control, finanzas_normalizacion TO portal_cxc_ro;      -- sin tablas de acceso, sesiones ni configuración
```
Y con la lógica de versión (`finVersion = activoId` y `finanzas_normalizacion`) reimplementada en el lector. Esta vía no está construida.

## 8. Qué se preparó en el repositorio
`cxc_portal/gelsyn_config.py` (configuración segura por variables de entorno, rechazo de URLs SQL, guardas pruebas/producción, redacción de la clave), `cxc_portal/gelsyn_preflight.py` (verificación mínima de solo lectura), `.env.example` (solo nombres), `.gitignore` (`.env*`, `*.pem`, `*.key`) y `tests/test_gelsyn_conexion.py` (24 pruebas con transporte falso: sin red ni credenciales reales).
