# Esquema de Gelsyn (`cosprobell_esquema_completo.sql`) vs. persistencia del Portal — 2026-11-05

Fuente: el SQL se **leyó como texto** (no se ejecutó) y se contrastó con consultas de solo lectura a `Cosprobell-pruebas` (solo existencia, conteos y metadatos no sensibles; jamás `usuarios`, `sesiones` ni `agentes_impresion`).

## Diferencias reales
| Tema | Hallazgo |
|---|---|
| Tablas `cxc` | 26 de 26 coinciden por nombre con las tablas SQLite de `Estado`. |
| Columnas | 229 de 229 idénticas en las 26 tablas (0 diferencias). Tipos: TEXT→text (168), INTEGER→bigint (54), REAL→double precision (5), 2 columnas `citext` (`usuarios.usuario`, `vendedores.nombre`: igualdad sin mayúsculas). |
| Índices / triggers | Los 2 índices coinciden. Los 15 triggers SQLite del contador `cambios` pasan a 5 triggers plpgsql. Nuevo en Gelsyn: `cxc_finanzas_cambio` en `public.finanzas_control` (`pg_notify('cxc_finanzas')`) para avisar al Portal cuando cambia una carga financiera; el Portal aún no escucha. |
| Código | **Este repositorio no tiene persistencia PostgreSQL** (solo SQLite, `sqlite3` en estado/admin/server/respaldos). El Portal que corre en Gelsyn es una variante con la capa de estado portada a PG; **ese código no lo tenemos**. |
| Versión | `meta.schema_version = 1` (igual que `SCHEMA_VERSION`). El esquema `cxc` equivale al de nuestro HEAD; `estado.py` no cambia desde el 2026-10-12 (`de02e28`), así que no se puede fijar el commit exacto que ejecutan. |
| Capturas | No están en disco (se mostraron en el chat), no las vi. La evidencia en la BD sí es de nuestra aplicación: acciones de auditoría `ruta.crear`, `usuario.crear`, `login.ok`, `usuario.cambiar_password`. |

## Qué contiene `cxc` hoy (conteos)
rutas 1 (`ZONA_04`, FORANEA, creada 2026-11-05 20:08Z), clientes_ruta 50 (sin zona), vendedores 1, auditoria 6, cambios 5, meta 1; las demás 17 tablas, vacías. Es decir: existe y se usó una vez para una prueba de Gelsyn; **no hay decisiones, salidas, visitas ni Excel**. Nosotros no escribimos nada allí.

## Información contable (existencia de tablas ≠ datos completos)
* Las 67 tablas `public` son las mismas: **no existen** tablas de conciliaciones, partidas abiertas, estado de sincronización por documento ni aplicación de notas de crédito (`notas_credito.facturaBaseEntry` sí enlaza la factura base).
* Sí hay libro auxiliar: `movimientos_contables` (258 179 líneas, debe/haber, vencimiento, tipos `ttInternalReconciliation` 14 241, `ttARCredItnote`, `ttReceipt`…). Σ(debe−haber) por cliente coincide con `currentAccountBalance` en **1 025 de 1 158** clientes (133 difieren; sumas 2 340 480,10 vs 2 527 188,10). Es una vía para **verificar o reconstruir saldos**, no es una partida abierta ni una conciliación (no hay ReconNum ni vínculo línea↔línea).
* `finanzas_control` ahora da cortes reales por entidad (clientes 27/10, aplicaciones 30/10, facturas y pagos 04/11): resuelve "estado de sincronización" parcialmente.
* Siguen faltando: conciliaciones/partidas, pagos sin aplicar y anticipos explícitos, fecha efectiva, validación contable en SAP B1. Nada de esto se marca como conciliado.

## Camino mínimo a Railway
1. **Decidir la persistencia**: (a) Railway como en `railway.toml` hoy (1 réplica, SQLite en volumen; ya probado) y PG solo para lectura financiera; o (b) estado en `cxc` de PG: pedir a Gelsyn el código de su variante y revisarlo, o implementar un `EstadoPG` con el mismo esquema (≈140 `execute` en estado/estado_flujo/admin + respaldos). Recomendado: (a) para el primer despliegue.
2. **Roles**: dos roles, no `postgres`: `portal_cxc_ro` (SELECT en tablas financieras) y, solo si se elige (b), `portal_cxc_rw` (CRUD en esquema `cxc`, sin acceso a escribir `public`). Nunca superusuario.
3. **Lectura financiera**: backend HTTPS con llave solo GET (preferido) o rol SELECT; definir recarga (reinicio hoy; `pg_notify` más adelante).
4. **Validación contable** de saldos con SAP B1 (24 cuentas de Las Vegas + 6 casos) y decisión sobre importar conciliaciones/partidas: bloquea publicar saldos.
5. Hosting Railway independiente de BLC, secretos solo en variables de entorno, usuarios reales con 2FA, respaldos cifrados probados.
