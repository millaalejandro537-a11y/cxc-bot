# HANDOFF — Bot de Rutas CxC (Distribuciones Cosprobell)

Checkpoint de cierre de sesión. Refleja el estado REAL del repositorio al
momento de escribirse (verificado con `git status`, `git log` y la suite de
pruebas, no solo la conversación).

**Último commit estable:** `719c0ddf878174010d0cc08163c74d5556f81257`
(`docs: manual operativo (seccion 32)`)
**`git status`:** working tree limpio, nada pendiente de commitear.
**Tests:** 211/211 pasan.

---

## 1. Estado actual del proyecto

Fases 1, 2 y 3 completas y estables. El motor de Excel, la orquestación
automática, el soporte de rutas reales/compuestas, el storage desacoplado,
el historial técnico + snapshots de negocio, y la infraestructura de n8n
están construidos, probados y documentados. **InSitu, SAP Service Layer y
WhatsApp siguen sin conectar, a propósito** (ver sección "Integraciones
pausadas" más abajo).

## 2. Qué está terminado

- Motor de Excel (generar/editar rutas, preservando fórmulas/formato/
  impresión), independiente de SAP.
- Orquestador (`process-due-routes`): detecta qué rutas están due,
  resuelve archivo base, genera, valida, publica, registra, notifica.
- Soporte para las 13 rutas operativas reales + ruta compuesta Comayagua.
- Catálogo oficial de vendedores/rutas, calendario laboral configurable,
  ventana de catch-up.
- Storage desacoplado (Local/Network) con publicación atómica y verificada.
- Historial técnico (SQLite) + snapshots de negocio inmutables (SQLite
  separada), con candado técnico DEV/TEST/PROD.
- Versionado de configuración.
- Consultas históricas básicas.
- n8n instalado vía Docker, workflow real creado y probado end-to-end.
- API HTTP local (puente entre n8n y el Bot).
- Documentación: README.md, MANUAL_OPERATIVO_CXC_BOT.md,
  docs/reporte_estructural_rutas.md.

## 3. Arquitectura actual

```
n8n (Docker, local)
  -> Cron (guardado, INACTIVO)
  -> HTTP Request -> api_server.py (host, fuera de Docker)
       -> runner.process_due_routes()
            -> schedule_source (LocalScheduleSource, DEV/TEST)
            -> catalog (VendorCatalog)
            -> business_calendar (dias habiles + timezone)
            -> file_resolver (localiza archivo base, nunca adivina)
            -> composite_config / composite_route_service (rutas compuestas)
            -> excel_service (motor: generar/validar/formulas)
            -> data_source (LocalMockDataSource, DEV/TEST)
            -> storage (RouteStorage: stage local -> publish atomico/verificado)
            -> history_store (historial tecnico, SQLite)
            -> business_history (snapshots de negocio, SQLite, inmutable)
            -> config_versioning (que config estaba vigente)
       <- JSON (mismo formato que el CLI)
  -> If (failed > 0) -> rama de alerta / rama de OK
```

Módulos en `cxc_bot/` (24 archivos): `models`, `text_utils`, `config`,
`catalog`, `business_calendar`, `data_source`, `route_engine`,
`excel_service`, `validators`, `file_resolver`, `composite_config`,
`composite_route_service`, `schedule_source`, `history_store`,
`business_history`, `business_queries`, `config_versioning`, `storage`,
`notifier`, `runner`, `api_server`, `cli`.

## 4. Tests: 211/211 pasan

```bash
cd "CXC BOT"
source .venv/bin/activate
python -m pytest tests/ -q
```

Cobertura: motor de Excel, catálogo, calendario (sábado hábil, timezone),
file_resolver (mes/año/día explícito, ambigüedad), rutas reales (las 14
estructuras reales parametrizadas), ruta compuesta Comayagua+Santa Cruz,
storage (local + network + fallos), runner (15 escenarios de Fase 2 + los
de Fase 3: catch-up, snapshots, storage failure, rutas múltiples por mes),
historial técnico, business history (inmutabilidad, candado prod/mock),
consultas históricas, API server real (servidor levantado en un thread).

## 5. Commits relevantes (más reciente primero)

```
719c0dd docs: manual operativo (seccion 32)
dac4be1 feat: consultas historicas basicas + n8n corriendo con workflow real
0b81534 infra: docker compose para n8n local (self-hosted, sin exposicion publica)
d21b80c feat: API HTTP local para que n8n invoque al Bot sin duplicar logica
d3c1639 feat: ventana de catch-up configurable (seccion 19)
825e551 feat: crear snapshot de negocio automaticamente al publicar (opt-in)
5db218a feat: versionado de configuracion + historial de negocio (snapshots)
a9cd382 feat: integrar rutas compuestas al runner
fa6275a feat: abstraccion de storage (Local/Network) integrada al runner
6abd3da feat: nombres con dia explicito + misma ruta varias veces por mes
33781fb fix: calendario laboral sabado-habil + zona horaria America/Tegucigalpa
4caa321 test: cubrir la ruta compuesta Comayagua+Santa Cruz con archivos reales
bccba58 Fase 3 (1/n): nombres oficiales, config de las 13 rutas reales + Comayagua compuesta
a98c6d5 Fase 2: orquestacion automatica (process-due-routes)
7b5e2d3 Fix: el Excel generado ahora se abre con las formulas ya calculadas
7302723 Fase 1: motor de Excel de rutas CxC independiente de SAP
```

(20 commits en total; `git log --oneline` para la lista completa.)

## 6. Docker / n8n

**Cómo levantarlo:**
```bash
cd "CXC BOT/n8n"
docker compose up -d
```
Abre `http://localhost:5678` (credenciales en `n8n/.env`, generadas con
`openssl rand`, NO están en git). Publicado solo en `127.0.0.1` — nunca
expuesto a la red ni a internet. Volumen nombrado `cxc_bot_n8n_data`
(persistente; sobrevivió una prueba real de `docker compose restart`).

**Detener:** `docker compose stop` (o `down`, el volumen persiste igual).

**Estado del workflow de n8n: GUARDADO PERO INACTIVO.** Existe en el
volumen (`"CxC Bot - Preparar rutas due"`, confirmado tras un restart real
del contenedor), con: Cron (diario 6am) → HTTP Request → If(`failed>0`) →
rama de alerta / rama de OK. Se ejecutó manualmente varias veces con
resultados reales. El toggle "Active" está apagado a propósito — no se
disparará solo hasta que alguien lo active desde la interfaz.

## 7. `api_server.py`

Servidor HTTP mínimo (solo librería estándar), puente entre n8n (Docker) y
el Bot (venv del host), porque el nodo Execute Command de n8n corre dentro
del contenedor y no tiene el venv del proyecto. Expone `GET /health`,
`POST /process-due-routes`, `GET /history`. Solo escucha en `127.0.0.1`.

**Estado:** en el último checkpoint de esta sesión estuvo corriendo como
proceso en segundo plano (`python -m cxc_bot.api_server &`), NO como
servicio del sistema — no arranca solo si se reinicia el Mac. Si al
retomar no responde `http://127.0.0.1:8765/health`, levantarlo de nuevo con
ese mismo comando. **No se configuró un LaunchAgent** (ver "No hacer
todavía").

## 8. RouteStorage

`cxc_bot/storage.py`: `RouteStorage` (interfaz) + `LocalRouteStorage` +
`NetworkRouteStorage`. Todo uso actual (DEV/TEST) es con `LocalRouteStorage`.
`NetworkRouteStorage` existe y tiene tests, pero **nunca se probó contra un
share SMB real** (no hay PC empresarial disponible todavía) — solo contra
carpetas locales simulando la disponibilidad/caída de un montaje. Publica
de forma atómica (temp + rename) y verifica el contenido antes de dar por
exitosa la publicación; nunca sobreescribe sin `force=True`.

## 9. Historial técnico

`cxc_bot/history_store.py` → `data/history.sqlite3` (excluido de git).
Registra cada intento (éxito, error u omitida) con: run_id, natural_key,
vendedor, ruta, fecha_salida, archivo origen/generado, fuente de datos,
cantidad de clientes, validaciones, error, duración. Consultar con
`python -m cxc_bot.cli history`.

## 10. Snapshots (historial de negocio)

`cxc_bot/business_history.py` → `data/business_snapshots.sqlite3`
(excluido de git). **Inmutables**: no existe ningún método para editar los
datos de un snapshot ya creado, solo `create_snapshot`, `replace_snapshot`,
`supersede_snapshot` (cambia estado) y `cancel_snapshot` (cambia estado). Un
índice único parcial en SQLite garantiza un solo snapshot `vigente` por
(vendedor, ruta, fecha_salida). Guarda por cliente: código, nombre,
posición, saldo, y `componente` (para rutas compuestas).

## 11. DEV/TEST/PROD

Cada snapshot lleva `environment` (`dev`/`test`/`prod`) y
`fuente_datos_es_mock`. **Candado técnico, no solo convención:** si se pide
`environment='prod'` con `fuente_datos_es_mock=True`, `create_snapshot` lo
degrada solo a `'dev'` y lo marca en el resultado
(`downgraded_from_prod=True`) — probado con datos reales en esta sesión.
Hoy TODO uso real es con `LocalMockDataSource` (mock), así que todo lo
generado hasta ahora es y debe seguir siendo `dev`/`test`, nunca `prod`.

## 12. Configuración de las 13 rutas

`config/routes.json` tiene 14 entradas: las 13 rutas operativas oficiales
(`CORTES`, `LAS VEGAS`, `LA LIMA`, `CHOLOMA`, `PROGRESO`, `OLANCHO`, `YORO`,
`ROATAN`, `LITORAL`, `OCCIDENTE`, `SANTA BARBARA`, `VILLANUEVA`,
`COMAYAGUA`) + `STA_CRUZ` (componente interno, no es ruta operativa
independiente). Cada una construida inspeccionando su Excel real — dos
familias de layout conviven (ver reporte estructural). Nota conocida: la
config de `LAS VEGAS` sigue con la estructura sintética de Fase 2
(`Hoja2`), no la real (`Hoja1`) — pendiente de reconciliar contra el
archivo de producción real cuando esté disponible.

## 13. Villanueva

Configurada (`config/routes.json` → `"VILLANUEVA"`, vendedor José Carpio),
probada contra `VILLANUEVA MAYO 2.xlsx` real
(`tests/test_real_routes_structural.py`) y con una generación completa en
vivo durante la demo final de esta sesión (junto con el candado de datos
SAP incompletos, que sí se disparó la primera vez con el mock reducido).

## 14. Comayagua + Santa Cruz de Yojoa

`config/route_composition.json` define la composición (genérica, no
hardcodeada): Comayagua = componente "Comayagua" (orden 1) + componente
"Santa Cruz de Yojoa" (orden 2). Cada mes el sistema resuelve y copia el
archivo más reciente de CADA componente por separado, arma un solo roster
(Comayagua primero, Santa Cruz al final), y genera con el motor existente.
Trazabilidad por cliente (`componente`) vive en los snapshots, no en el
Excel. Probado end-to-end con los archivos reales (54+16=70 clientes) tanto
llamando a `composite_route_service` directamente como a través del runner
completo. `vigente_desde` queda en `null` a propósito (ver "No hacer
todavía"). Ninguno de los dos archivos originales se modifica nunca.

## 15. Múltiples salidas en un mismo mes

La identidad de una ejecución es vendedor + ruta + **fecha_salida** (no
ruta + mes + año) desde Fase 2; la idempotencia respeta esto. El formato de
nombre con día (sección 16) fue necesario para que dos salidas del mismo
mes generen archivos distintos. Probado con dos viajes reales de Cortés en
octubre (día 8 y día 22) tanto en tests como en la demo en vivo.

## 16. Nombres de archivo actuales

Formato preferido para archivos nuevos (lo que genera el runner):
`[RUTA] [DD] [mes] [año].xlsx` (ej. `CORTES 08 octubre 2026.xlsx`).
`file_resolver.py` reconoce además, por compatibilidad hacia atrás:
`[RUTA] [mes].xlsx` (histórico, año inferido) y `[RUTA] [mes] [año].xlsx`
(histórico con año explícito). Una fecha más específica (con día) siempre
gana sobre una menos específica, nunca por `mtime`.

## 17. `MANUAL_OPERATIVO_CXC_BOT.md`

En la raíz del proyecto. Guía práctica para operar el sistema: qué
hace/no hace, cómo ejecutarlo (manual y vía n8n), dónde aparece cada
resultado, tabla de errores comunes, qué hacer si la Mac estuvo apagada o
la PC empresarial no responde, archivo bloqueado, consultar historial,
detener/reiniciar, backup/restore, cómo retomar InSitu y SAP, cómo
funcionan las rutas compuestas.

## 18. Reporte estructural de los 14 Excel

`docs/reporte_estructural_rutas.md`. Documenta, por archivo real: hoja
principal, celda de fecha, fila de encabezado, columnas, fila de total,
particularidades (Choloma trae dos versiones en pestañas distintas;
Litoral sin factura/observación; Progreso/Olancho corridos una fila). Los
14 originales están copiados de solo lectura en
`tests/fixtures/rutas_reales/` (rastreados en git) y se verificaron
byte-idénticos al original en `Documents/Cosprobell/Rutas de Cosprobell/`
al cierre de esta sesión.

## 19. Deuda técnica conocida

- `NetworkRouteStorage` sin probar contra un share SMB real.
- Config de `LAS VEGAS` con estructura sintética de Fase 2, no la real.
- `api_server.py` no es un servicio del sistema (no sobrevive un reinicio
  del Mac sin relanzarlo a mano).
- No hay CLI dedicado para `business_queries.py` (se usa importando el
  módulo desde un script/sesión de Python).
- `vigente_desde` de la composición Comayagua sin definir (ver abajo).

---

## INTEGRACIONES PAUSADAS

- **InSitu**: PAUSADO por decisión de gerencia mientras técnicos/
  configuración de InSitu terminan.
- **SAP Service Layer**: PAUSADO hasta que el experto esté disponible.
- **WhatsApp**: fuera del alcance actual.

No intentar conectar ninguna de ellas al retomar sin nueva autorización.

## NO HACER TODAVÍA

- No activar automáticamente el workflow de n8n.
- No configurar LaunchAgent todavía.
- No conectar InSitu.
- No conectar SAP.
- No usar históricos DEV/TEST como PROD.
- No inventar `vigente_desde` de Comayagua + Santa Cruz de Yojoa.

## PRÓXIMO PASO CUANDO SE RETOME CXC BOT

**Si InSitu sigue pausado:**
- No desarrollar más el Bot de Rutas salvo correcciones necesarias.
- Mantenerlo congelado y documentado.

**Cuando gerencia autorice InSitu:**
- Retomar desde `InsituScheduleSource` (`cxc_bot/schedule_source.py`).
- Verificar primero la configuración real de InSitu.
- Mantener acceso estrictamente de lectura.
- Sustituir la fuente DEV/TEST sin reconstruir el runner.

**Cuando SAP esté disponible:**
- Retomar desde el DataSource desacoplado (`cxc_bot/data_source.py`).
- Usar únicamente Service Layer oficial y lectura inicialmente.
