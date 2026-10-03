# Bot de Rutas CxC — Distribuciones Cosprobell (Fase 1)

Motor que automatiza la preparacion mensual de los Excel de rutas de cobro,
sin depender todavia de una conexion real a SAP. Cortes es la ruta piloto.

## 1. Que hace

Dado el Excel de ruta del mes anterior y una tabla de clientes (hoy: un
archivo local que simula la extraccion de SAP; manana: SAP Business One
Service Layer), genera el Excel del nuevo mes:

- copia el archivo base sin tocarlo nunca,
- le pone el nombre y la fecha del nuevo mes,
- reemplaza por completo los datos de `Hoja2` (la tabla SAP) por la nueva,
- deja la columna `Observacion` vacia (no hereda observaciones del mes anterior),
- conserva formulas, formato, anchos de columna, alturas de fila, bordes,
  combinaciones de celdas y configuracion de impresion,
- opcionalmente aplica agregar / eliminar / mover / reordenar clientes en la
  misma corrida,
- **calcula y deja grabado el resultado de cada formula** (nombre, saldo,
  total) para que el archivo se vea listo para imprimir apenas se abre, sin
  que nadie tenga que forzar un recalculo,
- valida automaticamente el resultado antes de darlo por bueno — incluyendo
  que los valores calculados existan y sean correctos, no solo que la formula
  este escrita.

Tambien permite editar la lista de clientes de una ruta ya generada
(`edit-route`) sin tener que regenerar el mes completo.

## 2. Arquitectura

```
data_source.py   -> de donde vienen los clientes (hoy: LocalMockDataSource,
                     manana: SapServiceLayerDataSource). Devuelve SapClientRecord.
route_engine.py  -> logica pura de agregar/eliminar/mover/reordenar clientes,
                     identificados SIEMPRE por codigo, nunca por numero de fila.
                     No sabe nada de Excel.
excel_service.py -> toda la manipulacion real del archivo (openpyxl): copia
                     segura, reescritura del bloque de clientes, reemplazo de
                     Hoja2, preservacion de estilos.
validators.py    -> validaciones automaticas post-generacion.
config.py        -> carga la configuracion de cada ruta desde config/routes.json.
models.py        -> dataclasses compartidas (ClientEntry, SapClientRecord,
                     RouteConfig, GenerationResult).
cli.py           -> interfaz de linea de comandos (subcomandos generate-route
                     y edit-route), pensada para que n8n la invoque despues.
```

Flujo actual:

```
archivo local (mock SAP) -> data_source -> route_engine -> excel_service -> Excel final
```

Flujo futuro (sin tocar route_engine ni excel_service):

```
SAP Business One Service Layer -> data_source -> route_engine -> excel_service -> Excel final
```

### Por que el motor reescribe el bloque de clientes en vez de "parchar" filas

Se verifico empiricamente que openpyxl mueve el *contenido* de las celdas al
insertar/eliminar filas, pero **no** reescribe el texto de las formulas ni
conserva estilo/alto de las filas nuevas o desplazadas. Por eso, cada vez que
cambia la cantidad de clientes, el motor:

1. Ajusta la cantidad de filas fisicas (`insert_rows`/`delete_rows`), lo que
   mueve la fila de "Total Saldo Ruta" a su nueva posicion.
2. Reescribe explicitamente cada fila del bloque (numero, codigo, formulas,
   No. Factura, Observacion) con una plantilla de estilo/alto capturada del
   archivo original antes de tocar nada.

Esto garantiza que ninguna formula quede apuntando a la fila equivocada y que
el formato quede identico sin importar cuantos clientes se agreguen o quiten.

### Por que el archivo se abre ya calculado (sin "Actualizar")

openpyxl nunca calcula formulas: un archivo recien escrito por openpyxl guarda
`=VLOOKUP(...)` con su valor en cache vacio. La mayoria de las veces Excel
recalcula solo al abrir, pero no todos los visores lo hacen, y el encargo
exige que el archivo se vea listo para imprimir sin que nadie tenga que
forzar un recalculo manual. La solucion NO fue instalar LibreOffice (no
estaba disponible en esta maquina) ni usar una libreria generica de
recalculo (se probo la libreria `formulas`: calcula perfecto, pero su
escritor de Excel destruye todo el formato — sin combinaciones de celdas,
sin anchos de columna, fuente por defecto). En su lugar, `excel_service.py`:

