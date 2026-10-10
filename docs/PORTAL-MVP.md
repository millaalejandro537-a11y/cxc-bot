# Portal CxC — MVP (2026-10-09)

Aplicación web responsiva (gerencia y vendedores) sobre los motores existentes. **Solo lectura**: no escribe en SAP ni en Cosprobell BD, no usa credenciales, no está desplegada.

## Arquitectura

```
portal-web/ (React 18 + Vite) ──/api──► cxc_portal/server.py (Python stdlib, 127.0.0.1:8780, solo GET)
                                          ├─ store.py          rutas, clientes en orden de folders, conciliación con el Excel aprobado
                                          ├─ sap_loader.py     consultas SAP 1/2/3 (08/10/2026) -> facturas + recibos
                                          ├─ gerencia_excel.py Excel aprobados (lista/orden, decisiones de gerencia, oráculo de regresión)
                                          ├─ demo.py           ruta SINTÉTICA
                                          └─ bridge.py         puente OPCIONAL a cxc-reporting-core (solo detecta)
                       cxc_bot/payment_semaphore.py  ← ÚNICO motor del semáforo (CXC BOT y portal)
```

- **Un solo motor**: `cxc_bot/payment_semaphore.py` (reglas vigentes 09/10/2026). `payment_behavior.evaluate_client`/`evaluate_portfolio` (reglas superadas) quedan **deprecados** (emiten `DeprecationWarning`); solo se conservan sus primitivas. El portal no contiene reglas: solo presenta.
- **Backend Python** (no Node/Express): las reglas viven en Python y no se duplican. Sin dependencias nuevas en el venv. La propuesta de Helsing (Express 5/Prisma/Zod) no se adoptó; cambiar la capa HTTP no tocaría las reglas.
- **`cxc-reporting-core`** no se fusiona ni se modifica. Su adaptador será la vía de datos en vivo cuando haya contrato (Gelsyn) y credencial SELECT.

## Datos privados (fuera de Git)

Los datos reales viven en `C:\Users\LTP\Documents\CxC-Datos-Privados\2026-10-08` (paquete `Cosprobell_CxC_Datos_MVP_08-09Oct2026.zip`, hashes SHA-256 verificados) con `01_Rutas/` y `02_SAP_08Oct2026/`. El backend los recibe por `--datos` o `CXC_DATOS_DIR`; no se copian al repo, al frontend ni a `dist/`. Sin ese directorio solo carga la ruta DEMO.

| Tipo de fuente | Significado |
|---|---|
| `SNAPSHOT_SAP_HISTORICO` | Consultas SAP exportadas el 08/10/2026. **No es en vivo.** Cada vista muestra la fecha de corte. |
| `SINTETICO_DEMO` | Ruta DEMO inventada. Excluida de las prioridades. |
| `EN_VIVO` | No existe (sin credencial SELECT ni conexión SAP). |

## Reglas del semáforo (`payment_semaphore`)

Últimas 10 facturas (emisión DESC, número DESC) · ≥ 4 facturas en TODO el historial (pagadas y abiertas) o EN_PRUEBA · plazo de la ruta (foránea 60 / local 30) desde la emisión, no el vencimiento SAP · mora = max(0, último recibo contable —o corte si está abierta— − emisión − plazo) · a la media entran pagadas íntegramente respaldadas por recibos y abiertas con mora > 5 · cierres sin cobro integral (nota de crédito, recibos anteriores a la emisión) quedan como excepción · media sin redondear: A ≤ 5, B ≤ 15, C ≤ 60, > 60 candidato a solo contado (nunca rojo sin decisión) · alerta por cada factura con mora > 60 · decisión de gerencia aparte del cálculo (autor, motivo, fecha).

## Validación

El motor, alimentado solo con las consultas SAP, reproduce los dos Excel aprobados por gerencia: **55 de 55 clientes** sin diferencias (historial, ventana, pagadas, abiertas, no acreditadas, evaluables, suma de mora, media, alertas, categoría), y el estado/mora/“en promedio” de cada una de las facturas de Las Vegas. Distribución final: Las Vegas 6 A · 3 B · 4 C · 9 en prueba · 2 candidatos; Yoro 11 A · 8 B · 9 C · 2 solo contado · 1 en prueba. Si el Excel y el motor difieren, el portal lo marca por cliente (`conciliacion_gerencia`) y en `/api/fuentes`.

## Ejecutar

```bash
# backend + frontend construido -> http://127.0.0.1:8780
python -m cxc_portal.server --datos "C:\Users\LTP\Documents\CxC-Datos-Privados\2026-10-08"
# desarrollo del frontend (proxy /api) -> http://localhost:5180
cd portal-web && npm install && npm run dev      # construir: npm run build
# pruebas (las de datos privados se omiten sin CXC_DATOS_DIR)
set CXC_DATOS_DIR=C:\Users\LTP\Documents\CxC-Datos-Privados\2026-10-08 && python -m pytest tests -q
```
Python: `C:\Users\LTP\Documents\Codex\environments\cxc-bot-windows\.venv`.

## Falta para producción

Credencial SELECT y adaptador de lectura (Cosprobell BD) · cortes validados de `cxc-reporting-core` · autenticación y roles reales (el selector es DEMO) · BD propia para visitas/decisiones/usuarios (alertas de visitas hoy es maqueta) · envío automático de reportes · que CXC BOT consuma `payment_semaphore` al generar rutas · numeración por zona y `Actual/Histórico` en CXC BOT · deploy.
