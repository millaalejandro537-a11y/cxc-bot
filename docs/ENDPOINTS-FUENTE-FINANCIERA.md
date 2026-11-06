# Endpoints de la fuente financiera (Portal CxC ↔ PostgreSQL de pruebas) — 2026-11-06

Una sola API (la existente, `cxc_portal/app.py`). La lectura financiera es PostgreSQL **solo lectura** (`fuente_pg_pruebas.py`, sesión READ ONLY, SELECT validados contra la lista cerrada de `inspeccion_pg_pruebas`); la persistencia operativa sigue en SQLite (`Estado`). Estructura de referencia: `docs/gelsyn/cosprobell_esquema_completo.sql` (solo documentación, no se ejecuta).

## Qué se revisó y qué cambió
| Endpoint | Decisión |
|---|---|
| `/api/rutas`, `/api/rutas/{id}/clientes`, `/api/clientes/{c}`, `/api/dashboard`, `/api/alertas`, `/api/prioridades`, `/api/salidas*`, reportes | **Sin cambios**: ya consumen `FuenteDatos`; funcionan con la fuente PG (174/174 clasificaciones verificadas). |
| `GET /api/fuente` | **Ampliado**: para admin/gerencia añade `detalle` (cortes por entidad, exclusiones, advertencias, motivo de no publicable, lecturas PG). Vendedor no lo ve. |
| `POST /api/fuente/recargar` (admin/gerencia, CSRF) | **Nuevo**: vuelve a leer PG sin reiniciar. Una recarga fallida conserva la última carga buena (`DEGRADADA`), no filtra la URL, invalida cachés por `version_datos`, una sola recarga a la vez, auditada (`fuente.recargar`). 409 si la fuente no admite recarga. |
| `GET /api/fuente/conciliacion` (admin/gerencia) | **Nuevo**: saldo de control (`clientes.currentAccountBalance`) vs libro (`movimientos_contables`): diferencias, corte por fecha y por `transId`, lista de clientes con diferencia (códigos e importes). Siempre `validado_contablemente=false`. Auditada. |
No se creó otra API ni otro servicio; autenticación, roles (RBAC), CSRF y auditoría son los existentes. Nada habilita publicar: la fuente sigue `EMPRESARIAL_PRUEBAS`, `apta_para_publicar=false`.

## Consultas (esquema `public`, solo lectura)
`clientes` (cardCode, salesPersonCode→vendedor, uZona→ruta; **sin nombres**), `facturas`, `pagos`, `pagos_facturas`, `finanzas_control` (cortes `publicadoEn`), `movimientos_contables` (agregado por cliente/transId). Siempre filtradas por la versión publicada (`finVersion = activoId`).

## Hallazgo del 6/11 (identidad)
La base pasó de 67 a 72 tablas `public`: Gelsyn agregó `asistente_*` (asistente de Telegram, ajeno al Portal; **no se consultan**) y no existe `finanzas_actualizaciones`. Los conteos de las 15 financieras no cambiaron. La verificación de identidad dejó de exigir exactamente 67 tablas (ahora ≥ 60) y se apoya en los conteos exactos.

## Pruebas
`tests/test_endpoints_fuente_pg.py` (8: permisos, detalle, recarga, recarga fallida, conciliación, 409, vendedor/zona), `tests/test_fuente_pg_pruebas.py`, `tests/test_investigacion_saldos_pg.py`; verificado además contra la copia real: recarga OK, conciliación = 133 diferencias sin corte y 0 con corte `transId < 85 024`.

