# Producción de Gelsyn en SOLO LECTURA — decisión y resultados (2026-11-09)

## Decisión de Alejandro
Se usa la URL de producción aunque su cuenta es `postgres` (administrador/superusuario), **con limitaciones**: el Portal solo puede **escribir** en las 26 tablas de su esquema `cxc` (usuarios, sesiones, auditoria · vendedores, rutas, zonas, clientes_ruta, folder_hwm, candidatos · decisiones · salidas, salida_resumen, visitas, visita_eventos, hojas_visita · documentos, salida_documentos, excel_rutas, cola_impresion, agentes_impresion · alertas, notificaciones, feriados, configuracion · meta, cambios) y **solo leer** las tablas financieras autorizadas. La publicación financiera sigue bloqueada. La URL vive en `~/.config/cxc/pg_produccion.url` (0600, fuera de Git, nunca impresa). Recomendación vigente: reemplazar la cuenta administrativa por un rol real de solo lectura/`cxc` en cuanto Gelsyn lo cree; las barreras de código no sustituyen un rol.

## Barreras (código, `cxc_portal/permisos_pg.py`)
Toda sentencia se clasifica antes de enviarse: lectura solo de tablas financieras autorizadas, catálogo y `cxc`; escritura (INSERT/UPDATE/DELETE) solo en las 26 tablas `cxc` y con `escritura=True` explícito; se rechazan DDL, GRANT, COPY, `public` en escritura, `asistente_*`, `api_keys`, `operadores`, varias sentencias, CTE con escritura y funciones peligrosas (72 pruebas, incluida la igualdad exacta de las 26 tablas con el esquema `cxc` de Gelsyn). Además el modo producción exige: atestación `CXC_GELSYN_PG_ATESTACION=PRODUCCION-SOLO-LECTURA`, sesión con `transaction_read_only=on` (se rechaza si no), identidad estructural (columnas y tipos) y solo lectura en el lector. Hoy el Portal **no escribe nada** en la base de Gelsyn: su persistencia sigue siendo SQLite.

## Hallazgos en producción (metadatos y agregados; sin datos de clientes)
* Cuenta: `postgres`, superusuario (por eso las barreras son de código). Sesión de solo lectura `on`.
* 73 tablas `public`: las 67 del SQL (**incluye `finanzas_actualizaciones`**: producción está por delante de la copia de pruebas) + `_prisma_migrations` + 5 `asistente_*` (no consultadas).
* **El esquema `cxc` NO existe en producción.** Crearlo es DDL, que las limitaciones no autorizan; mientras no exista, nada puede escribirse allí. Pendiente: que Gelsyn lo cree (o autorización expresa para que el Portal lo cree con el DDL del SQL recibido).
* Estructura crítica (63 columnas/tipos y 7 entidades publicadas en `finanzas_control`): **OK**.
* Más datos que la copia: facturas 17 344 (17 280), pagos 20 471 (20 407), aplicaciones 22 264 (22 146), notas de crédito 221 (219), movimientos 259 121 (258 179). Los cortes publicados son los mismos: clientes **27/10** (sigue desactualizado), aplicaciones 30/10, facturas y pagos 04/11, movimientos 04/11.
## Resultados con el Portal (rutas de prueba, 174 clientes; nombres reales de 1 170 clientes)
Fuente `EMPRESARIAL_PRODUCCION_NO_VALIDADA`: no viva, no publicable, calidad `CON_ADVERTENCIAS` (sin partidas/conciliaciones; cortes no simultáneos). Clasificación: A 43, B 32, C 50, Solo contado 21, En prueba 27, Revisión humana 1 (en la copia de pruebas B 31 y C 51: un cliente cambia). Conciliación saldo de control vs libro: **11 diferencias** (neto +21 870) frente a las 133 de la copia; con corte por `transId` quedan **5** sin explicar → requieren validación en SAP B1. Discrepancias vendedor/zona: mismas que en pruebas (Las Vegas mezcla 4 vendedores; Occidente 119/119 con un vendedor).

## Cómo se ejecuta
`CXC_FUENTE=gelsyn-pg-produccion CXC_GELSYN_PG_ATESTACION=PRODUCCION-SOLO-LECTURA CXC_PG_PRODUCCION_URL_FILE=~/.config/cxc/pg_produccion.url python -m cxc_portal.server --estado <estado aislado>`

## Pendiente
Rol real de Gelsyn; crear `cxc` en producción (decisión); refrescar `clientes`; conciliaciones/partidas; validación SAP B1 (incluye los 5 clientes sin explicar); cualquier publicación requiere tu autorización expresa.
