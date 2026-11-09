# Portal CxC (Cosprobell) — resumen de la sesión de integración con Gelsyn (2026-11-05 → 2026-11-09)

> Texto para dar contexto a otro asistente. No contiene credenciales, URLs de base de datos, códigos ni nombres de clientes.

## 1. Proyecto y reglas
* **Portal CxC**: backend Python (stdlib) + frontend React (`portal-web/`), estado propio en SQLite, **un solo motor de semáforo** (`cxc_bot/payment_semaphore.py`: clases A/B/C/SOLO_CONTADO/EN_PRUEBA/REVISION_HUMANA, ventana de 10 facturas, plazo 60 días). Repo: `~/Developer/cxc-bot-v2`, rama `feat/fuente-gelsyn-conexion` (sobre `main` = `9e7dd56`). **No se toca** `Trabajo-Sync/CXC BOT` ni clones viejos.
* Reglas permanentes de Alejandro: sin push/merge/deploy sin autorización; no imprimir ni guardar contraseñas/URLs en Git, logs, docs o informes; no activar saldos, viñetas ni reportes como definitivos; no inventar endpoints; Excel siempre BORRADOR y `apto_para_publicar=False` mientras la fuente no esté validada contablemente (SAP B1).

## 2. Qué se construyó (commits locales → ya en GitHub)
1. **Transporte Gelsyn** (`transporte_gelsyn.py`): lee `GET /finanzas/{clientes,facturas,pagos,aplicacionesPagos}` con los nombres del PDF de Gelsyn (04/11/2026): importes decimales exactos, `cancelado` Y/N/C, paginación con cursor y reinicio si cambia la versión, cortes por entidad, pagos sin `total` (se derivan de efectivo+transferencia), exclusiones contadas. Nunca publicable (`apto_para_publicar=False`).
2. **Fuente empresarial** (`fuente_empresarial.py`): validación (tolerancia 2 % de filas inválidas), vigencia dinámica, tipos `EMPRESARIAL_PRUEBAS` y `EMPRESARIAL_PRODUCCION_NO_VALIDADA`; nunca `en_vivo`.
3. **Lectura PostgreSQL de solo lectura** (`inspeccion_pg_pruebas.py`, `fuente_pg_pruebas.py`): sesión READ ONLY, lista cerrada de SELECT validados, URL desde archivo 0600, atestación obligatoria, identidad por conteos (pruebas) y **estructural** (63 columnas con tipo + versiones publicadas en `finanzas_control`). Lectura recargable sin reiniciar.
4. **Endpoints sobre la API existente** (sin segunda API): `GET /api/fuente` (+`detalle`), `POST /api/fuente/recargar`, `GET /api/fuente/conciliacion` (saldo de control vs libro), `GET /api/fuente/discrepancias` (zona/vendedor financieros vs rutas operativas; **informativo, no modifica rutas**). Todos con RBAC (admin/gerencia), CSRF y auditoría.
5. **Nombres reales** de clientes y vendedores desde tablas autorizadas (nunca teléfono/correo), mostrados por los permisos de ruta ya existentes.
6. **Modo producción en solo lectura** (`CXC_FUENTE=gelsyn-pg-produccion`) con `permisos_pg.py`: toda sentencia se clasifica antes de enviarse; escritura permitida **solo** en las 26 tablas del esquema `cxc` (y exige habilitarla explícitamente; hoy no se usa); se rechazan DDL, GRANT, COPY, escrituras en `public`, tablas `asistente_*`, etc. Sesión debe ser `transaction_read_only=on`.
7. Herramientas de investigación: `investigacion_saldos_pg.py` (control vs libro), `corrida_en_seco_gelsyn.py`, `preparar_pruebas_gelsyn.py` (estado aislado con las 3 rutas aprobadas por códigos).
8. Documentación: `docs/MATRIZ-CONTRATO-GELSYN.md`, `PETICION-TABLAS-GELSYN.md`, `ANALISIS-ESQUEMA-GELSYN-CXC.md`, `INVESTIGACION-SALDOS-CONTROL-VS-LIBRO.md`, `ENDPOINTS-FUENTE-FINANCIERA.md`, `PRODUCCION-SOLO-LECTURA.md`, `docs/gelsyn/cosprobell_esquema_completo.sql` (solo estructura).
* **Pruebas**: regresión completa 866 pasan / 18 omitidas (antes de la sesión: 749 / 17).