## Sigue dependiendo de otros
* **Gelsyn**: refrescar `clientes` al mismo corte que `movimientos` y publicarlo en `finanzas_control`; conciliaciones/partidas, pagos sin aplicar/anticipos; rol `portal_cxc_ro` (hoy se usa `postgres`: no apto para producción) o backend HTTPS con llave solo GET; confirmar que `publicadoEn` está en UTC.
* **SAP B1 / contabilidad**: validar saldos (56 clientes con saldo a favor o recibos sin aplicar), que el libro de pruebas coincida con SAP, y autorizar cualquier publicación.
* **Decisión de Alejandro**: leer nombres de cliente (hoy "SIN NOMBRE"), vendedor por nombre (hoy solo código) y si el reporte gerencial usa vendedor/zona financieros en vez del catálogo del Portal.

## Ampliación (2026-11-06, 2): nombres, discrepancias, identidad estructural y `finanzas_actualizaciones`
**Nombres.** La fuente PG lee `clientes.cardName` y `vendedores.salesEmployeeName` (tablas autorizadas; nunca teléfono, correo ni dirección). Llegan al Portal por los mecanismos y permisos existentes: `nombre_cliente` alimenta las listas de la ruta (un vendedor solo ve las suyas; un vendedor ajeno recibe 404) y el vendedor financiero se resuelve a nombre en `GET /api/fuente/discrepancias` (solo admin/gerencia). Verificado con la copia real: 174 de 174 clientes de las 3 rutas de prueba con nombre real. Los nombres no se escriben en Git, documentos ni informes.

**`GET /api/fuente/discrepancias` (admin/gerencia, auditado, solo lectura).** Compara, por ruta, la zona (`uZona`), el vendedor y el estado financieros con el catálogo operativo; **no modifica rutas, zonas, vendedores ni clientes** (prueba explícita). Informa clientes ausentes de la fuente, vendedor/zona que difieren del predominante, inactivos y si el vendedor predominante coincide con el de la ruta. Con la copia real (rutas de prueba): Occidente 119/119 con un mismo vendedor financiero y 117/119 en una zona financiera; Las Vegas mezcla 4 vendedores (14/5/3/2) y 3 zonas; Yoro 30/31 con un vendedor y 4 zonas. Límites: el estado de prueba tiene una sola zona operativa por ruta (los prefijos reales viven en el catálogo de Windows) y las zonas financieras 01–04 son territorios amplios, así que la zona es indicativa; la decisión de corregir es de gerencia.

**Identidad estructural.** Además de los conteos exactos de las 15 tablas financieras, ahora se verifica (`inspeccion_pg_pruebas.verificar_estructura`): que existan las 63 columnas que el Portal lee en 8 tablas **con su tipo** (`ESTRUCTURA_CRITICA`, contrastada por prueba contra `docs/gelsyn/*.sql`) y que `finanzas_control` publique versión (`activoId`) de las 7 entidades usadas. Un cambio de columna, tipo o versión detiene la carga con un mensaje que solo nombra la columna. Las 261 columnas de las 15 tablas financieras vivas son idénticas a las del SQL de Gelsyn.

**Por qué falta `finanzas_actualizaciones`** (solo lectura, sin crear nada): en el SQL es un registro de idempotencia de actualizaciones financieras (`operacionId` PK, `empresa`, `hash`, `respuesta jsonb`, `creadaEn`). En la base viva no existe en ningún esquema (ni vista ni tabla de nombre parecido; ninguna otra tabla tiene `operacionId`+`hash` equivalentes). `_prisma_migrations` (solo nombre y fechas, sin logs) muestra **30 migraciones aplicadas** (todas terminadas, ninguna revertida), la última `20261104090000_conteos`, mientras el SQL declara **32** migraciones: la base va dos migraciones por detrás del SQL y la que crea esa tabla no se ha aplicado. Además la base tiene 5 tablas `asistente_*` que el SQL no trae y que no aparecen en ninguna migración de Prisma. Conclusión: SQL y base viva son versiones distintas del backend. Impacto en el Portal: ninguno (solo lee; la tabla es del flujo de escritura/sincronización de Gelsyn). Hay que pedir a Gelsyn qué migraciones faltan y confirmar que el SQL corresponde a su siguiente despliegue.
