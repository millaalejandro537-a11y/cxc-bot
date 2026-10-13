# Migración de Occidente a 119 clientes — informe y procedimiento (2026-10-12; corrección previa 2026-10-13)

Estado: **ensayada dos veces y verificada sobre copias aisladas; la BD original NO se modificó.** Aplicarla a la BD real requiere la aprobación de Alejandro (sección 6).

## 1. Fuentes vigentes revisadas (solo lectura, en Descargas)
| Archivo | Resultado |
|---|---|
| `OCCIDENTE OCTUBRE.xlsx` (sha256 `bd2e50af…`) | 119 clientes únicos, 119 folders únicos, 21 zonas; el orden relativo de los 116 anteriores no cambió |
| `SEMAFORO OCCIDENTE.xlsx` | 119 clientes: 31 A · 16 B · 39 C · 18 en prueba · 15 contado (ROJO); trae los mismos códigos y folders |
| `HOJAS DE VISITA OCCIDENTE.xlsx` | 8 hojas, 119 clientes (16 × 7 + 7), mismo orden; colores 31/16/39/18/15 |
Los archivos intermedios (`… CORREGIDO.xlsx`, `… SEMAFORO 60 DIAS PARA GERENCIA.xlsx`, el JSON de 116) quedan superados.

## 2. Diferencias contra la BD local (116 clientes)
| Cambio | Detalle |
|---|---|
| **3 clientes nuevos** | C10266 `23 LE` (después de Alejandro's Nails, orden 118) · C10773 `1 SJ` (después de Renatha Studio, orden 43) · C11156 `2 SJ` (después de SB Bonitas, orden 44) |
| **Zona nueva** | `SJ` = San Juan, Intibucá |
| **Prefijos renombrados** | `LL`→`LO` (La Labor, Ocotepeque): C10423 `1 LO`, C10312 `2 LO`, C10503 `3 LO` · `D`→`DN` (Dulce Nombre de Copán): C10756 `1 DN` |
| Sin cambio | 112 clientes (código, nombre, folder) |
Todo lo demás coincide: códigos, nombres, zonas restantes, vendedor (José Carpio), tipo (foránea) y plazo (60 días).

## 3. Qué se construyó (reutilizando el Portal; sin motores nuevos)
- `cxc_portal/migrar_ruta.py` + comandos `generar-json-ruta` y `migrar-ruta`: **plan de solo lectura** (BD abierta en modo lectura) y **aplicación transaccional** con respaldo verificado obligatorio. Solo admite: renombre de zona completa, zona nueva, clientes nuevos con posición, y reordenamiento por esas inserciones. **Detiene sin escribir** ante: cliente borrado, código o nombre distinto, nombre de zona distinto, orden relativo alterado, cliente de otra ruta o rechazado, folder en uso, renombre con otro número. Idempotente. Los prefijos retirados (`LL`, `D`) conservan su marca de agua: **no se reutilizan**.
- `cxc_portal/verificar_migracion.py`: verificación de solo lectura (BD ↔ JSON ↔ hojas de visita ↔ semáforo/motor). Solo la **estructura** puede fallar; las diferencias de clasificación o de media frente al documento son **informativas** (prevalece el sistema).
- `Estado(solo_lectura=True)`: abre una BD real sin crear tablas ni migrarla; `respaldar` y `respaldar-cifrado` ya la usan.
- **Correcciones encontradas en el primer ensayo:** (1) el lector de hojas de visita **truncaba en silencio** el documento nuevo (119 → 23) por desconocer el color `ROJO`; ahora lee todo, advierte colores desconocidos y compara el documento con la ruta; (2) `agregar_cliente` aceptaba zonas inexistentes o retiradas; ahora exige zona definida.

## 4. Reglas de clasificación vigentes
1. **Contado = ROJO automático (2026-10-12).** Media > 60 días → `SOLO_CONTADO`, sin aprobación adicional de gerencia (`payment_semaphore.CONTADO_AUTOMATICO`). Revierte la aprobación del 11/10 ("nunca convertir candidatos en decisión automática"); reversible con un valor.
2. **El sistema prevalece sobre decisiones anteriores de gerencia (2026-10-13)** (`payment_semaphore.SISTEMA_PREVALECE`, único punto de decisión del motor). Las decisiones se **conservan** (autor, motivo, fecha, `decision_aplicada = false`) solo para trazabilidad y solo **resuelven lo que el sistema no puede determinar** (revisión humana por falta de evidencia o errores de datos). El Portal ya no acepta (409) una decisión sobre un cliente cuya categoría determina el sistema, y la interfaz no la ofrece.
   - Efecto medido sobre las 3 rutas (174 clientes, snapshot 08/10): **el cálculo no cambió en ninguno**; 3 decisiones antiguas dejan de aplicar y su cliente pasa de C a **ROJO**: **C10855** (Las Vegas; el motivo registrado era una incidencia de cobro de un vendedor anterior), **C10672** y **C10758** (Yoro; motivo no comunicado). Distribución final: Las Vegas 6 A · 3 B · 3 C · 9 prueba · 3 ROJO; Yoro 11 A · 8 B · 7 C · 1 prueba · 4 ROJO; Occidente 31 A · 16 B · 39 C · 17 prueba · 15 ROJO · 1 revisión humana. Las 7 decisiones siguen en la BD, intactas.

## 5. Inconsistencias del ensayo: causa verificada factura por factura
Fuente: consultas SAP 1, 2 y 3 del 08/10/2026 (histórico, no saldos actuales). El detalle por factura (números, importes y fechas) es dato privado y **no se publica**: vive en el snapshot local y se reproduce con `tests/test_occidente_evidencia.py` (con `CXC_DATOS_DIR`) y con la lógica de `tests/test_sap_exports.py` (fixtures sintéticos).

**Fechas que el motor distingue.** *Fecha de pago* = fecha contable del recibo (consulta 3: `FechaContableRecibo`; consulta 2: `FechaRecibo`) — es la única que cuenta para la mora. *Aplicación* = el recibo se aplica a la factura (consulta 3, aplicación directa). *Conciliación* = cuando SAP concilia el recibo contra la factura (consulta 2, `FechaConciliacion`); puede llegar meses después y **nunca** es fecha de pago. Ahora se conserva como dato informativo en cada recibo (`Recibo.conciliacion`) y las notas de crédito (consulta 2, tipo 14) se acumulan aparte (`notas_credito`): **no son pago**.

### 5.1 C10270, C10298, C10502 — el motor es el correcto
El documento solo contó recibos de **aplicación directa** (consulta 3: "cobrada íntegramente según recibos directos"); el motor cuenta además los recibos que SAP **concilió** contra la factura (consulta 2). En cada caso hay **una factura** cuyo cierre en SAP solo se explica con un recibo conciliado: con él, la suma de recibos = `ImporteAplicado` de SAP = total de la factura **al centavo**; sin él, SAP habría cerrado la factura con un importe que nadie explica. El recibo conciliado es de un pago anterior a la conciliación, y la fecha de pago que usa el motor es la del recibo, no la de conciliación.
| Cliente | Efecto de contar el recibo conciliado | Clase |
|---|---|---|
| C10270 | una factura más en la media (con atraso): la media sube de 24.57 a **29.50** | C → C |
| C10298 | una factura más en la media (con atraso): la media sube de 34.50 a **41.40** | C → C |
| C10502 | una factura más en la media (sin atraso): la media baja de 10.43 a **9.13** | B → B |
Reproducción exacta: con el criterio "solo directos" el motor da las medias del documento. La clase no cambia en ninguno.

### 5.2 C10266 — el documento usó una regla descartada
Su media del documento (53.67) sale de usar el **vencimiento de SAP** (`DocDueDate`) como base del plazo: en tres facturas ese vencimiento no es emisión + 60 (es anterior), lo que suma unos días de atraso. La regla aprobada prohíbe usarlo; con emisión + 60 la media es **51.56**. Clase C en ambos casos. Es un cliente nuevo de la ruta: su fila del documento arrastra la propuesta anterior.

### 5.3 C10061 — por qué el motor da REVISIÓN HUMANA
Confirmado: **tres facturas pagadas con recibos y una cerrada con recibo + nota de crédito.** Pero los recibos que completan el pago de las tres pagadas son **anticipos**: fechados *antes* de la emisión de la factura y conciliados por SAP después (en un caso con meses de diferencia entre pago y conciliación). La cuarta factura se cerró con un recibo parcial más una nota de crédito que completa el total; la nota de crédito **no es pago**. En las cuatro, recibos + nota de crédito = total de la factura = importe aplicado por SAP.
La **regla aprobada** (`docs/INTEGRACION-SAP-VIVO.md`, probada contra los Excel aprobados 55/55) dice que un recibo respalda una factura solo si su fecha contable es ≥ la emisión. Por eso el motor no acredita ninguna como pagada → 0 facturas evaluables → **REVISIÓN HUMANA** (no hay base para la media); el documento, en cambio, la dejó **BLANCA / EN PRUEBA** ("solo 3 evaluables"), que tampoco es lo que dicen las reglas (4 facturas históricas = elegible). No se fuerza ninguna categoría. El motivo de cada factura ahora muestra el panorama completo: *"recibos válidos + recibos anteriores a la emisión (anticipos conciliados; no cuentan como pago según la regla vigente) = total explicado por SAP"*.

**Decisión de metodología pendiente (de Alejandro, no de gerencia):** ¿un anticipo conciliado cuenta como pago? Está implementado tras el interruptor `payment_semaphore.ANTICIPOS_RESPALDAN_PAGO` (hoy `False` = regla aprobada). Con `True` la fecha de pago es la del recibo (anterior al vencimiento ⇒ sin mora) y **C10061 queda en A** (3 facturas sin mora, historial 4) sin intervención de nadie. Pero el patrón es muy común en SAP (más de 2 000 pares recibo-factura 1 a 1), así que cambiar la regla **mueve a otros clientes**. Impacto medido en las 3 rutas:
| Ruta | Clientes con media distinta | Cambian de clase |
|---|---|---|
| Las Vegas (24) | 0 | 0 |
| Yoro (31) | 1 | 0 |
| **Occidente (119)** | **40** | **10, todos a mejor:** **7 salen de ROJO a C** (C10238, C10241, C10258, C10260, C10365, C10406, C10500), C10273 C→B, C10467 B→A y C10061 REVISIÓN→A |
Por eso **no se activó**: quitaría la restricción de contado a 7 clientes de Occidente respecto del documento aprobado. Mientras tanto, C10061 se queda en REVISIÓN HUMANA con la evidencia completa a la vista.

## 6. Ensayo 2 sobre copia aislada (2026-10-13) y regresión
Copia nueva desde el respaldo previo (`portal-20261013-040224.sqlite3`, sha256 verificado), sin tocar la BD real:
1. `migrar-ruta` (plan, aplicar, repetir → `sin_cambios`), `importar-documento --nueva-version` (119 clientes, 8 hojas, v1 histórica) y `verificar_migracion`: **15 PASS / 0 FAIL**; informativas: C10061 (revisión humana) y las 4 medias de la sección 5.
2. **Diferencia tabla por tabla contra el respaldo:** solo cambian filas de Occidente (clientes, zonas, folders y candidatos, documentos v1→histórica + v2, 2 asientos de auditoría) y se crean las tablas nuevas; **Las Vegas, Yoro, decisiones (7), usuarios, vendedores, salidas y visitas, idénticos.**
3. **Motor:** cálculo del sistema sin cambios en los 174 clientes de las 3 rutas (0 diferencias contra la línea base anterior; también 0 en las 1 148 cuentas del snapshot con el interruptor apagado).
4. **Pruebas:** suite completa con datos privados y `cryptography` aislado: **562 pasan, 1 se omite** (solo POSIX); 19 pruebas nuevas (motor, lector SAP, decisiones del Portal y auditoría de los 5 clientes con fixtures sintéticos; la parte contra el snapshot privado solo comprueba invariantes y se omite sin `CXC_DATOS_DIR`). Se actualizaron las pruebas que fijaban la regla anterior de decisiones.

## 7. Procedimiento para aplicarlo a la BD real (requiere aprobación; no ejecutado)
1. Detener cualquier servidor que use la BD (la CLI y el servidor no deben escribir a la vez).
2. Respaldo fresco: `python -m cxc_portal.admin respaldar --estado <estado> --destino <respaldos>` (solo lectura; ya existe `respaldos-pre-migracion-119\portal-20261013-040224.sqlite3` + `.sha256` y espejo de documentos).
3. `python -m cxc_portal.admin migrar-ruta --estado <estado> --ruta OCCIDENTE --archivo OCCIDENTE_PORTAL_119_CLIENTES_ZONAS_60D.json` (ensayo de solo lectura: revisar el plan).
4. Aplicar: mismo comando con `--aplicar --respaldo <respaldo .sqlite3>`. (La primera apertura con el código actual también crea las tablas nuevas del Portal: alertas, Excel versionado, configuración, feriados, notificaciones.)
5. Hojas de visita v2: copiar `HOJAS DE VISITA OCCIDENTE.xlsx` a `<datos>\01_Rutas\` y `python -m cxc_portal.admin importar-documento --estado … --datos … --ruta OCCIDENTE --archivo <nombre>.xlsx --nueva-version --confirmar --motivo "…"` (la v1 queda HISTÓRICA; no hay salidas vinculadas).
6. Verificar: `python -m cxc_portal.verificar_migracion --estado … --ruta OCCIDENTE --json … --hojas … --semaforo … --datos …` (debe dar 0 FAIL; las informativas están explicadas en la sección 5).
7. Reiniciar el servidor. La ruta sigue **en confirmación**: marcarla operativa es una acción humana en “Confirmar clientes”.
**Reversa:** `python -m cxc_portal.admin restaurar --estado … --archivo <respaldo> --forzar` (conserva `.pre-restauracion`).

## 8. Bloqueos y riesgos reales
- **Aprobación de Alejandro** para aplicar sobre la BD real (sección 7).
- **Decisión de metodología sobre anticipos** (sección 5.3): define C10061 y, de adoptarse, la clase de otros 9 clientes de Occidente. Hasta entonces C10061 es la única revisión humana de la ruta.
- Las 3 decisiones antiguas que dejaron de aplicar (C10855, C10672, C10758) dan ROJO por la regla vigente; el motivo de C10855 (incidencia de un vendedor anterior) era explícito. Si se quiere honrarlo, es una excepción nueva a la regla, no un olvido.
- Las clasificaciones usan el **corte histórico 08/10**, no saldos actuales: siguen siendo preliminares hasta tener fuente vigente (BLC). BLC debe entregar, además, la fecha de conciliación y el tipo de documento (nota de crédito, anticipo) — ver `docs/CONTRATO-DATOS-BLC.md`.
- Occidente sigue sin ser operativa; no hay salidas ni hojas generadas.