1. Calcula el resultado real de cada VLOOKUP/SUM con la misma logica de
   Excel (coincidencia exacta contra Hoja2; `#N/A` si el codigo no existe; el
   total se vuelve `#N/A` si algun saldo lo es), usando los mismos datos que
   ya tiene en memoria (`compute_cached_values`).
2. Inyecta ese resultado directamente en el `<v>` de cada celda de formula,
   dentro del XML ya guardado por openpyxl (`inject_cached_formula_values`),
   sin tocar ninguna otra parte del archivo — por eso el formato queda
   exactamente igual.
3. Ademas deja `fullCalcOnLoad=1` en las propiedades de calculo del libro,
   como respaldo, para que Excel real igual recalcule si algo cambiara.

Esto se verifico de dos formas: (a) leyendo el archivo con
`openpyxl(data_only=True)`, que es exactamente lo que cualquier programa lee
sin recalcular, y (b) con una prueba cruzada independiente usando la libreria
`formulas` para evaluar el archivo de verdad y comparar contra los valores
inyectados (`tests/test_formula_crosscheck.py`).

## 3. Estructura de carpetas

```
cxc_bot/            codigo del motor (paquete Python)
config/routes.json  configuracion por ruta (Cortes es la piloto)
tests/              pruebas automatizadas (pytest)
samples/            copia de trabajo del Excel real + un mock SAP de ejemplo
output/             archivos generados por las corridas (nunca el original)
logs/               log de cada corrida (cxc_bot.log)
```

## 4. Como se ejecuta

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Generar la ruta de un nuevo mes

```bash
python -m cxc_bot.cli generate-route \
  --route CORTES \
  --base "samples/CORTES septiembre.xlsx" \
  --month octubre \
  --date 2026-10-13 \
  --data "samples/sap_mock_octubre.xlsx" \
  --output-dir output
```

Con edicion de clientes en la misma corrida:

```bash
python -m cxc_bot.cli generate-route \
  --route CORTES --base "samples/CORTES septiembre.xlsx" \
  --month octubre --date 2026-10-13 --data "samples/sap_mock_octubre.xlsx" \
  --output-dir output \
  --add "C99500:after:C10642" \
  --remove "C10932" \
  --move "C10755:after:C10621"
```

### Editar clientes de una ruta ya generada

```bash
python -m cxc_bot.cli edit-route \
  --route CORTES \
  --input "output/CORTES octubre.xlsx" \
  --add "C12345:after:C10642" \
  --remove "C11098" \
  --move "C10755:after:C10621"
```

Por defecto escribe en `<input>_editado.xlsx` (nunca sobre el archivo de
entrada). Usa `--output` para elegir otro nombre.

Para operaciones mas complejas (o para que n8n las genere programaticamente)
se puede pasar un archivo JSON con `--ops-json operaciones.json`:

```json
[
  {"op": "add", "codigo": "C12345", "after_codigo": "C10642"},
  {"op": "remove", "codigo": "C11098"},
  {"op": "move", "codigo": "C10755", "after_codigo": "C10621"},
  {"op": "reorder", "codigos": ["C10641", "C10642", "..."]}
]
```

Agregar `--json` a cualquier comando devuelve el resultado y el reporte de
validacion en JSON por stdout, listo para que lo consuma otro programa.

### Ejecutar las pruebas

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt  # agrega `formulas`, solo para una prueba cruzada de calculo
python -m pytest tests/ -v
```

## 5. Preservacion del Excel

El motor nunca reconstruye el archivo desde cero: siempre parte de una copia
exacta del archivo base (`shutil.copy2`) y solo modifica lo estrictamente
necesario con openpyxl (fecha, bloque de clientes, Hoja2). Todo lo demas
—titulos, vendedor, anchos de columna, combinaciones de celdas, bordes,
formatos numericos, configuracion de impresion— queda intacto porque nunca se
toca.

## 6. Agregar / eliminar / reordenar clientes

El identificador de un cliente es siempre su **codigo** (nunca el numero de
fila). Operaciones soportadas (`route_engine.py`):

- `AddClient(codigo, after_codigo=None, at_start=False, no_factura=None)`
- `RemoveClient(codigo)`
- `MoveClient(codigo, after_codigo=None, at_start=False)`
- `ReorderAll(codigos=[...])` — reemplaza el orden completo por una lista
  explicita (debe contener exactamente los mismos clientes actuales).

Se pueden combinar varias operaciones en una sola corrida; si alguna es
invalida (cliente duplicado, codigo inexistente, etc.) **no se aplica
ninguna** y no se crea ningun archivo de salida.

## 7. Como funcionan las validaciones

Despues de generar o editar, `validators.validate_workbook` revisa:

- que existan las hojas `CORTES` y `Hoja2`;
- que la fecha en `B5` sea la esperada;
- que la numeracion de clientes sea consecutiva y sin codigos duplicados o
  invalidos;
- que las formulas de nombre/saldo existan, apunten a `Hoja2` y a su propia
  fila, y que no contengan `#REF!`;
