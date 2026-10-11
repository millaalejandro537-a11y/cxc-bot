#!/bin/sh
# Arranque del contenedor. Los volumenes de las plataformas (p. ej. Railway) se montan como root y sobre-escriben el chown de la imagen:
# se corrige la propiedad de /data y se BAJA a un usuario sin privilegios antes de ejecutar la aplicacion.
set -e
if [ "$(id -u)" = "0" ]; then
  mkdir -p /data/estado /data/datos
  chown -R 10001:10001 /data
  exec setpriv --reuid=10001 --regid=10001 --clear-groups -- "$@"
fi
exec "$@"
