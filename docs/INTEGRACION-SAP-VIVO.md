# Integración de datos vivos (SAP / Cosprobell BD) — paquete para el ingeniero

Estado: **diseñado y probado con el snapshot SAP del 08/10/2026; la fuente viva NO existe todavía.** El portal solo lee. Ninguna pieza de este paquete escribe en SAP, en Cosprobell BD ni en recursos administrados por BLC, ni usa credenciales administrativas.

## 1. Qué hay que construir (y qué no)
La aplicación solo conoce la interfaz `cxc_portal.fuentes.FuenteDatos`. Hoy la implementa `FuenteSnapshotSAP` (archivos exportados). Para pasar a datos vivos se implementa **una clase nueva** con la misma interfaz y se activa con `CXC_FUENTE=viva`; no se reescribe la app, el semáforo (`cxc_bot/payment_semaphore.py`) ni los reportes (`cxc-reporting-core`).

```python
class FuenteDatos(Protocol):
    tipo: str            # "EN_VIVO"
    fecha_corte: date    # momento al que corresponden los datos (fin de la última sincronización COMPLETA)
    etiqueta: str
    en_vivo: bool        # True
    def facturas_por_cliente(self, codigos) -> dict[str, list[FacturaSAP]]
    def nombre_cliente(self, codigo) -> str | None
    def saldo_abierto(self, codigo) -> float
    def documentos_abiertos(self) -> list[dict]      # facturas abiertas de TODOS los clientes (alimenta cxc-reporting-core)
    def registros_sap(self) -> list[tuple]           # (codigo, nombre, saldo) de todos los clientes (hoja de datos SAP de los Excel de ruta)
    def frescura(self, hoy=None) -> dict
```
Alternativa equivalente: completar el `CosprobellBackendAdapter` de `cxc-reporting-core` (rama `feature/cosprobell-backend-adapter`, ya con cliente HTTP GET, frescura y reconciliación) y que `FuenteViva` lo envuelva. Decisión del ingeniero según lo que exponga Gelsyn/BLC.

## 2. Datos necesarios (campos de las 3 consultas del snapshot; son el contrato mínimo)

**Facturas** (consulta 1) — una fila por factura de crédito: `CodigoCliente`, `NombreCliente`, `IdFactura` (clave estable), `NumeroFactura`, `FechaFactura`, `FechaVencimiento` (solo para cotejo: **no** es la base del plazo), `MontoFactura`, `ImporteAplicado`, `Estado` (`O` abierta / `C` cerrada), `Moneda`.
**Aplicaciones directas recibo→factura** (consulta 3): `IdFactura`, `IdRecibo`, `NumeroRecibo`, `FechaContableRecibo`, `MontoAplicado`.
**Conciliaciones** (consulta 2): `IdConciliacion`, `TipoDocumento` (13 = factura, 24 = recibo; otros = notas de crédito, asientos…), `DebeHaber`, `IdDocumento`, `MontoConciliado`, `NumeroRecibo`, `FechaRecibo`. Aporta recibos que consulta 3 no lista como aplicación directa.
**Pendientes que el snapshot NO trae y la app necesita:** vendedor y zona por cliente (hoy el reporte gerencial muestra “SIN VENDEDOR / SIN ZONA” para los ~320 clientes fuera de las rutas cargadas), tipo de documento (notas de crédito, anticipos, pagos no aplicados), código de moneda, estado activo del cliente.

Reglas de lectura ya implementadas y probadas (`cxc_bot/sap_exports.py`, `cxc_bot/payment_semaphore.py`; el motor reproduce los Excel aprobados por gerencia 55/55 clientes):
- Un recibo respalda una factura solo si su fecha contable es ≥ la fecha de emisión; la fecha de pago = fecha contable del **último** recibo cuando la suma de recibos cubre el total.
- “Cerrada sin cobro íntegro acreditado” (nota de crédito, conciliación sin recibo, recibos anteriores a la emisión) no es pago y queda como excepción. Desde 2026-10-13 el motivo de cada factura así cerrada explica el panorama: recibos válidos, anticipos anteriores a la emisión (interruptor `ANTICIPOS_RESPALDAN_PAGO`, hoy apagado; ver `docs/MIGRACION-OCCIDENTE-119.md` §5.3) y notas de crédito (consulta 2, tipo 14).
- La fecha de pago es la **contable del recibo**; la fecha de conciliación de SAP (consulta 2) se guarda aparte y no entra al cálculo.
- El resultado del sistema **prevalece** sobre decisiones anteriores de gerencia (`SISTEMA_PREVALECE`); estas solo resuelven revisión humana por falta de evidencia o errores de datos.
- Mora = max(0, último recibo contable —o fecha de corte si está abierta— − emisión − plazo de la ruta). Plazo foráneo 60 / local 30.