## 3. Hallazgos verificados (solo lectura)
* La copia de pruebas coincidía **174/174** en clasificación entre el camino HTTP simulado y la lectura directa.
* **Esquema `cxc` de Gelsyn = nuestras 26 tablas SQLite** (229/229 columnas idénticas; tipos text/bigint/double precision y 2 `citext`). Gelsyn ejecuta una variante PG de la capa de estado que no tenemos; nuestro repo solo tiene SQLite.
* **Saldo de control vs libro auxiliar** (`movimientos_contables`): en la copia, 133 diferencias (neto −186 708,00) **totalmente explicadas por desfase de cortes**: cortando el libro por orden de creación (`transId` < ~85 024, no por fecha, porque hay asientos retrofechados) coinciden 1 169/1 169 clientes. La moneda no influye.
* Documentos vs libro: 1 109/1 169 coinciden por facturas; 56 clientes distintos (saldos a favor / recibos sin aplicar) → validar en SAP B1.
* **`finalizadoEn/publicadoEn` de `finanzas_control` es la última carga completa**; las filas se actualizan de forma incremental (`sincronizadoEn`). El corte real = `max(publicadoEn, última sincronización)`.
* `finanzas_actualizaciones` faltaba en pruebas porque la base tenía 30 de 32 migraciones; **producción sí la tiene**. Hay 5 tablas `asistente_*` (asistente de Telegram, ajenas, nunca consultadas).
* **Producción** (cuenta `postgres`, decisión de Alejandro con límites): 73 tablas `public`; estructura crítica OK; mismos cortes de carga completa; más datos que la copia; 11 diferencias control vs libro (5 sin explicar → SAP B1). El esquema `cxc` apareció después, con las 26 tablas vacías y **sin triggers**.
* Sin conciliaciones, partidas abiertas, pagos sin aplicar/anticipos ni aplicación de notas de crédito en ninguna de las dos bases.

## 4. Decisiones de Alejandro
* No rotar la contraseña de `postgres`; usar la URL de **producción** pese a ser `postgres`, con límites de escritura (26 tablas `cxc`), **solo lectura** hoy y publicación bloqueada. Gelsyn dijo que el rol de solo lectura no es necesario. El Portal sigue con **SQLite** por ahora (sin migrar a `cxc` en PostgreSQL).
* Se probó el Portal en local (Safari) sin contraseña mediante un puente local solo en `127.0.0.1:8781` (fuera del repo), con estado aislado en `~/Developer/portal-gelsyn-pruebas`.

## 5. Pendiente / bloqueos reales
1. **Gelsyn**: conciliaciones/partidas, pagos sin aplicar y anticipos, marca de sincronización para `aplicacionesPagos` y `movimientos`; triggers del esquema `cxc` si se usa; backend HTTPS con llave solo GET (preferido a largo plazo); confirmar UTC de `publicadoEn`.
2. **SAP B1 / contabilidad**: validar saldos (56 clientes con saldo a favor, 5 con diferencia sin explicar), que el saldo de control sea el contable; **autorizar cualquier publicación**.
3. **Decisiones**: dónde vive el estado en Railway (SQLite en volumen vs `cxc` en PostgreSQL, que exigiría una capa `EstadoPG` ≈140 sentencias); si el reporte gerencial usa vendedor/zona financieros; corregir (o no) discrepancias de zona/vendedor.
4. **Camino a Railway**: despliegue con `railway.toml` (1 réplica, SQLite en volumen), secretos solo en variables de entorno, usuarios reales con 2FA, respaldos cifrados probados.
