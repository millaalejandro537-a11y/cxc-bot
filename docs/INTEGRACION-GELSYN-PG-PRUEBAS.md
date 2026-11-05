# Portal ↔ `Cosprobell-pruebas` (PostgreSQL): lectura automática en entorno aislado

Estado: **funciona solo en pruebas**. Nunca apta para publicar; no marca saldos como conciliados; no escribe en PG, Gelsyn ni SAP.

## Qué hace
`CXC_FUENTE=gelsyn-pg-pruebas` hace que el servidor del Portal (el mismo, sin segunda API) cargue al arrancar la copia de pruebas:
`inspeccion_pg_pruebas` (sesión READ ONLY, SELECT validados, identidad por 67 tablas y conteos) → `fuente_pg_pruebas` (clientes, facturas, pagos, aplicaciones y cortes reales `publicadoEn` de `finanzas_control`) → `TransporteGelsyn` → `FuenteEmpresarial` → motor único de semáforo → API/UI existentes. La conexión se cierra tras la carga; reiniciar = releer. No se lee `cardName` ni datos personales (los clientes aparecen por código).

## Cómo se ejecuta (estado aislado, fuera de Git)
```bash
python -m cxc_portal.preparar_pruebas_gelsyn --estado ~/Developer/portal-gelsyn-pruebas          # 3 rutas con códigos aprobados, sin nombres
python -m cxc_portal.admin crear-usuario --estado ~/Developer/portal-gelsyn-pruebas --usuario pruebas --rol admin --generar-clave
CXC_FUENTE=gelsyn-pg-pruebas CXC_GELSYN_PG_ATESTACION=Cosprobell-pruebas CXC_PG_PRUEBAS_URL_FILE=~/.config/cxc/pg_pruebas.url \
CXC_REPORTING_SRC=<cxc-reporting-core>/src python -m cxc_portal.server --estado ~/Developer/portal-gelsyn-pruebas
```

## Permisos (verificado en la copia, solo lectura)
La URL de pruebas usa `postgres`: **superusuario**, con INSERT/UPDATE/DELETE sobre las tablas. Lo único que impide escribir es la sesión (`default_transaction_read_only=on` + transacción READ ONLY + lista cerrada de SELECT). **No es aceptable como acceso permanente ni de producción.** Rol propuesto que debe crear Gelsyn (no ejecutado por nosotros):
```sql
CREATE ROLE portal_cxc_ro LOGIN PASSWORD '<la define Gelsyn>' NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER ROLE portal_cxc_ro SET default_transaction_read_only = on;
GRANT CONNECT ON DATABASE <base> TO portal_cxc_ro;  GRANT USAGE ON SCHEMA public TO portal_cxc_ro;
GRANT SELECT ON clientes, facturas, pagos, pagos_facturas, finanzas_control TO portal_cxc_ro;   -- y las tablas nuevas de docs/PETICION-TABLAS-GELSYN.md
```
En producción la vía prevista sigue siendo el backend HTTPS con llave solo GET (`TransporteGelsyn`); esta lectura directa es un puente de pruebas.

## Resultados (2026-11-05)
* Servidor real arrancado con la copia: fuente `EMPRESARIAL_PRUEBAS`, `en_vivo=false`, calidad OK, corte 2026-11-04, cortes por entidad reales (clientes 27/10, aplicaciones 30/10, facturas y pagos 04/11) declarados como no simultáneos.
* Por HTTP autenticado: `/api/fuente`, `/api/rutas` (24 + 31 + 119), `/api/rutas/{id}/clientes`, `/api/clientes/{codigo}`, `/api/dashboard`, `/api/alertas`, `/api/prioridades`, `/api/avisos`, `/api/calendario` responden 200.
* **174/174** clasificaciones del Portal coinciden con la lectura directa al 04/11 (A 43, B 31, C 51, SOLO_CONTADO 21, EN_PRUEBA 27, REVISION_HUMANA 1).
* Crear y preparar una salida (LAS_VEGAS): estado `preparada` con avisos de cobro; no se genera ni publica ningún Excel.
* Regresión completa: 767 pasan, 18 omitidas (una omitida es la prueba opcional contra la copia real, que también pasó con credencial local).

## Límites
Sin partidas ni conciliaciones el saldo no está certificado; los nombres no se leen; las alertas muestran "SIN NOMBRE (no se lee)" porque la fuente lo trae así; el Excel sigue en BORRADOR; los textos de `capacidades` aún dicen "snapshot".