- que `Observacion` quede vacia cuando corresponde (nuevo mes);
- que `Hoja2` tenga el encabezado correcto y la cantidad de filas esperada;
- (advertencia, no error) que todos los codigos de la ruta existan en
  `Hoja2`, para anticipar un `#N/A` antes de imprimir;
- **que cada nombre y saldo tenga un valor YA CALCULADO** (no solo la
  formula) y que ese valor no sea un error (`#N/A`, `#REF!`, etc.) — esto se
  revisa releyendo el archivo con `data_only=True`, es decir, exactamente lo
  que ve cualquier programa que lo abra sin recalcular. Una formula que
  "existe" pero cuyo resultado calculado es un error **es un error de
  validacion**, no una advertencia;
- que el total calculado coincida con la suma de los saldos calculados.

Un resultado con errores lanza `ValidationError` y detiene la corrida.

## 8. Sustitucion futura por SAP Service Layer

`data_source.py` define la interfaz `SapDataSource` (`fetch_clients()` y la
propiedad `is_mock`). `LocalMockDataSource` la implementa leyendo un archivo
local. `sap_service_layer.py` ya contiene `SapServiceLayerDataSource`
(filtro OData de clientes, paginacion, mapeo estricto, validacion de calidad,
re-login de sesion, errores tipados), que habla con SAP solo a traves de un
`ServiceLayerTransport` minimo (login/get/logout). Hoy esta probado contra
`FakeServiceLayerTransport` (`sap_fakes.py`, datos sinteticos). Lo unico que
falta es el transport HTTP real y confirmar el mapeo de 3 campos: ver
`docs/SAP-INTEGRATION-TODO.md`. **Ni `route_engine.py`, ni `excel_service.py`,
ni el runner necesitan cambiar.**

## 9. Como podra llamarlo n8n

`cli.py` ya esta pensado para invocacion no interactiva:

- subcomandos claros (`generate-route`, `edit-route`),
- todos los parametros por flags (nada de prompts),
- `--json` para salida machine-readable,
- codigos de salida distintos por tipo de error (0 = ok, 2 = ruta no
  configurada, 3 = error de archivo/validacion, 4 = operacion de cliente
  invalida, 1 = error inesperado),
- log estructurado en `logs/cxc_bot.log`.

n8n (via su nodo Execute Command, o un wrapper HTTP delgado alrededor de
`cxc_bot.cli.main()`) puede invocarlo directamente cuando el flujo
`Calendario -> n8n -> SAP Service Layer -> Bot de Rutas` este listo.

## 10. Limitaciones actuales (Fase 1)

- No hay conexion real a SAP: se usa `LocalMockDataSource` (Excel/CSV local).
- No hay calendario de vendedores automatico propio (ver Fase 2: se usa una
  fuente de programacion reemplazable en vez de construir uno).
- `RouteConfig` asume que la hoja de datos SAP tiene la misma forma que
  `Hoja2` (6 columnas, codigo en A). Si una ruta futura usa una forma
  distinta, se agrega su propia entrada en `config/routes.json` (los nombres
  de columna SAP tambien son configurables por ruta).
- La ruta piloto (`CORTES`) tiene el vendedor `DARWING` cargado en el Excel
  real (`E3`), lo cual no coincide con el listado de rutas por vendedor del
  encargo (que asigna Cortes a Eric Brizuela). El motor no interpreta ni
  corrige ese dato: preserva exactamente lo que trae el archivo. Se
  documenta aqui para que el negocio decida si el archivo piloto necesita
  corregirse antes de producción.

---

