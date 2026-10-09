# Integración SAP Business One — pendiente para el ingeniero

Alcance: **solo lo que falta conectar a SAP real**. Todo lo demás (motor de
Excel, runner, validaciones, historial, errores, idempotencia) ya existe y
está probado con datos sintéticos. Este documento no contiene secretos.

## Estado de la arquitectura

```
runner / motor de Excel
  -> SapDataSource (cxc_bot/data_source.py)                       LISTO
    -> SapServiceLayerDataSource (cxc_bot/sap_service_layer.py)   LISTO (sin red)
         filtro OData, paginación, mapeo, validación, errores, re-login
      -> ServiceLayerTransport (interfaz HTTP mínima)             LISTO
           - FakeServiceLayerTransport (cxc_bot/sap_fakes.py)     LISTO (tests)
           - HttpServiceLayerTransport                            PENDIENTE  <-- único código por escribir
```

Dependencia SAP actual del Bot: **una sola, de lectura** (tabla de clientes
que hoy se exporta a mano a `Hoja2`). El Bot no escribe en SAP, no bloquea
clientes ni postea documentos; no hay ninguna escritura SAP planificada.

## Pendiente 1 — Lectura de clientes (BusinessPartners)

| Campo | Detalle |
|---|---|
| Función del Bot | Reemplazar `Hoja2` de cada ruta con la tabla vigente de clientes (código, nombre, saldo, …) antes de generar el Excel. El Excel resuelve nombre/saldo con `VLOOKUP` sobre `Hoja2`. |
| Lectura/escritura | **Solo lectura** |
| Objeto SAP | `BusinessPartners` (Service Layer, `GET /b1s/v1/BusinessPartners`) o vista/SQL HANA equivalente autorizada |
| Filtro | Equivale al filtro manual "Código por C": `CardType eq 'cCustomer' and startswith(CardCode,'C')` (configurable en `odata_filter`) |
| Mapping pendiente | `CardCode→codigo`, `CardName→nombre`, `CurrentAccountBalance→saldo` (estándar; **confirmar** que el saldo coincide con el de la columna que hoy se exporta). **Sin identificar:** `Dirección factura` (hoy `BilltoDefault`), `Activo desde` (hoy `ValidFrom`), `Contabilización automática` (sin campo). Ajustar en `BusinessPartnerFieldMap`; son opcionales (`None` si no existen). |
| Validación esperada | Ya implementada: respuesta con lista `value`; `CardCode` texto no vacío; saldo numérico (nunca se convierte a 0 en silencio); sin `CardCode` duplicados (sin distinguir mayúsculas, como VLOOKUP); todos inician con `C`; tabla no vacía (reemplaza `Hoja2` completa); límite de páginas. |
| Errores esperados | `SapUnavailableError` (red/5xx/timeout), `SapAuthError`, `SapSessionExpiredError` (re-login + 1 reintento, ya implementado), `SapPermissionError` (403), `SapSchemaError`, `SapDataQualityError`. El runner los registra como `fuente_datos_invalida` en todas las rutas due y notifica; ninguna ruta se genera con datos parciales. |
| Idempotencia | Lectura pura: repetir es seguro. El runner consulta SAP **una vez por corrida** y solo si hay rutas due. La idempotencia de las rutas vive en `HistoryStore` (vendedor+ruta+fecha_salida), no en SAP. |
| Permisos mínimos | Usuario de servicio **solo lectura** sobre `BusinessPartners` (campos de arriba). Sin permisos de escritura ni de otros objetos. |
| Estado | Fuente y contrato: ✅ hecho y probado con fake. Transport HTTP real: ⏳ pendiente. Mapping de 3 campos y verificación del saldo: ⏳ pendiente. |

### Lo que debe implementar `HttpServiceLayerTransport`

Subclase de `ServiceLayerTransport` (`login`, `get`, `logout`):

- `login`: `POST /b1s/v1/Login` con `CompanyDB`, `UserName`, `Password` leídos de
  variables de entorno / gestor de secretos del servidor (nunca del repo ni de
  `scheduling.json`); guardar la cookie `B1SESSION`.
- `get(path, params)`: `GET /b1s/v1/<path>`; `path` puede ser un `odata.nextLink`
  relativo; enviar `Prefer: odata.maxpagesize=<n>`; timeout explícito; verificación TLS
  activa (certificado de la empresa si es interno).
- Traducir errores: 401 con sesión previa → `SapSessionExpiredError`; 401 en login →
  `SapAuthError`; 403 → `SapPermissionError`; red/timeout/5xx → `SapUnavailableError`.
  **Ningún mensaje de error debe contener credenciales ni cookies.**
- `logout`: `POST /b1s/v1/Logout`, best-effort.
- Cablear: en `cli.py` y `api_server.py` hoy se instancia `LocalMockDataSource`; sustituirlo
  por `SapServiceLayerDataSource(transport=HttpServiceLayerTransport(...))` según un
  ajuste de configuración. `is_mock` ya es `False` en esa fuente, por lo que el candado
  de ambiente (prod+mock → dev) deja de degradar los snapshots de forma automática.
- Si se usa HANA/SQL en lugar de Service Layer: implementar otra `SapDataSource`
  (`fetch_clients`) con la misma salida; reutilizar `map_business_partner`-style
  mapping y `validate_records`.

### Pruebas a ejecutar con SAP real (solo lectura, en un ambiente de pruebas de SAP)

1. Login/logout y lectura de una página.
2. Conteo de clientes y suma de saldos vs. el export manual actual (cuadre).
3. Corrida del Bot con `environment="test"`, comparando el Excel generado contra el
   que se produce con el export manual.
4. Cortar red / credencial inválida: debe fallar con `fuente_datos_invalida` sin generar archivos.

## Pendientes fuera del Bot de Rutas (no SAP, listados para no perderlos)

- **InSitu** (`InsituScheduleSource`): pausado por gerencia; es la fuente de la
  programación, no de SAP.
- `NetworkRouteStorage` sin probar contra un share SMB real.
- ~~Config de `LAS VEGAS` sintética~~: corregida 2026-10-09 (ver PROJECT-STATE.md).
