# Manual Operativo — Bot de Rutas CxC (Distribuciones Cosprobell)

Guía breve y práctica para operar el sistema día a día. Para el detalle
técnico de cada fase ver [README.md](README.md).

## 1. Qué hace

Todos los días (por defecto 06:00, hora de Honduras), el sistema:

1. revisa la programación de salidas (`data/schedule.json`);
2. determina qué rutas deben prepararse **hoy** (3 días hábiles antes de su
   salida — sábado es hábil, solo domingo no);
3. para cada una: localiza el archivo base más reciente, obtiene la tabla
   de clientes, genera el Excel del nuevo mes con fórmulas/formato
   intactos, lo valida, y lo publica en la carpeta del vendedor;
4. registra cada intento (éxito o error) en un historial técnico y, si
   tuvo éxito, crea un snapshot inmutable del resultado (historial de
   negocio);
5. notifica éxito o error.

Soporta las 13 rutas operativas reales, incluida Comayagua (que en
realidad combina dos archivos: Comayagua + Santa Cruz de Yojoa).

### 1.1 Preparación física de rutas con consignaciones

- En clientes de consignación, el cliente paga lo vendido.
- El vendedor revisa existencias durante la visita.
- Cobra las ventas realizadas.
- Repone producto.
- La hoja de consigna acompaña la preparación de la ruta.

Este es un proceso operativo y físico; no implica que CXC BOT ejecute
esas acciones.

## 2. Qué NO hace (todavía)

- No se conecta a InSitu ni a SAP Business One / Service Layer.
- No envía WhatsApp ni correo real (deja el mensaje listo para que un
  canal real lo tome después).
- No tiene calendario propio ni portal de vendedores.
- No genera informes gerenciales completos, solo consultas puntuales.
- No asume que un saldo es "mora" — solo es saldo (ver README, sección de
  limitaciones).

## 3. Cómo se ejecuta

**Manual, una vez:**

```bash
cd "CXC BOT" && source .venv/bin/activate
python -m cxc_bot.cli process-due-routes --json
```

**Automático (n8n):** ver sección 4.

**Consultar qué rutas están programadas o ya se prepararon:**

```bash
python -m cxc_bot.cli history --json
```

## 4. Papel de n8n

n8n es el reloj y el mensajero, nada más. El workflow (`n8n/`) hace:

```
Cron (6am) -> HTTP Request a http://host.docker.internal:8765/process-due-routes
  -> IF failed > 0
       true  -> rama de alerta
       false -> rama de "OK"
```

Toda la lógica (resolver archivos, generar, validar, componer rutas,
snapshots, idempotencia) vive en el código Python, **no** en n8n. Si más
adelante se conecta WhatsApp/correo real, solo hay que cambiar la rama de
alerta del workflow — no el Bot.

**El workflow queda guardado pero INACTIVO** a propósito: actívalo desde la
interfaz de n8n (`http://localhost:5678`, toggle "Active" arriba a la
derecha) cuando quieras que corra solo todos los días. Mientras tanto,
ejecútalo manualmente desde ahí con "Execute workflow" o usa el comando de
la sección 3.

## 5. Dónde aparece el resultado

- **El Excel final**: en la misma carpeta del archivo base
  (`RUTA VENDEDORES/<Vendedor>/<Ruta>/[RUTA] [DD] [mes] [año].xlsx`).
- **Historial técnico** (cada intento, éxito o error):
  `data/history.sqlite3` — consultar con `cxc-bot history`.
- **Snapshots de negocio** (solo generaciones exitosas):
  `data/business_snapshots.sqlite3`.
- **Logs**: `logs/cxc_bot.log`.

## 6. Errores comunes y qué significan

| Categoría | Qué pasó | Qué hacer |
|---|---|---|
| `archivo_base_faltante` | No hay ningún Excel en la carpeta de esa ruta | Verificar que el archivo del mes anterior esté ahí, con el nombre correcto |
| `archivo_base_ambiguo` | Hay dos archivos y no se puede saber cuál es el más reciente | Revisar manualmente y borrar/renombrar el duplicado |
| `configuracion_ruta_faltante` | La ruta no tiene entrada en `config/routes.json` | Agregar su configuración (estructura del Excel) |
| `cliente_inexistente` | Un código de cliente no está en la tabla de SAP/mock | Verificar el código o la fuente de datos |
| `archivo_destino_ya_existente` | Ya existe un archivo con ese nombre | No se sobreescribe nunca; revisar si ya se generó |
| `storage_no_disponible` | Ver sección 8 | — |
| `catchup_expirado` | Ver sección 7 | — |
| `programacion_invalida` | El vendedor/ruta no coincide con el catálogo oficial | Corregir `data/schedule.json` |

