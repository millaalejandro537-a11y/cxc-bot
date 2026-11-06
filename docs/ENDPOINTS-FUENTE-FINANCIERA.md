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
