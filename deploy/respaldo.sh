#!/bin/sh
# PLANTILLA (NO ACTIVA). Respaldo cifrado diario para ejecutar dentro del contenedor (cron de la plataforma o servicio programado).
# Solo necesita CLAVES PUBLICAS: el servidor no puede descifrar sus propios respaldos. Variables:
#   CXC_RESPALDO_DESTINO   carpeta de salida (por defecto /data/respaldos). Debe copiarse FUERA del volumen (ver docs/RESPALDOS-CIFRADOS.md).
#   CXC_RESPALDO_PUBLICAS  rutas de claves publicas separadas por coma (p. ej. /data/claves/custodio1.pub.pem,/data/claves/custodio2.pub.pem)
set -eu
DESTINO="${CXC_RESPALDO_DESTINO:-/data/respaldos}"
: "${CXC_RESPALDO_PUBLICAS:?defina CXC_RESPALDO_PUBLICAS con al menos una clave publica}"
ARGS=""
for k in $(echo "$CXC_RESPALDO_PUBLICAS" | tr ',' ' '); do ARGS="$ARGS --publica $k"; done
# shellcheck disable=SC2086
exec python -m cxc_portal.admin respaldar-cifrado --estado "${CXC_ESTADO_DIR:-/data/estado}" --destino "$DESTINO" $ARGS \
  --diarios "${CXC_RESPALDO_DIARIOS:-14}" --mensuales "${CXC_RESPALDO_MENSUALES:-6}"
