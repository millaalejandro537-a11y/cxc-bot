# Reporte estructural — Excel de referencia (`Rutas de Cosprobell`)

Carpeta inspeccionada: `/Users/alejandrom/Documents/Cosprobell/Rutas de Cosprobell`
(14 archivos: las 13 rutas operativas + el histórico independiente de Santa Cruz de Yojoa).

**Advertencia que aplica a todo este reporte:** estos archivos son referencia
estructural DEV/TEST únicamente (fechas, saldos y clientes viejos/no
vigentes). Nunca se modificaron; todo el trabajo de esta fase se hizo sobre
copias o solo leyéndolos.

## Hallazgo principal: NO son estructuralmente iguales a Cortés

| Archivo | Hoja principal | Fila FECHA | Fila encabezado | Columnas | Fila total |
|---|---|---|---|---|---|
| CORTES septiembre | `CORTES` | A5 | 7 | # · Codigo · Nombre · Factura · **Saldo(E)** · Obs | 40, `E` |
| LAS VEGAS MAYO | `LAS VEGAS` | A5 | 7 | igual a Cortés | `E` |
| LIMA JUNIO 6 | `LIMA ` | A5 | 7 | igual a Cortés | `E` |
| ROATAN ABRIL | `Hoja1` | A5 | 7 | igual a Cortés | `E` |
| **PROGRESO 21-5** | `Hoja1` | **A4** | **6** | igual a Cortés, todo corrido 1 fila arriba | `E` |
| **OLANCHO AGOSTO** | `OLANCHO JUNIO ` | **A4** | **6** | igual a Cortés, corrido 1 fila arriba | `E` |
| **COMAYAGUA JUNIO** | `COMAYAGUA` | A5 | 7 | # · Codigo · Nombre · **ESTADO(D)** · Factura · **Saldo(F)** · Obs | 62, `F` |
| **OCCIDENTE MAYO** | `Hoja1` | A5 | 7 | igual a Comayagua (con ESTADO) | `F` |
| **STA CRUZ MAYO** | `Hoja1` | A5 | 7 | igual a Comayagua (con ESTADO) | `F` |
| **VILLANUEVA MAYO 2** | `Hoja1` | A5 | 7 | igual a Comayagua (con ESTADO) | `F` |
| **YOROABRIL** | `Hoja1` | A5 | 7 | igual a Comayagua (con ESTADO) | `F` |
| **sta barbara Junio** | `Hoja1` | A5 | 7 | igual a Comayagua (con ESTADO) | `F` |
| **LITORAL MARZO** | `Hoja1` | A5 | 7 | **sin** No.Factura, **sin** Observacion (solo # · Codigo · Nombre · Saldo(D)) | `D` |
| **CHOLOMA 19-5** | `Hoja3` (¡y `Hoja1` con otra versión!) | A4 | 6 | ver nota abajo | `E` (en `Hoja1`) |

Conclusiones:

1. **Dos familias de layout conviven**: la de Cortés (6 columnas, Saldo en
   `E`) y una más nueva con una columna `ESTADO` extra (7 columnas, Saldo en
   `F`) que usan Comayagua, Occidente, Santa Cruz, Villanueva, Yoro y Santa
   Bárbara. No se puede asumir un layout único para "todas las rutas".
2. **Los nombres de hoja NO siguen un patrón**: 8 de los 14 archivos usan el
   nombre genérico `Hoja1` como hoja principal en vez del nombre de la
   ruta. El motor ya soporta esto (`RouteConfig.main_sheet` es libre), pero
   confirma que cada ruta necesita su propia entrada de configuración — no
   se puede inferir el nombre de hoja a partir del nombre de la ruta.
3. **Progreso y Olancho** tienen todo corrido una fila hacia arriba (FECHA
   en A4, encabezado en fila 6, datos desde fila 7) en vez del patrón A5/7/8
   de Cortés.
4. **Litoral** es el más distinto: sin columna de factura ni de
   observación, y su numeración interna tiene un salto (existe fila 106 →
   108 sin 107) — un defecto de captura ya presente en el archivo histórico,
   no algo que este sistema deba "corregir" al leerlo solo como referencia.
5. **Choloma** es un caso especial: el archivo trae DOS versiones de la
   misma ruta en pestañas distintas — `Hoja3` (fecha 2026-05-06, layout
   viejo con columna `FIRMA` en vez de `Saldo`/`Observacion`, sin fórmulas
   de saldo) y `Hoja1` (fecha 2026-05-19, layout ya compatible con el motor:
   VLOOKUP contra `Hoja2`, `Saldo`, `Observacion`, encabezado en fila 6). Se
   tomó `Hoja1` como referencia estructural vigente; `Hoja3` parece un
   layout anterior que ya no se usa.
6. **La hoja de datos SAP no siempre se llama `Hoja2`**: en `LAS VEGAS
   MAYO.xlsx` la tabla de clientes vive en una hoja llamada `Hoja1` (el
   archivo no tiene ninguna hoja llamada `Hoja2`). `RouteConfig.data_sheet`
   ya es configurable por ruta, así que esto no requiere cambios de motor,
   solo la configuración correcta por ruta.
7. **La columna `ESTADO`** (rutas con 7 columnas) no tiene equivalente en
   los datos que hoy provee el DataSource (mock ni SAP futuro). El motor la
   preserva como columna de formato pero la deja **vacía** en cada mes
   nuevo, igual que `Observacion` — no hay fuente de datos real para
   inventar su contenido, y no se pidió tratarla de otra forma en esta
   fase. Si el negocio quiere que `ESTADO` traiga algo específico, es una
   decisión pendiente para cuando se defina su fuente real.

## Comayagua + Santa Cruz de Yojoa (para la ruta compuesta)

Ambos archivos comparten EXACTAMENTE el mismo layout (7 columnas, `ESTADO`
en D, `Saldo` en F, encabezado en fila 7, datos desde fila 8):

- `COMAYAGUA JUNIO.xlsx`: 54 clientes (filas 8–61), total en fila 62.
- `STA CRUZ MAYO.xlsx`: 16 clientes (filas 8–23), total en fila 24.

Esto simplificó la decisión técnica de la sección siguiente: al compartir
layout, la fila-plantilla de estilos de Comayagua sirve igual para las filas
que en realidad vienen de Santa Cruz — no hace falta reconciliar dos
plantillas de estilo distintas.

## Nombres de archivo históricos (fuera del alcance de `file_resolver`)

Los nombres reales en la carpeta de referencia son inconsistentes a
propósito (son históricos, de antes de este sistema): `YOROABRIL.xlsx` (sin
espacio), `CHOLOMA 19-5.xlsx` / `PROGRESO 21-5.xlsx` (fecha numérica en vez
de mes), `LIMA JUNIO 6.xlsx` / `VILLANUEVA MAYO 2.xlsx` (sufijo numérico).
`file_resolver.py` no necesita reconocer estos patrones: son solo muestras
estructurales, no archivos que el sistema vaya a procesar en producción. Los
archivos reales de producción (en `RUTA VENDEDORES/` en la PC empresarial)
seguirán la convención de este sistema (`[RUTA] [DD] [mes] [año].xlsx` o,
para históricos, `[RUTA] [mes].xlsx` / `[RUTA] [mes] [año].xlsx`).
