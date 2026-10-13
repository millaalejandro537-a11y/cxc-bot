# Contrato de datos requerido a BLC (backend empresarial) — Portal CxC

Versión del contrato: **1.0 (borrador para revisión de BLC)** · 2026-10-11.
Estado: **propuesta de lo que el Portal necesita leer.** No describe endpoints, tablas ni credenciales de BLC/Gelsyn porque **no los conocemos ni los inventamos**: BLC decide cómo exponerlo (vistas SQL de solo lectura, API GET, exportación), siempre que entregue estos datos con esta semántica. SAP Business One es la **fuente contable oficial**; el Portal nunca escribe en ella.

## 0. Principios y separación
| Lado | Contenido | Quién lo administra | Reglas |
|---|---|---|---|
| **BD de lectura empresarial** (SAP / Cosprobell BD / backend de Gelsyn) | Clientes, facturas, recibos, aplicaciones, saldos | **BLC** | El Portal usa una credencial **SELECT dedicada**, solo lectura. Prohibido crear objetos, escribir, o tocar esquemas, usuarios, variables o servicios de BLC. |
| **Almacenamiento privado del Portal** (SQLite en volumen propio, cifrado en respaldos) | Usuarios/2FA, rutas, zonas, folders, decisiones de gerencia, salidas, evaluaciones, visitas, documentos, avisos, auditoría | Cosprobell | Nunca se mezcla con la BD empresarial ni se escribe en ella. Los datos del Portal no se sincronizan de vuelta a SAP. |
El Portal consume el contrato mediante `cxc_portal/fuente_empresarial.py` (interfaz `Transporte` de solo lectura). Hoy solo existe `TransporteSimulado` (datos sintéticos); el transporte real lo escribirá el ingeniero con este contrato.

## 1. Metadatos de cada entrega (obligatorio)
| Campo | Tipo | Significado |
|---|---|---|
| `fecha_corte` | fecha ISO | Momento al que corresponden los datos: fin de la última sincronización **completa**. Todo saldo/estado es “al cierre de esa fecha”. |
| `generado_en` | datetime UTC | Cuándo se generó la entrega. |
| `version_contrato` | texto `M.m` | El Portal acepta la misma versión mayor (hoy `1`). |
| `sistema_origen` | texto | Identifica el origen (p. ej. nombre de la vista/servicio), para trazabilidad. |
| `completo` | booleano | `true` solo si la entrega es total y consistente. **Si es `false` el Portal la rechaza** y conserva la última buena. |

## 2. Entidades

### 2.1 Clientes
| Campo | Req. | Tipo | Notas |
|---|---|---|---|
| `codigo_sap` | sí | texto | Código de socio de negocio (`C…`), único y estable. |
| `nombre` | sí | texto | Nombre en SAP. (La ruta puede tener un nombre propio distinto: el Portal lo conserva aparte.) |
| `activo` | no | booleano | Cliente activo/bloqueado. |
| `vendedor_codigo`, `vendedor_nombre` | **pedido** | texto | Vendedor asignado **al cliente**. Hoy el snapshot no lo trae. Aclarar si es del cliente o del documento. |
| `zona` | **pedido** | texto | Zona comercial en SAP, si existe. |
| `moneda` | no | texto ISO | Moneda del cliente. |

