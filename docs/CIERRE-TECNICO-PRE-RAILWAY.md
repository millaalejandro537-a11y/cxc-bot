# Cierre técnico del Portal CxC antes de Railway — estado por funcionalidad (2026-10-11)

Alcance: todo lo que puede construirse y verificarse **sin Railway y sin SAP en vivo**, con datos sintéticos. Nada aquí se conectó a producción, a SAP, a BLC/Gelsyn ni a su PostgreSQL; no se usó ninguna BD financiera real; no hay envíos externos ni impresoras.

## 1. Auditoría previa (qué existía y se reutilizó)
Ya existían y **no se reconstruyeron**: semáforo único (`cxc_bot/payment_semaphore.py`), generador de Excel (`excel_service`, plantilla y validador), calendario hábil (`cxc_bot/business_calendar.py`), BD propia SQLite, decisiones de gerencia con historial, salidas/preparación/excepciones, candidatos con confirmación humana, folders permanentes, hojas de visita versionadas, cola de impresión y token de agente, seguridad (RBAC, CSRF, 2FA, sesiones), Docker, respaldos cifrados, `FuenteDatos` + `validar_fuente`, e integración con `cxc-reporting-core`. Se añadió lo que **faltaba** (tabla siguiente).

## 2. Estado por funcionalidad
| Requisito | Estado | Dónde / cómo se verifica |
|---|---|---|
| Calendario de salidas, preparación 3 días hábiles antes (lun–sáb), feriados configurables | **Terminado** | `flujo.py` (reutiliza `BusinessCalendar`), `estado_flujo.py` (feriados en BD + `config/scheduling.json`); API `/api/calendario`, `/api/salidas/serie`, `/api/admin/feriados`; UI Calendario. Pruebas `test_flujo_operativo.py` |
| Ciclo diario automático e idempotente | **Terminado** (listo para programar) | `admin ciclo-diario`, `POST /api/salidas/ciclo`; la tarea programada NO está activada |
| Selección de vendedor/ruta/período | **Terminado** | filtros del calendario; `periodo` = AAAA-MM de la salida |
| Clientes nuevos, zonas, prefijos, folders permanentes | **Ya existía y se verificó** | `confirmar_candidato`, `agregar_cliente`, marca de agua de folders |
| Excel con la plantilla de Cosprobell (gris, bordes, 2 hojas, fórmulas, saldos, total, formato monetario) | **Terminado reutilizando el generador aprobado** (idéntico celda por celda a Las Vegas y Yoro en pruebas previas) | `excel_service` + `validate_workbook` en cada generación; nunca se publica si no pasa |
| Carpetas Actual / Histórico, versiones inmutables | **Terminado en el Portal** | `excel_rutas` + `estado/rutas_excel/<ruta>/{Actual,Historico}`; exclusivo (`xb`), el histórico pasa a solo lectura, hash verificado, incluido en respaldos y migración |
| Excepciones antes de publicar | **Terminado** | El Excel exige: salida preparada, **revisión humana** de las excepciones y 0 clientes propuestos sin confirmar; siempre BORRADOR mientras la fuente no sea real/vigente |
| Repetición segura | **Terminado** | salida única por (ruta, fecha); alertas y notificaciones con clave única; Excel por huella de contenido; impresión sin duplicar |
| Motor único de semaforo, plazos 30/60, reglas de elegibilidad | **Ya existía** (sin cambios) | `payment_semaphore` |
| Decisiones, motivos, responsables, fechas, historial | **Ya existía** | `decisiones` |
| Evaluaciones por salida persistidas y comparables | **Terminado** | `salida_resumen` + `GET /api/comparar-salidas` |
| Alertas de cambio de categoría, mora y sin evidencia | **Terminado** (umbral de mora = SUPUESTO de +5 días, por confirmar) | `flujo.generar_alertas`; tipos en la tabla `alertas` |
| Candidatos a solo contado nunca se convierten en decisión | **Terminado y probado** | alerta "pendiente de gerencia"; las pruebas verifican 0 decisiones automáticas |
| Hojas de visita v1 (Occidente: 8 hojas) y vínculo por salida | **Ya existía** | `documentos`, `salida_documentos` |
| Seguimiento de visitas, **cobros y ventas** | **Terminado** | columnas `cobro`/`venta` (montos declarados en campo, no de SAP); totales por salida |
| Alerta por 3 salidas consecutivas con visita confirmada como no realizada | **Terminado** | aviso `OMISION_3_SALIDAS`; la falta de firma NO cuenta |
| Preparación y registro de impresión + adaptador del agente | **Terminado** | `POST /api/salidas/{id}/impresion`, `agente_impresion.py` (`ImpresoraSimulada`; rechaza impresoras reales) |
| Dashboard: cartera por vendedor/ruta/zona/cliente, saldos, vencimientos, mora, top 10, clasificaciones, alertas, excepciones, decisiones | **Terminado** | `dashboard.py` + `GET /api/dashboard`; cartera/antigüedad/vendedor/zona/prioritarios vienen de `cxc-reporting-core` (cortes NO VALIDADOS) |
| Oportunidades (clientes A con compras esporádicas) | **Terminado con criterio SUPUESTO** (A con ≤ 2 facturas en 180 días) | `dashboard.oportunidades`; por confirmar con gerencia |
| Cumplimiento de rutas | **Terminado** | `dashboard.cumplimiento` |
| Reportes automáticos y notificaciones preparadas | **Terminado en modo simulado** | resumen semanal y aviso de salida preparada a gerencia/admin y a cada vendedor (solo sus rutas); `ENVIO_EXTERNO_HABILITADO=False` |
| Adaptador de solo lectura de fuente empresarial + contrato | **Terminado con transporte simulado** | `fuente_empresarial.py`, `docs/CONTRATO-DATOS-BLC.md`; simula datos incompletos, desactualizados, duplicados y fallas de conexión |
| Seguridad, permisos, auditoría, recuperación | **Verificado** | pruebas de permisos por rol, CSRF, visibilidad por ruta, auditoría de cada paso; respaldo cifrado y restauración con salidas y Excel |
| Frontend | **Verificado** (compila; revisado en navegador contra datos sintéticos) | `Operacion.jsx`, `Salidas.jsx`, `Visitas.jsx` |

