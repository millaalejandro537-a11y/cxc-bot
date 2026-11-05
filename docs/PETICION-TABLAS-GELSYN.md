# Petición a Gelsyn: tablas y campos que faltan en `Cosprobell-pruebas`

Fecha: 2026-11-05 · Origen: Portal CxC de Cosprobell · Uso: **solo lectura**. No pedimos escritura en ninguna tabla ni acceso a usuarios, sesiones, credenciales, teléfonos o correos.
Base de la petición: la copia de pruebas (67 tablas, 15 financieras) y el PDF de contrato del 4/11/2026. No se incluyen datos por cliente.

## A. Tablas o entidades que faltan

| # | Entidad | Para qué se necesita | Prioridad |
|---|---|---|---|
| 1 | **Conciliaciones** (cabecera y líneas: tipo de documento, debe/haber, id del documento, monto conciliado, fecha de conciliación) | 2 191 facturas cerradas (13,9 %) no tienen aplicación y 812 solo parcial. Sin esto no se distingue "cobrada" de "cerrada por nota de crédito o conciliación". | Crítica |
| 2 | **Partidas abiertas** por cliente, con saldo abierto firmado | El saldo no se puede reconstruir: 178 clientes difieren más de 0,01 entre `currentAccountBalance` y Σ(total − pagado). | Crítica |
| 3 | **Aplicación de notas de crédito a facturas** (existen `notas_credito`, pero no a qué factura se aplican) | 219 notas de crédito (213 vigentes) explican cierres sin recibo. | Alta |
| 4 | **Pagos sin aplicar y anticipos** (saldo a favor, con fecha) | 4 048 de 20 407 pagos (19,8 %) sin aplicación; 34 clientes con saldo negativo. | Alta |
| 5 | **Estado de sincronización** (fecha/hora de la última carga completa y si fue completa) | Decidir frescura y rechazar cargas parciales; `clientes` está en un corte anterior al de los documentos. | Alta |
| 6 | **Cartera agregada** (`/finanzas/cartera`, hoy "no disponible en esta carga") | Opcional: solo para cruzar contra el cálculo propio. | Baja |

## B. Campos que faltan en tablas existentes

| Dato | Estado actual | Se pide |
|---|---|---|
| Fecha efectiva (bancaria) del pago | No existe | Confirmar si SAP la guarda y exponerla |
| Fecha de conciliación/aplicación del recibo a la factura | No existe | Incluirla en `pagos_facturas` |
| Monto del recibo (`pagos.total` es NULL en el 100 %) | Se deriva de efectivo + transferencia (+ cheques + tarjetas) | Confirmar que esa suma es el monto oficial, o poblar `total` |
| `zona` financiera del cliente | NULL (se usa `ruta`) | Confirmar que `ruta` es la correcta |
| Vendedor | Solo el del cliente | Aclarar si es del cliente o del documento |
| Tipo de documento / venta de contado | No distinguible | Indicar cómo identificar contado |
| Corte de `clientes` | Anterior al de documentos | Refrescar al mismo corte |

## C. Aclaraciones de valores y acceso
1. Valores exactos de `estado`/`documentStatus`, de `cancelado` (Y/N/C y qué significa C) y de `tipo` en las aplicaciones (hoy solo observados en SQL).
2. Si la fecha de documento es comercial o un instante UTC.
3. 2–3 respuestas reales de ejemplo de `GET /finanzas/*`.
4. Un backend HTTPS de pruebas y una llave **limitada a GET `/finanzas/*`**. La llave de alcance completo podría permitir escrituras y no la usaremos.
5. Límites de tasa y comportamiento ante errores.

## D. Qué hará el Portal con esto
Lectura solo con GET, sin escribir en SAP ni en Gelsyn. Mientras falten 1 y 2, la fuente queda como `EMPRESARIAL_PRUEBAS`, no publicable, y los Excel como BORRADOR. Cualquier publicación requiere conciliación contable en SAP B1 y autorización de Cosprobell.