Un fallo en una ruta **nunca** detiene a las demás.

## 7. Si la Mac estuvo apagada / dormida

No pasa nada grave. Al volver a estar disponible, corre
`process-due-routes` (o espera al próximo disparo de n8n): el sistema
"pone al día" cualquier salida reciente automáticamente. Solo algo **muy**
atrasado (más de 14 días por defecto, configurable en
`config/scheduling.json` → `catchup_window_dias`) queda marcado como
`catchup_expirado` en vez de generarse solo — revísalo a mano.

## 8. Si la PC empresarial no responde

Cuando `RUTA VENDEDORES` viva en el share de red (`NetworkRouteStorage`),
un fallo se reporta como `storage_no_disponible` — el sistema **nunca**
usa una copia vieja en su lugar ni marca la ruta como preparada. Espera a
que la PC/share vuelva y corre de nuevo; es seguro repetir.

## 9. Archivo bloqueado / ocupado

Si alguien tiene el Excel abierto en el momento de publicar, el error es
`archivo_bloqueado`. Ciérralo y vuelve a correr — es seguro repetir, nunca
se genera un archivo corrupto a medias.

## 10. Consultar el historial

```bash
# todo lo reciente
python -m cxc_bot.cli history

# una ruta en particular
python -m cxc_bot.cli history --route Cortés --vendor "Erick Brizuela"
```

Para preguntas de negocio (evolución de saldo, comparar salidas, clientes
que subieron/bajaron) usar `cxc_bot/business_queries.py` desde un script o
una sesión de Python — son funciones simples, no hay CLI dedicado todavía.

## 11. Detener / reiniciar

```bash
# n8n
cd n8n && docker compose stop      # detener
docker compose start               # reanudar
docker compose down                # detener y quitar el contenedor (el volumen persiste)

# API local (la invoca n8n)
# Buscar el proceso y detenerlo, o simplemente cerrar la terminal donde corre:
pkill -f cxc_bot.api_server
# Volver a levantarla:
python -m cxc_bot.api_server &
```

## 12. Backup

```bash
# Volumen de n8n (workflows, credenciales, ejecuciones)
docker run --rm -v cxc_bot_n8n_data:/data -v "$(pwd)":/backup alpine \
  tar czf /backup/n8n_backup_$(date +%Y%m%d).tar.gz -C / data

# Bases de datos del Bot (historial tecnico, snapshots, config versions)
cp data/*.sqlite3 /ruta/de/respaldo/
```

## 13. Restore

```bash
# n8n
docker run --rm -v cxc_bot_n8n_data:/data -v "$(pwd)":/backup alpine \
  sh -c "cd / && tar xzf /backup/n8n_backup_YYYYMMDD.tar.gz"
cd n8n && docker compose restart

# Bases de datos del Bot
cp /ruta/de/respaldo/*.sqlite3 data/
```

## 14. Cómo retomar InSitu

1. Implementar `InsituScheduleSource` en `cxc_bot/schedule_source.py`
   (método `list_entries()`, misma forma que `LocalScheduleSource`).
2. Cambiar qué fuente de programación usa `runner.py`/`cli.py`.
3. Nada más cambia — el runner no sabe ni le importa de dónde viene la
   programación.

## 15. Cómo retomar SAP

La fuente `SapServiceLayerDataSource` (`cxc_bot/sap_service_layer.py`) ya
existe y está probada con un transport falso. Falta solo el transport HTTP
real y confirmar el mapeo de campos; el detalle completo está en
`docs/SAP-INTEGRATION-TODO.md`.

1. Implementar `HttpServiceLayerTransport` (login/get/logout, solo lectura).
2. Usar esa fuente en `cli.py`/`api_server.py` en lugar de `LocalMockDataSource`.
   Su propiedad `is_mock=False` hace que los snapshots puedan marcarse `prod`.
3. Nada más cambia — el motor de Excel y el runner no saben de dónde
   vienen los clientes.

## 16. Cómo funcionan las rutas compuestas

Hoy solo Comayagua (Comayagua + Santa Cruz de Yojoa), configurado en
`config/route_composition.json`. Cada mes, el sistema:

1. busca el archivo más reciente de **cada** componente por separado (cada
   uno en su propia carpeta de vendedor/ruta);
2. arma una sola lista: primero todos los clientes de Comayagua, después
   todos los de Santa Cruz de Yojoa;
3. genera el Excel de Comayagua con esa lista combinada (numeración,
   fórmulas y total recalculados);
4. guarda internamente (no en el Excel) de qué componente vino cada
   cliente, para poder consultarlo después por separado.

Ninguno de los dos archivos originales se modifica nunca. Agregar otra
ruta compuesta en el futuro es solo agregar una entrada en
`route_composition.json` — no requiere tocar código.
