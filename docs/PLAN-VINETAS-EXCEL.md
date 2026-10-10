# Plan: viñetas de semáforo dentro del Excel operativo (NO implementado)

Estado: **aprobado como siguiente fase; no implementado** (decisión 2026-10-09). El formato de los Excel operativos NO se modifica hasta que se apruebe esta fase.

## Objetivo
Que `process-due-routes` (y la edición manual) pinten en la hoja de la ruta la viñeta de cada cliente con el semáforo de `cxc_bot/payment_semaphore.py`, preservando **exactamente** las dos hojas operativas y el formato aprobado de Las Vegas.

## Formato aprobado que debe quedar intacto (de `Las_Vegas_Orden_Por_Zonas_Con_Leyenda.xlsx`)
- Hojas: `LAS VEGAS` (portada) y `Hoja1` (base SAP con `Código SN / Nombre SN / Saldo de cuenta…`). El archivo de trabajo trae además una hoja borrador `Control de ruta`: **no** forma parte del formato operativo de 2 hojas y no debe generarse ni propagarse.
- Portada: columnas A `Folder` (p. ej. `1 LV`), B `Codigo`, C nombre (`=VLOOKUP(B,Hoja1!A:C,2,0)`), D `No. Factura`, E `Saldo` (`=VLOOKUP(B,Hoja1!A:C,3,0)`, formato `L`), F `Observacion`; total en `E32` (`=SUM`); anchos de columna y alto de fila (≈ 30.75) fijos; celdas fusionadas A2:C2, A3:C3.
- Leyenda bajo la ruta (hoy A35:C40): título `SIGNIFICADO DE LAS VIÑETAS` y 5 filas ● con color — Blanca `#64748B` (cliente de prueba), Verde `#34A853` (excelente), Azul `#2563EB` (bueno), Amarilla `#FACC15` (regular), Roja `#DC2626` (solo contado).
- Folder = `<n> <prefijo de zona>` (LV, FB, PB, RL, SC; Yoro: SY, MY, PY, YT, YO). Altas nuevas: siguiente número de SU zona; no se renumera.

## Diseño propuesto
1. **Dónde pintar** (a confirmar con Alejandro sobre el Excel aprobado): relleno de la celda de la columna A (`Folder`) con el color de la viñeta final, sin cambiar texto, fórmulas, bordes, anchos ni número de hojas. Si gerencia prefiere otro lugar, se cambia una sola constante.
2. **Módulo nuevo** `cxc_bot/route_markers.py` (puro + escritura acotada): recibe `RouteConfig`, el roster y `{codigo: ResultadoCliente}` y devuelve un mapa `fila → color`. Sin reglas propias: el color sale de `ResultadoCliente.color_final`; `PENDIENTE_GERENCIA`/`SIN_DATOS` se pintan **sin color** (nunca rojo sin decisión).
3. **Integración en `excel_service.generate_route`** detrás de un parámetro opt-in (`markers=None` por defecto → comportamiento idéntico al actual). Solo toca el `PatternFill` de A{fila}; reutiliza el mismo flujo de copia→generación→validación→publicación atómica del runner.
4. **Validación** (`validators.py`): comprobar que (a) siguen existiendo exactamente las hojas esperadas, (b) no cambió ninguna fórmula/valor/ancho respecto a la generación sin viñetas, (c) cada color de A coincide con el semáforo del cliente, (d) la leyenda existe y no se alteró.
5. **Regla de seguridad**: la viñeta es una *propuesta pintada*; cualquier `SOLO_CONTADO`/rojo solo aparece si hay decisión de gerencia registrada (`decisiones_gerencia.json`). Las viñetas físicas siguen siendo decisión humana.

## Pruebas de regresión previstas
- Generar con y sin `markers` y comparar celda por celda (valores, fórmulas, estilos excepto el relleno de A): idénticas.
- Golden con los datos privados: la hoja pintada de Las Vegas reproduce los colores de `1 - Lista` del Excel aprobado (6 verde, 3 azul, 4 amarillo, 9 blanca, 2 roja) y la de Yoro los de su Excel aprobado.
- Las 14 rutas reales (`tests/fixtures/rutas_reales`) siguen pasando `test_real_routes_structural` sin cambios; rutas sin `credit_days` no se pintan.
- Fallo de la fuente de evidencia ⇒ la ruta se genera sin viñetas y se reporta `semaphore_error` (no se detiene la publicación).

## Orden de trabajo (cuando se apruebe)
1. Confirmar con Alejandro la celda/formato exactos usando el Excel aprobado (A del folder vs. otra columna).
2. `route_markers.py` + pruebas unitarias. 3. Parámetro opt-in en `generate_route` + validadores. 4. Golden con datos privados. 5. Flag en CLI/runner. 6. Prueba manual abriendo el Excel en Excel/LibreOffice antes de usar en producción.

## Riesgos
Cambiar el relleno puede romper formato condicional o impresión si existiera (no hay en Las Vegas, revisar Yoro y las demás); la hoja `Control de ruta` borrador no debe colarse; los Excel generados no deben mezclar la fecha de corte del snapshot con la de la salida.