### 2.2 Facturas (una fila por factura de crédito)
| Campo | Req. | Tipo | Notas |
|---|---|---|---|
| `id_factura` | sí | texto | **Identificador estable y único** (no sintetizado; no cambia entre entregas). |
| `numero` | sí | texto | Número visible. |
| `codigo_cliente` | sí | texto | FK a clientes. |
| `fecha_factura` | sí | fecha | Emisión. Base de la mora (con el plazo de la ruta). Debe ser ≤ `fecha_corte`. |
| `fecha_vencimiento` | no | fecha | Vencimiento en SAP. **Solo cotejo**: el plazo vigente es 30 días (local) / 60 (foránea) desde la emisión. Una igualdad con la emisión se trata como “posible contado”. |
| `total` | sí | decimal | Monto de la factura (≥ 0). |
| `aplicado` | sí | decimal | Importe aplicado según SAP (0 ≤ aplicado ≤ total). |
| `estado` | sí | `O` / `C` | Abierta / cerrada, tal como la entrega SAP. |
| `moneda` | no | texto ISO | |
| `tipo_documento` | **pedido** | texto | Distinguir factura, nota de crédito, anticipo, etc. (el snapshot no los distingue). |
| `es_contado` | **pedido** | booleano | Si la venta fue de contado, para no evaluarla como crédito. |
| `actualizado_en` | sí | datetime UTC | Última modificación del registro en el origen (trazabilidad / sincronización). |

### 2.3 Recibos y aplicaciones
| Campo | Req. | Tipo | Notas |
|---|---|---|---|
| `id_recibo`, `numero_recibo` | sí | texto | Identificador estable y número visible. |
| `codigo_cliente` | sí | texto | |
| `fecha_contable` | sí | fecha | **Fecha contable** del recibo. Es la que usa el motor para el atraso (el recibo solo respalda una factura si `fecha_contable` ≥ emisión). |
| `fecha_efectiva` | **pedido** | fecha | Fecha bancaria/real del pago, si difiere de la contable (hay diferencias entre fecha de recibo contable y efectiva sin resolver). |
| `monto` | sí | decimal | > 0. |
| `aplicaciones[]` | sí | lista | `{ id_factura, monto }`: cómo se reparte el recibo. Una aplicación a una factura inexistente se reporta, no se inventa. |
| `via` | no | `aplicacion_directa` / `conciliacion` | Origen de la evidencia (consultas SAP 3 y 2 respectivamente). |
| `fecha_conciliacion` | **pedido** | fecha | Cuándo SAP concilió/aplicó el recibo contra la factura. Puede llegar meses después del pago; el motor la conserva solo como dato informativo y **nunca** como fecha de pago. |
| `es_anticipo` | **pedido** | booleano | Recibo fechado antes de la emisión de la factura que luego se concilió contra ella (pago a cuenta). Hoy el motor lo detecta por fecha y, según la regla aprobada vigente, **no** respalda el pago (`ANTICIPOS_RESPALDAN_PAGO`). |
| `notas_credito[]` | **pedido** | lista | `{ id_factura, monto, fecha }`: notas de crédito conciliadas contra la factura. **No son pago**; el motor las muestra en el motivo de las facturas cerradas sin cobro íntegro por recibos. |
| `actualizado_en` | sí | datetime UTC | |

### 2.4 Saldos vigentes
| Campo | Req. | Tipo | Notas |
|---|---|---|---|
| `codigo_cliente` | sí | texto | |
| `saldo_abierto` | sí | decimal | **Saldo provisto por SAP al `fecha_corte`.** El Portal NO lo recalcula; si difiere de la suma de facturas abiertas (por notas de crédito, anticipos o pagos sin aplicar) lo **reporta** como diferencia, no lo corrige. |
| `moneda` | no | texto ISO | |

## 3. Frescura, actualizaciones y trazabilidad
- **Frescura**: umbral **por aprobar** (el Portal acepta hoy `max_edad_dias`, por defecto 2). Con datos más viejos la fuente queda **no apta para publicar**: el Portal sigue mostrando la última carga con su fecha y el aviso, nunca como “en vivo”.
- **Carga**: v1 del adaptador es **carga completa** por entrega. BLC debe indicar si ofrece cambios incrementales (`actualizado_en` ≥ cursor) y cómo se marcan los registros **eliminados/anulados**; mientras no exista, las anulaciones deben desaparecer o cambiar de estado de forma visible entre entregas.
- **Identificadores**: `id_factura` e `id_recibo` inmutables; si SAP renumera, BLC debe mantener una tabla de equivalencias.
- **Auditoría del Portal**: cada carga queda registrada (fecha de corte, calidad, filas descartadas). Las decisiones de gerencia se refieren a `codigo_sap` y `fecha_corte` de la evaluación.

