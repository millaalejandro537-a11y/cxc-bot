# Matriz de contrato: requisito del Portal ↔ Gelsyn (`GET /finanzas/*`)

Fuente del contrato: PDF de Gelsyn (4/11/2026, §3.19–3.21). Nombres de campo del transporte tomados del PDF; **no hay respuestas reales de ejemplo todavía** (no existe backend HTTP de pruebas ni llave de solo lectura). Evidencia de nulos/valores: copia SQL `Cosprobell-pruebas`, solo agregados. Nada se inventó: lo que falta se marca así.

| Requisito del Portal | Endpoint / campo Gelsyn | Disponibilidad | Acción en el código |
|---|---|---|---|
| Clientes: código, nombre, activo/bloqueado, moneda | `/finanzas/clientes`: cardCode, nombre, activo, bloqueado, moneda | Contrato documentado | `TransporteGelsyn.clientes()` |
| Ruta del cliente | `ruta` (la `zona` financiera es NULL) | Documentado; usar `ruta` | `ruta` → zona |
| Vendedor | `vendedor` | Documentado; confirmar si es del cliente o del documento | Mapeado |
| Saldo de control | `saldo` | Corte de clientes anterior al de documentos → solo se usa si es el mismo corte | `saldos()` devuelve [] y registra advertencia si no coincide |
| Facturas: id, número, fecha, vencimiento, total, pagado, estado | `/finanzas/facturas`: docEntry, docNum, fecha, vencimiento, total, pagado, estado | Documentado | `facturas()` |
| Anuladas | `cancelado` Y/N/C (C = documento de cancelación) | Y/C se excluyen y se cuentan; ausente → fila inválida | `cancelacion()` |
| Recibos: id, fecha contable | `/finanzas/pagos`: docEntry, docNum, fecha | Documentado | `recibos()` |
| Monto del recibo | `total` es NULL en el 100 % de la copia → efectivo + transferencia (+ cheques + tarjetas) | Derivado, no oficial | `_monto_pago()`; confirmar con Gelsyn |
| Aplicación recibo→factura | `/finanzas/aplicacionesPagos`: pagoEntry, documento, tipo (`it_Invoice`), aplicado | Documentado; vocabulario de `tipo` solo visto en SQL | Guardia: ContratoIncumplido si no aparece `it_Invoice` |
| Conciliaciones / partidas / NC | No importadas por Gelsyn | **Falta** (13,9 % de facturas cerradas y 19,8 % de pagos sin aplicación) | Se excluyen y cuentan; no se inventan |
| Cartera agregada | `/finanzas/cartera` | No disponible | Calculada en el Portal con el motor único |
| Paginación / versión | `limit` 1–100, cursor opaco, `meta.version` | Documentado | Reinicio (máx. 2) ante `VERSION_FINANCIERA_CAMBIO` |
| Moneda / decimales | cadenas decimales | Documentado | `importe()` exacto, máx. 6 decimales |
| Frescura | `meta.ultimaCargaCompleta` | Documentado | `fecha_corte` = max(facturas, pagos) + cortes por entidad |

## Adaptador de `cxc-reporting-core`
`CosprobellBackendAdapter` espera partidas abiertas y estado de sincronización; Gelsyn no los expone. Solo se reutiliza `ReadOnlyApiClient` (solo GET, HTTPS, reintentos, sin secretos en errores).

## Resultado de validación (copia de pruebas, solo lectura)
Vía `--via-transporte` con un servidor falso alimentado por la copia: 174 de 174 clientes de las rutas aprobadas quedan en la misma clase que el camino directo; 0 filas descartadas; calidad OK; fuente `EMPRESARIAL_PRUEBAS`, **nunca apta para publicar**.

## Dependencias externas reales
1. Backend HTTPS de pruebas y llave limitada a GET `/finanzas/*` (la llave de alcance completo podría permitir escrituras).
2. Respuestas reales de ejemplo (nombres de campo, `estado`, `tipo`, semántica de `C`, formatos de fecha e importe).
3. Clientes al mismo corte que los documentos; decisión sobre importar conciliaciones/partidas.
4. Límites de tasa, autenticación y errores.
5. Conciliación contable en SAP B1 y autorización explícita para publicar.
