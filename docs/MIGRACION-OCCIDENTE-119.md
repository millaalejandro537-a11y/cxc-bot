# Migración de Occidente a 119 clientes — informe y procedimiento (2026-10-12)

Estado: **ensayada y verificada sobre una copia aislada; la BD original NO se modificó.** Aplicarla a la BD real requiere la aprobación de Alejandro (sección 5).

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
- `cxc_portal/verificar_migracion.py`: verificación de solo lectura (BD ↔ JSON ↔ hojas de visita ↔ semáforo/motor).
- `Estado(solo_lectura=True)`: abre una BD real sin crear tablas ni migrarla; `respaldar` y `respaldar-cifrado` ya la usan, así que respaldar una BD real no la toca.
- **Correcciones encontradas en el ensayo:**
  1. El lector de hojas de visita **truncaba en silencio** el documento nuevo (119 → 23 clientes) porque desconocía el color `ROJO` y lo tomaba como fin de documento. Ahora lee todo, conserva y advierte colores desconocidos, y `importar_documento` compara el documento con la ruta (clientes de más, de menos y cantidad).
  2. `agregar_cliente` no exigía que la zona existiera: podía crear un cliente en un prefijo retirado. Ahora exige una zona definida.

## 4. Regla de clasificación vigente (2026-10-12) y verificación
**El sistema determina la clasificación; si el resultado es Contado (media > 60 días), corresponde ROJO (`SOLO_CONTADO`) sin aprobación adicional de gerencia.** Implementado en el **motor único** (`payment_semaphore.CONTADO_AUTOMATICO`; una decisión explícita de gerencia sigue pudiendo sustituir cualquier resultado). Sustituye a la regla anterior (“candidato pendiente de decisión”): ya no existe el bloqueo por candidatos a contado; solo la **revisión humana** (sin base para la media) y los **clientes sin evidencia** piden a gerencia. Para volver a la regla anterior basta poner `CONTADO_AUTOMATICO = False`.

| Ruta | Verificación (motor único con el corte histórico 08/10 vs documentos vigentes) |
|---|---|
| **Las Vegas (24)** y **Yoro (31)** | Las 12 pruebas de regresión con los documentos aprobados pasan; **ninguna clasificación final cambió** (todos sus candidatos ya tenían decisión); los 7 resultados de gerencia se conservan |
| **Occidente (119)** | El motor coincide con el semáforo vigente en **118 de 119** clientes; distribución del motor: 31 A · 16 B · 39 C · 15 contado · 17 en prueba · **1 revisión humana** |

**Diferencias del documento frente al motor (no se corrigen en silencio):**
1. **C10061** — el documento lo pinta BLANCA (en prueba); el motor da **REVISION_HUMANA** (4 facturas cerradas, ninguna con cobro íntegro acreditado por recibos). Es el “cliente sin evidencia” que seguía pendiente. **Requiere decisión de gerencia** (p. ej. EN_PRUEBA con motivo).
2. **Media de mora distinta, misma clase, en 4 clientes:** C10270 (doc 24.57 / motor 29.50), C10298 (34.50 / 41.40), C10502 (10.43 / 9.13), C10266 (53.67 / 51.56). El documento cuenta como pagadas algunas facturas que el motor excluye por no tener cobro íntegro acreditado por recibos (o con recibos anteriores a la emisión). Las clases no cambian. El motor es el que reproduce los Excel aprobados de Las Vegas y Yoro (55/55); conviene confirmar con quien preparó el documento qué criterio de pago usó.
Los 3 clientes nuevos: C10266 → C (51.56); C10773 y C11156 → en prueba (2 y 1 facturas).

## 5. Procedimiento para aplicarlo a la BD real (requiere aprobación; no ejecutado)
1. Detener cualquier servidor que use la BD (la CLI y el servidor no deben escribir a la vez).
2. Respaldo fresco: `python -m cxc_portal.admin respaldar --estado <estado> --destino <respaldos>` (solo lectura; ya existe `respaldos-pre-migracion-119\portal-20261013-040224.sqlite3` + `.sha256`).
3. `python -m cxc_portal.admin migrar-ruta --estado <estado> --ruta OCCIDENTE --archivo OCCIDENTE_PORTAL_119_CLIENTES_ZONAS_60D.json` (ensayo de solo lectura: revisar el plan).
4. Aplicar: mismo comando con `--aplicar --respaldo <respaldo .sqlite3>`. (La primera apertura con el código actual también crea las tablas nuevas del Portal: alertas, Excel versionado, configuración, feriados, notificaciones.)
5. Hojas de visita v2: copiar `HOJAS DE VISITA OCCIDENTE.xlsx` a `<datos>\01_Rutas\` y `python -m cxc_portal.admin importar-documento --estado … --datos … --ruta OCCIDENTE --archivo <nombre>.xlsx --nueva-version --confirmar --motivo "…"` (la v1 queda HISTÓRICA; no hay salidas vinculadas).
6. Verificar: `python -m cxc_portal.verificar_migracion --estado … --ruta OCCIDENTE --json … --hojas … --semaforo … --datos …` (debe dar 0 fallas salvo C10061, ya explicado).
7. Reiniciar el servidor. La ruta sigue **en confirmación**: marcarla operativa es una acción humana en “Confirmar clientes”.
**Reversa:** `python -m cxc_portal.admin restaurar --estado … --archivo <respaldo> --forzar` (conserva `.pre-restauracion`).

## 6. Bloqueos y riesgos reales
- **Aprobación de Alejandro** para aplicar sobre la BD real (paso 5).
- **Decisión de gerencia sobre C10061** y confirmación del criterio de pago del documento (4 clientes con media distinta).
- La regla “contado = ROJO automático” **revierte** la aprobación del 11/10 (“nunca convertir candidatos en decisión automática”): está implementada como pidió el 12/10 y es reversible con un valor.
- Las clasificaciones usan el **corte histórico 08/10**, no saldos actuales: siguen siendo preliminares hasta tener fuente vigente (BLC).
- Occidente sigue sin ser operativa; no hay salidas ni hojas generadas.