## 3. Validaciones obligatorias de la fuente viva
| Tema | Regla |
|---|---|
| Frescura | `fecha_corte` no anterior a **N horas/días** (umbral **por aprobar**; el core usa 48 h de desarrollo, no aprobado). Sync fallida o ausente ⇒ la fuente se declara no apta; el portal sigue mostrando la última fecha con aviso (nunca presenta datos viejos como vivos). |
| Identidad | `IdFactura` único y estable; sin ids sintetizados. |
| Saldo | `saldo_abierto` por cliente = Σ (total − aplicado) de facturas abiertas **provisto por SAP**; no recalcular con otra fórmula. Confirmar que `ImporteAplicado` sale de la misma fuente contable que el reporte de antigüedad. |
| Conciliación | Por cliente y global contra los controles de SAP (`currentAccountBalance` / total del reporte de antigüedad). Diferencias visibles, nunca corregidas en silencio. Nota: el saldo de cuenta puede incluir notas de crédito, anticipos y pagos sin aplicar que las facturas no contienen. |
| Contabilidad | `saldo_fuente_validada` permanece **False** (cortes NO VALIDADOS) hasta aprobación humana de contabilidad con prueba de muestra (6 casos: pagada, parcial, varias facturas, NC aplicada, pago no aplicado, saldo a favor). |

## 4. Pruebas de conexión de solo lectura (listas para usar)
```bash
# 1) Aceptación de la fuente (sin escribir nada). Salida 0 = apta.
python -m cxc_portal.validar_fuente --fuente paquete.modulo:crear_fuente --max-edad-dias 1 --controles controles_sap.json [--json]
# 2) Preflight/frescura del backend con el core (solo GET; requiere COSPROBELL_BACKEND_*; ver cxc-reporting-core/docs/BACKEND-INTEGRATION.md)
cxc preflight backend --fecha-corte AAAA-MM-DD
# 3) Regresión funcional completa del portal sobre la fuente
python -m pytest tests -q
```
`validar_fuente` comprueba: contrato completo; que no exponga métodos de escritura; en vivo; edad de los datos; integridad (ids únicos, estados C/O, aplicado ≤ total, abiertas con saldo, recibos > 0); consistencia saldo = Σ abiertas; cobertura de recibos; rendimiento de una muestra; conciliación contra `controles_sap.json` (`{"por_cliente": {...}, "total_global": ...}`). Probado hoy contra el snapshot: **falla solo por no ser en vivo**; contra una fuente sintética coherente y fresca: apta.

## 5. Permisos y dependencias pendientes (decisiones de personas)
1. **Credencial SELECT dedicada** a Cosprobell BD (Railway) solo lectura, emitida por BLC; no existe/validada. Si solo hay usuario administrador: **detener** y pedir la de lectura.
2. Acceso al workspace de Railway donde vive la BD (hoy el conector solo ve el workspace personal, no el invitado “Helsing Estrada Project”).
3. Contrato final de Gelsyn: endpoints/paths, autenticación, nombres de campo, paginación, semántica de `saldoAbierto` y de `fecha_corte`, umbral de frescura, `secuencia`.
4. Mapeo de vendedor y zona por cliente (y si el vendedor es del cliente o del documento), tipos de documento, monedas.
5. Prueba de muestra contable (sección 3) y aprobación de contabilidad.
6. Quién opera y monitorea la sincronización; qué pasa si falla (política de aviso).
7. Mientras tanto la app **bloquea o rotula** lo que depende de datos vigentes (ver `docs/PORTAL-MVP.md`, “Qué funciona sin SAP vivo”).
