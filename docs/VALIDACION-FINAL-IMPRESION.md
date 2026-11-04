# Validación final de impresión — incompatibilidades registradas (2026-11-04)

Estado: **pendiente de validación humana con el Excel aprobado y la impresora real.** Nada de lo siguiente altera la plantilla aprobada: las viñetas del Excel están **desactivadas por defecto** (`vinetas_excel = 0`) y cuando se activan solo cambian el relleno de una celda por cliente, con aviso explícito.

## 1. Impresión en blanco y negro (incompatibilidad medida)
Los colores de la leyenda aprobada de Las Vegas (`cxc_bot/route_markers.HEX`) se reproducen en una impresora monocromática con estos grises (0 = negro, 255 = blanco; luma 0.299/0.587/0.114):

| Viñeta | Hex | Gris B/N | Texto negro sobre el relleno |
|---|---|---|---|
| AZUL (bueno) | `2563EB` | 96 | 4.1 : 1 |
| ROJO (solo contado) | `DC2626` | 92 | 4.3 : 1 |
| BLANCO (prueba) | `64748B` | 114 | 4.4 : 1 |
| VERDE (excelente) | `34A853` | 124 | 6.9 : 1 |
| AMARILLO (regular) | `FACC15` | 197 | 13.7 : 1 |

**Consecuencia:** en blanco y negro AZUL, ROJO, BLANCO y VERDE quedan a ≤ 40 niveles entre sí y **no se distinguen**; solo AMARILLO se separa. El Portal no lo corrige en silencio: al generar un Excel con viñetas añade la advertencia *"Impresión en blanco y negro: … no se distinguen entre sí; usar impresión a color"* (`excel_service` / `store.generar_excel`).
Legibilidad: la plantilla usa Arial 16 negrita (texto grande, mínimo 3 : 1), por lo que el texto negro es legible sobre los cinco rellenos. El validador (`validators.validate_vinetas`) rechaza un texto pequeño sin contraste suficiente.

**Decisión humana pendiente:** si se exige impresión en blanco y negro, hace falta una marca que no dependa del color (texto, símbolo o patrón). No se ha inventado ninguna.

## 2. Otros puntos que el Excel/impresión real debe confirmar
1. Celda de la viñeta (columna A / Folder es **propuesta**) contra el Excel aprobado de Las Vegas / Occidente.
2. Encabezado `#` vs `Folder`: depende del libro base; el generador lo conserva.
3. Impresión: las bases históricas son verticales, ajustadas al ancho, sin títulos ni área de impresión. Con 119 clientes (Occidente) el libro ocupa ~2 páginas (estimación) **sin repetir el encabezado** en la segunda; las bases sin `fitToHeight=0` se reducirían a una sola página ilegible.
4. Hojas de visita: la vista de impresión del Portal declara carta horizontal, una página por hoja y ~16–21 filas por página (estimación). Falta comprobarlo con el documento aprobado y una impresora real; ROJO tenía color propio solo desde esta rama.
5. Render real en Excel y LibreOffice (no disponible en el entorno de desarrollo Mac): leyenda bajo el total, combinaciones, alturas y saltos de página.

## 3. Cómo se verificará
Lista de comprobación para la sesión final: abrir el libro generado (borrador sintético y luego real) en Excel; imprimir a color y en blanco y negro; comparar con el aprobado; registrar aprobación o ajuste en el Hub antes de activar `vinetas_excel` en documentos operativos.
