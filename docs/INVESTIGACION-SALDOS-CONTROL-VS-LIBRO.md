# Saldo de control vs. libro auxiliar (`movimientos_contables`) — Cosprobell-pruebas, 2026-11-06

Solo lectura y solo agregados (sin códigos ni nombres de clientes). Reproducible: `python -m cxc_portal.investigacion_saldos_pg --confirmo-servicio-pruebas Cosprobell-pruebas`.

## 1. Cortes y versiones (verificados hoy)
Gelsyn indicó que actualizó la base hoy, pero `finanzas_control` **no muestra ninguna publicación nueva**: todas las entidades conservan las mismas fechas y cantidades de la lectura anterior (17 280 facturas, 20 407 pagos, 22 146 aplicaciones, 1 170 clientes, 258 179 movimientos, 219 notas de crédito). Cortes `publicadoEn` (se asumen UTC): clientes 27/10 18:33 · aplicaciones 30/10 · facturas 04/11 16:07 · pagos 04/11 16:43 · movimientos 04/11 19:29 · notas de crédito 27/10. Lo único nuevo que vimos en la base es el esquema `cxc` (ver `docs/ANALISIS-ESQUEMA-GELSYN-CXC.md`). Si Gelsyn refrescó algo, no está publicado en `finanzas_control`.

## 2. Comparación recalculada
1 169 clientes `C…` (el cliente 12.º, de código no-C, tiene saldo 0): 11 sin movimientos (todos con saldo 0). **133 difieren** (1 036 coinciden), neto libro − control = **−186 708,00** (la cifra anterior de 1 158 clientes/133 diferencias se mantiene). Moneda: los 1 170 clientes son LPS; 33 clientes tienen líneas en USD (`monedaFC`) pero la comparación en moneda local (`debe/haber`) cuadra, así que **la moneda no explica nada**.

## 3. Causa: el corte, pero por orden de creación (`transId`), no por fecha
* Cortar el libro por fecha (≤ 27/10, foto de clientes) deja **97** diferencias: explica 36, no todas.
* Cortar por **`transId` < ~85 024** (asientos creados antes de la foto de clientes) da **0 diferencias en 1 169 de 1 169 clientes**. Los asientos posteriores son 180 líneas en 138 clientes (119 recibos, 55 facturas, 2 NC, 2 asientos manuales, 2 conciliaciones internas), neto **−186 708,00**, exactamente la diferencia.
* **106 de esas 180 líneas tienen fecha contable ≤ 27/10** (retrofechadas): por eso la fecha no basta. Los recibos que explican individualmente los 97 casos que la fecha no resuelve (90 pagos) son en efectivo, vigentes y 80 de ellos con aplicación a factura; sus fechas están 0–15 días antes del 27/10.
* Conclusión: el libro (04/11) es consistente con el saldo de control (27/10); **no hay discrepancia contable, hay desfase de cortes**. Cuando `clientes` se refresque al mismo corte, se espera coincidencia total.

## 4. Documentos vs libro (04/11), sin depender del corte
Σ(docTotal − paidToDate) de facturas vigentes coincide con el libro en 1 109 de 1 169 clientes; sumando el saldo pendiente de notas de crédito (7 abiertas, 0,20 en total) 1 113. Quedan **56 clientes** (neto −24 156,77; 52 con libro menor): 37 de ellos tienen saldo **a favor** (libro negativo), que las facturas no pueden expresar (recibos sin aplicar/anticipos). Las 213 notas de crédito vigentes están cerradas y ya reflejadas en `paidToDate`.

## 5. Clasificación de lo encontrado
| Categoría | Alcance | Estado |
|---|---|---|
| **Explicado** | 133 diferencias saldo de control vs libro (desfase de cortes por `transId`; moneda descartada) | Verificado: 0 diferencias con el corte correcto |
| **Dato faltante** | Conciliaciones/partidas (no hay `ReconNum`); pagos sin aplicar y anticipos como entidad; `pagos.transId` es NULL en 100 % (no se enlaza recibo↔asiento); `movimientos.documento` NULL | Pendiente de Gelsyn |
| **Requiere SAP B1** | Los 56 clientes con libro ≠ facturas (saldos a favor/recibos sin aplicar); que el libro de pruebas coincida con SAP; criterio contable de "saldo abierto" | Pendiente de contabilidad |

## 6. Siguiente bloqueo real
Para certificar saldos: (1) Gelsyn debe **refrescar `clientes` al mismo corte** que `movimientos` y publicarlo en `finanzas_control` (hoy no hay publicación nueva) — con eso se repite este análisis y debe dar 0; (2) conciliaciones/partidas o aceptar el libro como fuente de saldo; (3) validación contable en SAP B1 de una muestra (incluye saldos a favor). Mientras tanto la fuente sigue `EMPRESARIAL_PRUEBAS`, no publicable, sin saldos conciliados.