## 4. Reglas de calidad que el Portal aplica (ya implementadas y probadas con datos simulados)
| Situación | Comportamiento |
|---|---|
| `completo = false` o versión mayor distinta | Se rechaza la entrega; si había una carga previa buena, se conserva. |
| Fila con campos obligatorios ausentes, importes incoherentes, estado inválido o fecha posterior al corte | La fila se aparta y se reporta; si superan el 2 % de las filas, **se rechaza toda la carga**. |
| Duplicado idéntico | Se ignora y se reporta. |
| Duplicado con contenido distinto (misma `id_factura`) | **Bloquea la carga**: no se elige una versión en silencio. |
| Recibo aplicado a factura inexistente / cliente de factura inexistente | Se reporta. |
| Saldo del origen ≠ suma de facturas abiertas | Prevalece el saldo del origen; la diferencia se lista. |
| Sin conexión (con reintentos y espera creciente) | Con carga previa: estado **DEGRADADA** (se muestra su fecha). Sin carga previa: la fuente no está disponible; no se inventan datos. |
| Datos desactualizados | Se usan para consulta con aviso; **no se publican documentos no-borrador**. |

## 5. Seguridad exigida a la fuente
1. Credencial **SELECT dedicada**, de mínimo privilegio, distinta de cualquier usuario administrador. Si solo existe un administrador: **detener** y pedir la de lectura.
2. Transporte cifrado (TLS) y, si es posible, lista de IP/origen permitido.
3. Sin escritura posible con esa credencial (se verifica con `validar_fuente`: no expone métodos de escritura; la garantía real es el permiso del lado de BLC).
4. Los secretos viven solo en las variables de entorno de la plataforma, nunca en el repositorio ni en el volumen de respaldos.

## 6. Proceso de aceptación (antes de usar datos reales)
1. BLC entrega una **muestra anonimizable** o acceso de solo lectura a un entorno de prueba con el formato del §2.
2. Se ejecuta `python -m cxc_portal.validar_fuente --fuente paquete.modulo:crear_fuente --max-edad-dias N --controles controles_sap.json` (sin escribir nada): contrato, solo lectura, frescura, integridad, coherencia de saldos, rendimiento y conciliación contra controles de SAP.
3. **Conciliación contable** de una muestra de casos (pagada, parcial, varias facturas, NC aplicada, pago no aplicado, saldo a favor) y de las 24 cuentas de Las Vegas contra las tres consultas SAP. Mientras no la apruebe contabilidad, los cortes del Portal permanecen **NO VALIDADOS**.
4. Solo entonces se cambia el transporte simulado por el real y se habilitan `apta_para_publicar` y la salida de documentos no-borrador.

## 7. Lo que NO hay que asumir
- Que existan los endpoints/tablas que BLC no ha confirmado.
- Que los datos simulados representen saldos reales: la fuente simulada se rotula `EMPRESARIAL_SIMULADA`, `en_vivo = false` y nunca es “apta para publicar”.
- Que el vencimiento SAP sea la base del plazo (lo es la emisión + plazo de la ruta).

## 8. Preguntas abiertas para BLC
1. ¿Vista SQL, API o exportación programada? ¿Paginación y límites?
2. ¿Hay cambios incrementales y marca de eliminados?
3. Vendedor y zona: ¿del cliente o del documento? ¿Cómo se actualizan?
4. Tipos de documento (NC, anticipos, pagos no aplicados) y monedas.
5. Umbral de frescura aceptable y ventana de sincronización.
6. Semántica exacta de `aplicado`, `saldo_abierto` y de las fechas contable/efectiva.
7. Entorno de prueba y credencial SELECT; quién la emite y cómo se rota.
