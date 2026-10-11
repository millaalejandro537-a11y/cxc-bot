# Respaldos cifrados del Portal CxC — diseño, custodia y puesta en marcha

Estado (2026-10-11): **implementado y probado con datos sintéticos (y en contenedor Linux). NO activado:** no existe ninguna tarea programada, no se generaron claves reales y no se respaldó la BD real.

## 1. Qué había y qué se añadió (sin duplicar)
| Pieza | Ya existía | Cambio |
|---|---|---|
| Copia consistente de SQLite + sha256 + integrity_check | `Estado.respaldar` | Se **reutiliza** (genera la copia en un directorio temporal privado que se borra siempre) |
| Espejo de documentos (`.xlsx`) | `Estado.respaldar` | Reutilizado |
| Restauración con verificación de suma/integridad/documentos | `Estado.restaurar`, `verificar_documentos` | Reutilizado: el respaldo cifrado se descifra a un temporal y se entrega a este mismo código |
| Cifrado, destinatarios, rotación, verificación | — | `cxc_portal/respaldo_cifrado.py` (nuevo) |
| Volcado lógico portable | `exportar/importar` | Sin cambios (es migración, **no** respaldo; contiene secretos en claro: no guardarlo sin cifrar) |

## 2. Construcción criptográfica (solo primitivas estándar de `cryptography`)
- Contenido (BD + documentos + manifiesto con sha256 por archivo): **AES-256-GCM**, clave de datos aleatoria **por respaldo**, nonce aleatorio de 96 bits.
- La clave de datos se envuelve con **RSA-OAEP (SHA-256)** para cada destinatario (clave **pública**). Varios destinatarios = varios custodios; cualquiera recupera.
- La cabecera completa (versión, algoritmos, nonce, destinatarios) va como **AAD**: alterar un bit de cabecera o contenido invalida el respaldo.
- La **clave privada** (RSA, 4096 bits por defecto) se guarda cifrada con contraseña (PKCS#8, `BestAvailableEncryption`) y **nunca** está en el servidor: la tarea programada solo conoce claves públicas, de modo que **comprometer el servidor no permite leer respaldos anteriores**. Contrapartida: la tarea no puede probar una restauración completa por sí sola (ver §4).
- Contraseñas: nunca por argumento de línea de comandos; variable `CXC_CLAVE_PRIVADA_PASS` o teclado. Mínimo 16 caracteres.
- Limitación conocida: el respaldo se arma en memoria (adecuado para BD de decenas de MB; revisar si crece a cientos de MB).

## 3. Comandos (`python -m cxc_portal.admin …`)
| Comando | Uso | Quién/dónde |
|---|---|---|
| `generar-claves-respaldo --publica P --privada K` | Crea el par; no sobrescribe | Máquina del custodio, **no** el servidor |
| `respaldar-cifrado --estado D --destino D --publica P [--publica P2] [--diarios 14 --mensuales 6]` | Respaldo + verificación estructural + rotación + `ultimo_respaldo.json` | Servidor / tarea programada |
| `verificar-respaldo --archivo F --privada K` | **Prueba de restauración completa** en carpeta temporal (hashes, integridad, documentos); no toca nada real | Custodio |
| `restaurar-cifrado --estado D --archivo F --privada K [--forzar]` | Restaura BD + documentos (conserva `.pre-restauracion`; reiniciar el portal después) | Custodio / recuperación |

Plantillas **no activas**: `deploy/respaldo.sh` (dentro del contenedor, instalado como `respaldo.sh`) y `deploy/respaldo-diario.ps1` (Tarea Programada de Windows; sin `-Registrar` solo muestra lo que haría).

## 4. Programación, rotación, retención y verificación (propuesta)
- **Frecuencia:** diaria (02:30) más uno manual antes de cada cambio de versión o migración.
- **Retención:** últimos 14 respaldos diarios + el más reciente de cada uno de los últimos 6 meses (`rotar`); nunca se borra el último. Mismo criterio en la copia externa.
- **Copia externa:** la plataforma no debe ser la única copia. `/data/respaldos` se sincroniza a un almacenamiento externo (decisión §6); como los archivos ya están cifrados, ese almacenamiento no necesita ser de confianza.
- **Verificación automática (sin clave privada):** tras cada respaldo se valida formato, cabecera, destinatarios y suma sha256, y se escribe `ultimo_respaldo.json` (fecha, tamaño, destinatarios, rotados) para que un monitor alerte si tiene más de 26 h.
- **Prueba de restauración (con clave privada, por el custodio):** mensual con `verificar-respaldo` sobre el respaldo más reciente, y un simulacro completo (`restaurar-cifrado` a un volumen vacío + arrancar el portal) **antes de habilitar usuarios reales** y cada trimestre. Registrar fecha y resultado.

## 5. Custodia de la clave de recuperación (propuesta; requiere su decisión)
1. **Dos custodios independientes** (p. ej. Alejandro y una persona de confianza), cada uno con **su propio par**; el servidor recibe ambas claves públicas. Cualquiera recupera sin el otro.
2. La **clave privada** (`.pem` cifrado) y su **contraseña** se guardan **por separado**: el archivo en un USB o gestor de contraseñas cifrado, con una copia en caja fuerte; la contraseña en un gestor de contraseñas o en papel en otro lugar. Nunca en el repositorio, en Syncthing/OneDrive, en variables de la plataforma ni junto a los respaldos.
3. Generar las claves **en la máquina del custodio**, no en el servidor. Si una clave se pierde o se expone: generar un par nuevo y agregarlo a los siguientes respaldos (los antiguos siguen abriéndose con las claves anteriores mientras exista alguna).
4. **Perder todas las claves privadas = perder los respaldos de forma irrecuperable.** Por eso el simulacro de restauración (§4) es obligatorio.
5. Salida de un custodio: sustituir su clave pública en la tarea y generar el par del reemplazo.

## 6. Decisiones que faltan para activar
1. Quiénes son los 2 custodios y dónde guardan clave y contraseña.
2. Destino externo de los respaldos (nube cifrada, disco externo, otro equipo) y quién lo opera/paga.
3. Dónde correrá la tarea (cron de Railway, Programador de tareas de la PC) y a qué hora.
4. Política de retención (propuesta: 14 diarios + 6 mensuales) y quién recibe la alerta si falta un respaldo.
5. Autorización explícita para el **primer respaldo real** y para instalar `cryptography` en el entorno compartido de la PC (hoy sus pruebas se omiten allí porque no está instalado; en la imagen Docker ya va fijado).
6. Fecha del simulacro de restauración previo a abrir el acceso a usuarios reales.

## 7. Consistencia entre la CLI y el servidor
El servidor mantenía las rutas evaluadas en memoria y **no veía** los cambios hechos por otro proceso (`integrar-ruta`, importaciones, altas de rutas/clientes/decisiones por CLI) hasta reiniciar. Solución mínima: un contador por tabla del catálogo (`cambios`, mantenido por disparadores SQLite en `vendedores`, `rutas`, `zonas`, `clientes_ruta`, `decisiones`) que el servidor compara en cada lectura de rutas y, si cambió, recalcula. Las escrituras de sesiones y auditoría no lo tocan, así que no hay recálculo innecesario. Los disparadores viajan en el archivo de BD (respaldo/restauración) y se crean solos al abrir BDs antiguas.