# Fase 2: orquestacion automatica (process-due-routes)

Todo lo de la Fase 1 (motor de Excel) sigue exactamente igual — Fase 2 es
una capa nueva que se apoya en el, sin tocarlo. El objetivo: poder preguntar
"¿que rutas debo preparar ahora?" y que el sistema resuelva todo solo, listo
para que un orquestador (hoy: el propio CLI; manana: n8n) lo dispare.

```
Fuente de programacion -> ¿esta due? -> validar contra el catalogo oficial
  -> config de la ruta -> archivo base mas reciente -> datos SAP (una vez
  por corrida) -> generar con el motor de Fase 1 -> validar -> guardar junto
  al base -> registrar en el historial -> marcar preparada (si tuvo exito)
```

Dos piezas se dejaron **intencionalmente reemplazables**, porque van a
cambiar pronto (InSitu en ~1 mes; SAP Service Layer despues) y no se quiso
construir infraestructura propia para ninguna de las dos:

- **Programacion de rutas**: interfaz `ScheduleSource` (`schedule_source.py`).
  Hoy: `LocalScheduleSource`, un JSON simple y editable a mano
  (`data/schedule.json`). El dia que exista InSitu, se implementa
  `InsituScheduleSource.list_entries()` con la misma forma y nada mas en el
  sistema cambia — ni siquiera hace falta que InSitu permita escribir de
  vuelta el estado (ver Idempotencia mas abajo).
- **Datos de clientes**: sigue siendo `SapDataSource` de la Fase 1
  (`LocalMockDataSource` hoy, `SapServiceLayerDataSource` despues). Sin
  cambios aqui, Fase 2 solo la reutiliza.

## 11. Modulos nuevos

```
text_utils.py       -> normalizacion de texto compartida (sin acentos,
                        mayusculas) para comparar vendedor/ruta sin depender
                        de que el texto venga identico desde distintas fuentes.
catalog.py           -> catalogo oficial vendedor -> rutas (config/vendor_catalog.json).
                        Valida que una ruta pertenezca a su vendedor; NO confia
                        en texto suelto heredado del Excel.
business_calendar.py -> dias no habiles de la semana + feriados (config/scheduling.json)
                        y el calculo de "N dias habiles antes de una fecha".
schedule_source.py   -> ScheduleSource (interfaz) + LocalScheduleSource (JSON).
file_resolver.py     -> localiza <routes_root>/<Vendedor>/<Ruta>/archivo base
                        mas reciente por el MES (y año, si esta explicito en
                        el nombre) del archivo -- no por fecha de modificacion:
                        abrir/guardar un Excel viejo no debe hacerlo parecer
                        el mas nuevo. Reconoce tanto "[RUTA] <mes>.xlsx"
                        (historico, año inferido) como "[RUTA] <mes> <año>.xlsx"
                        (formato preferido para archivos nuevos, año explicito
                        siempre prevalece). Si hay ambiguedad, aborta (nunca
                        adivina).
history_store.py     -> SQLite: registro de cada ejecucion (exito/error/omitida).
                        Es la fuente de verdad de la idempotencia.
notifier.py          -> Notifier (interfaz) + ConsoleNotifier/LogNotifier hoy;
                        WhatsApp/correo/Slack despues, sin tocar el runner.
runner.py            -> orquesta todo lo anterior: compute_due_entries(),
                        process_one_entry(), process_due_routes().
```

## 12. Configuracion

```
config/vendor_catalog.json  -> catalogo oficial (vendedor -> lista de rutas).
config/scheduling.json      -> dias_habiles_anticipacion (3 por defecto),
                                dias_no_habiles_semana, feriados (vacio hoy,
                                se agregan como ["YYYY-MM-DD", ...]),
                                routes_root, sap_mock_data_path.
data/schedule.json           -> la programacion en si (que sale, cuando, aprobada
                                o no). Es DATO de negocio, no configuracion del
                                sistema, por eso vive fuera de config/.
data/history.sqlite3         -> se crea solo; historial de ejecuciones.
```

`config/routes.json` (de Fase 1) se extiende con una entrada por cada ruta
real que exista bajo `routes_root` — el runner busca la config por el nombre
de ruta del catalogo, normalizado (acentos/mayusculas no importan).

## 13. Como se ejecuta