## 3. Parcial, y por qué
1. **Excel como documento operativo vigente** — mientras la fuente sea snapshot/simulada todo Excel queda BORRADOR y **no se prepara su impresión**. Falta fuente real validada (dependencia externa).
2. **`Actual/Histórico` en CXC BOT** (resolvedor/runner siguen en `<Vendedor>/<Ruta>/` plano): la gestión se hizo en el Portal (almacenamiento propio). Migrar los archivos de red de CXC BOT depende de decidir el almacenamiento definitivo.
3. **Numeración por zona en `excel_service._rebuild_client_block`** (modo clásico global): el Portal ya usa folders por zona (modo folder); el modo clásico se mantiene para las rutas no migradas.
4. **Diez rutas pendientes**: no se inventaron clientes ni orden; permanecen sin cargar hasta recibir su organización física.
5. **Occidente**: sigue **en confirmación** (no operativa); sus colores son preliminares y los 15 candidatos a solo contado y el cliente sin evidencia siguen pendientes de gerencia.
6. **Notificaciones**: solo simuladas. El canal real (correo/WhatsApp) y sus destinatarios requieren decisión y credenciales.
7. **Agente de impresión real y impresoras**: solo el adaptador y el simulador.
8. **Reportes con la fuente real**: la cartera del core depende de un corte importado; con datos sintéticos se prueba el flujo, no los saldos.
9. **Cambios incrementales de la fuente empresarial**: el adaptador v1 hace carga completa; falta saber qué ofrecerá BLC.
10. **Pruebas con datos privados** (`test_portal_datos_privados.py`, 12): NO se ejecutaron en este cierre por la restricción de no usar bases financieras reales.

## 4. Supuestos que requieren confirmación de Alejandro
- Umbral de “aumento importante de mora” entre salidas: +5 días de mora media.
- Definición de oportunidad comercial: cliente A con ≤ 2 facturas en 180 días.
- Frescura aceptable de la fuente (por defecto 2 días en el adaptador) y 3 días hábiles de anticipación (ya vigente).
- Que la revisión humana de excepciones sea requisito para generar el Excel (implementado así).

## 5. Qué decide/aporta BLC y qué hay que hacer para empezar con datos reales
Ver `docs/CONTRATO-DATOS-BLC.md` (datos exactos) y la lista mínima de acciones al final de `docs/NUBE-ALOJAMIENTO.md` / `docs/AUDITORIA-DESPLIEGUE.md`.
