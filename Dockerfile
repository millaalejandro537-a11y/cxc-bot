# BORRADOR PARA REVISION (no construido ni desplegado). Imagen unica: frontend React construido + backend Python (solo biblioteca estandar + openpyxl).
FROM node:24-slim AS web
WORKDIR /web
COPY portal-web/package.json portal-web/package-lock.json ./
RUN npm ci
COPY portal-web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=America/Tegucigalpa PORT=8080
RUN useradd --create-home --uid 10001 app && mkdir -p /data/estado /data/datos && chown -R app /data
WORKDIR /app
# Dependencias de ejecucion FIJADAS (requirements.txt incluye pytest, que no va en produccion). tzdata explicito: no depender del sistema base.
RUN pip install --no-cache-dir "openpyxl==3.1.5" "tzdata==2025.2"
COPY cxc_bot ./cxc_bot
COPY cxc_portal ./cxc_portal
COPY config ./config
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY --from=web /web/dist ./portal-web/dist
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && chmod 755 /usr/local/bin/entrypoint.sh
# cxc-reporting-core: NO se copia ni se duplica aqui. Se instala FIJADO a un commit desde su propio repositorio privado (ver docs/NUBE-ALOJAMIENTO.md):
#   ARG CORE_REF=<commit> ; RUN pip install "git+https://<deploy-token>@github.com/millaalejandro537-a11y/cxc-reporting-core@${CORE_REF}"
# y se deja CXC_REPORTING_SRC vacio. Mientras tanto el portal funciona sin reportes del core (responde 503 en /api/reportes/*).
# Sin VOLUME ni USER: las plataformas montan el volumen en /data (propiedad root); el entrypoint corrige la propiedad y baja a UID 10001.
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+__import__('os').environ.get('PORT','8080')+'/api/health',timeout=4).status==200 else 1)"
# Detras del TLS de la plataforma: HTTPS+HSTS+cookies Secure, 2FA obligatorio, hosts permitidos explicitos.
# Variables: PORTAL_HOST (o RAILWAY_PUBLIC_DOMAIN), CXC_PERIMETER_TOKEN (opcional), CXC_PROXY_HOPS (proxies delante; 1 por defecto)
CMD ["sh", "-c", "exec python -m cxc_portal.server --host 0.0.0.0 --datos /data/datos --estado /data/estado --detras-de-proxy-https --hosts-permitidos \"${PORTAL_HOST:-$RAILWAY_PUBLIC_DOMAIN}\" ${CXC_PERIMETER_TOKEN:+--exigir-perimetro}"]