```bash
# Ver que rutas estan due HOY y generarlas:
python -m cxc_bot.cli process-due-routes --json

# Con una fecha "de hoy" fija (para pruebas o para reprocesar un dia atrasado):
python -m cxc_bot.cli process-due-routes --today 2026-10-12 --json

# Consultar el historial ("¿cuando se genero Cortes de octubre?"):
python -m cxc_bot.cli history --route Cortés --vendor "Eric Brizuela" --json
```

Salida de `process-due-routes --json` (exactamente lo que consume n8n):

```json
{
  "run_id": "2026-10-12-c567acb6",
  "status": "partial",
  "processed": 4,
  "succeeded": 2,
  "skipped": 0,
  "failed": 2,
  "routes": [
    {"vendor": "Eric Brizuela", "route": "Cortés", "fecha_salida": "2026-10-13",
     "status": "success", "output_file": ".../CORTES octubre.xlsx", "client_count": 32, ...},
    {"vendor": "José Carpio", "route": "Occidente", "fecha_salida": "2026-10-14",
     "status": "error", "error_category": "configuracion_ruta_faltante", "error_message": "..."}
  ]
}
```

`status` es `"success"` (todas bien), `"partial"` (alguna fallo pero otras no)
o `"error"` (todas fallaron). El proceso en si sale con **codigo 0** aunque
haya fallos individuales — la decision de que hacer con cada ruta fallida es
de quien orquesta (n8n), leyendo `routes[].status`, no del codigo de salida
del proceso completo (eso solo indica si el runner pudo ejecutarse).

### Categorias de error (`error_category`)

| Categoria | Cuando pasa |
|---|---|
| `programacion_invalida` | El vendedor o la ruta no estan en el catalogo oficial, o la ruta no le pertenece a ese vendedor. |
| `configuracion_ruta_faltante` | La ruta no tiene entrada en `config/routes.json`. |
| `carpeta_vendedor_faltante` / `carpeta_ruta_faltante` | No existe la carpeta esperada bajo `routes_root`. |
| `archivo_base_faltante` | La carpeta existe pero no hay ningun archivo `[RUTA] <mes>.xlsx`. |
| `archivo_base_ambiguo` | Hay mas de un candidato y no se puede decidir cual es el mas reciente con certeza. |
| `fuente_datos_invalida` | Fallo al obtener la tabla de clientes (SAP/mock). |
| `cliente_inexistente` | Un codigo de cliente de la ruta no existe en la tabla de clientes (VLOOKUP = `#N/A`). |
| `formula_invalida` | Error de formula distinto de `#N/A` (`#REF!`, etc.). |
| `validacion_fallida` | Cualquier otra validacion post-generacion que no pase. |
| `archivo_destino_ya_existente` | Ya existe un archivo con ese nombre (no se sobreescribe). |
| `error_escritura` | Error de sistema de archivos al guardar. |
| `error_inesperado` | Cualquier otra cosa no prevista. |

## 14. Idempotencia

La fuente de verdad es `HistoryStore` (SQLite), **no** el estado que
devuelva el `ScheduleSource`. Antes de tocar cualquier archivo, el runner
busca si ya existe una ejecucion **exitosa** con la misma `natural_key`
(`fecha_salida + vendedor + ruta`, normalizados). Si existe, la entrada se
marca `skipped` y no se hace nada mas.

Esto es deliberado: `LocalScheduleSource.mark_prepared()` SI actualiza el
JSON local (asi una entrada ya preparada ni siquiera vuelve a aparecer como
"due"), pero una futura fuente de solo lectura (InSitu, si no permite
escribir de vuelta el estado) igual quedaria protegida por el historial. Se
probaron ambos caminos explicitamente (ver `tests/test_runner.py`).

Ademas, `generate_route` (Fase 1) nunca sobreescribe un archivo existente
sin `force=True`, asi que aunque el historial se perdiera, un archivo ya
generado nunca se pisa silenciosamente.

## 15. Manejo de errores y validacion fallida

Un fallo en una ruta se registra y se pasa a la siguiente; nunca detiene el
lote completo (`tests/test_runner.py::test_one_route_fails_another_succeeds_in_same_batch`).
Si la generacion produce un archivo pero la validacion posterior falla (por
ejemplo, un cliente sin registro SAP → `#N/A`), el archivo invalido se
**elimina** antes de terminar — nunca queda un archivo con apariencia de
valido pero con datos incorrectos en la carpeta del vendedor.

