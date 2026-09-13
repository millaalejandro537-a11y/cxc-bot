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
- valida automaticamente el resultado antes de darlo por bueno.

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
  `Hoja2`, para anticipar un `#N/A` antes de imprimir.

Un resultado con errores lanza `ValidationError` y detiene la corrida.

## 8. Sustitucion futura por SAP Service Layer

`data_source.py` define la interfaz `SapDataSource` con un unico metodo,
`fetch_clients()`. `LocalMockDataSource` la implementa leyendo un archivo
local. El dia que se conecte el Service Layer real, basta con implementar
`SapServiceLayerDataSource.fetch_clients()` (autenticarse, consultar
`BusinessPartners` filtrando `CardCode` que empiece con `C`, y devolver la
misma lista de `SapClientRecord`). **Ni `route_engine.py` ni
`excel_service.py` necesitan cambiar.**

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
- No hay integracion con n8n todavia (la CLI esta lista para que se conecte).
- No hay calendario de vendedores automatico.
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
