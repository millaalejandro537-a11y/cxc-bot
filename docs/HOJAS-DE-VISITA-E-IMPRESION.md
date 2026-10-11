# Hojas de visita (Occidente) y flujo de impresión — estado 2026-10-11

## Regla principal
Las hojas de visita son **documentos reutilizables**, no se generan en cada salida. Solo cambian con una **nueva versión confirmada** (clientes, zonas, orden o diseño). Las Vegas, Yoro y las rutas sin la marca “usa hojas de visita” no las utilizan.

## Documento integrado (Occidente · José Carpio)
`OCCIDENTE OCTUBRE - HOJAS DE VISITA COLORES Y COBROS.xlsx` (aprobado): 8 hojas (`Hoja 01`…`Hoja 08`), **116 clientes** (16 por hoja; la 08 tiene 4), en el orden del documento. Colores del documento (preliminares): 31 verde · 38 amarilla · 16 azul · 15 blanca · **15 CANDIDATO** · 1 REVISAR. “CANDIDATO” (rojo claro) es **candidato a solo contado, NO una decisión de gerencia**; el portal lo rotula así en la vista de impresión y en el contenido registrado.
- **Registro:** v1, 2026-10-11, sha256 `58e16ddf76f742de…` (registrado en la BD propia).
- **Almacenamiento privado:** `…\CxC-Datos-Privados\estado\documentos\OCCIDENTE\HOJA_VISITA\v1_58e16ddf76.xlsx`. Escritura exclusiva (`xb`): nunca se sobrescribe un histórico. Fuera de Git y fuera del frontend público; se entrega solo por la API autenticada (`/api/documentos/{id}/descargar`, auditada). La copia de trabajo está en `…\2026-10-08\01_Rutas\`.
- **Clientes propuestos, no confirmados:** los 116 se cargaron como *candidatos* de la ruta (origen “documento v1”). La ruta sigue **EN CONFIRMACIÓN**: una persona define las zonas (prefijos), asigna zona y confirma (Administración → Clientes por confirmar). El documento no trae zonas ni folders; el portal solo sugiere una zona leída del nombre entre paréntesis.
- Tipo de ruta/plazo de Occidente sigue **provisional** (foránea 60 d) hasta confirmarlo.

## Versionado y vínculo con salidas
- **Nueva versión** = `POST /api/admin/rutas/{ruta}/documentos` con `nueva_version`, `confirmar: true` y `motivo` (≥ 10 caracteres). La anterior pasa a HISTÓRICA (no se borra). El mismo contenido (hash) no se registra dos veces.
- Al **crear una salida**, queda vinculada a la versión **vigente en ese momento** (`salida_documentos`); si luego se publica otra versión, esa salida conserva la suya y el portal avisa que hay una más reciente.
- Ver, descargar el Excel y **preparar para imprimir** (una página por hoja, carta horizontal, impresión del navegador → Windows, Mac, iPhone, Android o “guardar PDF”) desde **Hojas de visita**.

## Seguimiento de visitas
Estados: prevista · visitada y firmada · visitada sin firma (motivo) · no visitada (motivo). **La falta de firma no es no visita.** Omisiones consecutivas (últimas 3 salidas en curso/cerradas): *confirmadas* = no visitada en las 3; *sin registro* = revisión humana. “Preparar salida” incluye esas omisiones en las excepciones.

## Impresión futura (solo diseño; **no imprime nada ni hay servicio instalado**)
1. Selección: cada documento (Excel de ruta generado, hoja de visita vigente, “otros”) se agrega a la **cola** (`/api/impresion`) con copias y destino; vendedor solo de sus rutas.
2. Estados: pendiente → tomado → impreso / error / cancelado; historial con quién, cuándo y resultado (auditado).
3. **Agente de impresión** (futuro, en la oficina): lo registra un admin (`/api/admin/agentes-impresion`, token mostrado una vez, solo se guarda su hash); consulta `/api/agente/siguiente` (conexión saliente, sin puertos abiertos), imprime localmente y reporta `/api/agente/{id}/resultado`. Pendiente para esa fase: descarga del archivo por el agente, selección de impresora, política de aprobación (p. ej. gerencia aprueba imprimir), reintentos.