## 16. Como se conectara n8n

El contrato es el CLI con `--json`: n8n no necesita saber nada de Python.

**Nodo recomendado: Execute Command**, en un workflow disparado por un
**Cron** (por ejemplo, todos los dias habiles a las 6am):

```
Cron (todos los dias, 06:00)
  -> Execute Command:
       command: cd "/ruta/al/proyecto/CXC BOT" && .venv/bin/python -m cxc_bot.cli process-due-routes --json
  -> Function/Code node: JSON.parse(stdout)
  -> IF: {{$json.failed > 0}}
       true  -> (recorrer $json.routes filtrando status=="error") -> notificar (WhatsApp/correo/Slack)
       false -> (opcional) notificar exito resumido
```

Notas practicas para quien configure el nodo:

- El comando debe ejecutarse con el `cwd` del proyecto (las rutas de
  config son relativas a el) y usando el interprete del `.venv` del
  proyecto (`.venv/bin/python`), no el Python del sistema.
- stdout contiene **solo** el JSON (los logs van a `logs/cxc_bot.log` y a
  stderr) — se puede pasar `stdout` directo a un nodo `JSON.parse`.
- El proceso sale con codigo 0 aunque algunas rutas hayan fallado
  individualmente; n8n debe decidir por `$json.failed` / `$json.routes[].status`,
  no por el exit code, para no tratar un `"partial"` como si el runner
  hubiera reventado.
- Cuando se quiera reemplazar `ConsoleNotifier`/`LogNotifier` por WhatsApp o
  correo real, NO hace falta tocar `runner.py`: basta con que ese paso lo
  haga el propio workflow de n8n leyendo `$json.routes` (mas simple), o
  implementar `WebhookNotifier` en `notifier.py` para que el runner mismo
  llame a un webhook de n8n (el contrato ya esta definido en ese archivo).

## 17. No implementado todavia (a proposito)

- Aplicacion de calendario propia (se uso un JSON simple en su lugar).
- Integracion real con InSitu (`InsituScheduleSource` esta definida pero
  lanza `NotImplementedError` a proposito).
- SAP Business One Service Layer (`SapServiceLayerDataSource`, de Fase 1,
  sigue sin implementarse).
- Notificaciones reales (WhatsApp/correo) — el contrato (`Notifier`) esta
  listo, falta solo conectar un canal real cuando haya credenciales.
- Despliegue de n8n en si (esta fase deja el CLI listo para que n8n lo
  invoque, no instala ni configura n8n).


## Instalacion y validacion en Windows (PowerShell)

Python 3.12 validado. No reutilizar el `.venv` recibido de macOS.
Crear un entorno local fuera del repositorio y de Trabajo-Sync:

```powershell
$CxcEnv = Join-Path $HOME "Documents/Codex/environments/cxc-bot-windows/.venv"
py -3.12 -m venv $CxcEnv
$CxcPython = Join-Path $CxcEnv "Scripts/python.exe"
# Ejecutar los comandos siguientes desde la raiz de CXC BOT.
& $CxcPython -m pip install -r requirements-dev.txt
& $CxcPython -B -m pytest tests/ -q -p no:cacheprovider
# Smoke test seguro: servidor temporal, solo /health, sin operaciones reales.
& $CxcPython -B -m pytest tests/test_api_server.py::test_health_endpoint -q -p no:cacheprovider
```

Para uso sin tests adicionales: instalar `requirements.txt`. `tzdata` es
necesario en Windows para `America/Tegucigalpa` y otras zonas IANA.
El entorno externo no pertenece al repositorio Git ni a Trabajo-Sync;
no se sincroniza ni se versiona. Las reglas `.venv` existentes tambien lo
excluirian si se utilizara una carpeta de ese nombre dentro del proyecto.
Los fixtures deben estar disponibles en `tests/fixtures/`; ninguna prueba
requiere archivos del Escritorio ni rutas personales del Mac.

Arranque manual de API, solo cuando se autorice operar:

```powershell
& $CxcPython -m cxc_bot.api_server
```

Escucha en `127.0.0.1:8765`; detener con Ctrl+C. `GET /health` es seguro.
No invocar `POST /process-due-routes` durante la validacion: procesa datos.
No se necesita Docker/n8n para ejecutar los tests o la API local.
