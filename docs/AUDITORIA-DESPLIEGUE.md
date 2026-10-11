# Auditoría técnica previa al despliegue — Portal CxC (2026-10-11)

Alcance: revisión de código, configuración y archivos de despliegue; pruebas solo con **datos sintéticos**. No se desplegó nada, no se contrató nada, no se tocó DNS, no se subieron datos reales, no se arrancó Docker Desktop (apagado). Occidente sigue sin ser operativa.

## 1. Veredicto
**Aplicación lista para un piloto detrás de un proxy HTTPS; el empaquetado en contenedor NO está validado** (no hay Docker/Linux disponible sin arrancar Docker Desktop). Lo que falta antes de desplegar con datos reales está en la sección 4.

## 2. Verificado (con evidencia)
| Área | Resultado |
|---|---|
| Arranque como proceso aparte con los mismos argumentos del `CMD` (datos sintéticos, loopback) | OK: `/api/health` 200 (también con Host de monitor), frontend 200, API sin sesión 401, Host falso 403, login con cookie `Secure` + HSTS, 2FA exigido, cambio de clave obligatorio. |
| Código fuera de Windows | Sin `C:\`, `msvcrt`, `win32` ni rutas absolutas en `cxc_portal`/`cxc_bot`; `pathlib` en todo; dependencias: `openpyxl` + biblioteca estándar; `zoneinfo` con `tzdata` explícito. **No ejecutado en Linux** (ver 4). |
| Frontend | `vite build` OK (212 kB JS / 64 kB gzip), `viewport` móvil presente. `npm audit`: 2 hallazgos (esbuild/vite) **solo del servidor de desarrollo**; no viajan en la imagen (solo `dist` estático). React 18.3.1 sin hallazgos. |
| Autenticación | scrypt, política de clave, bloqueo por intentos, sesiones 30 min/8 h con token guardado como hash, CSRF, SameSite=Strict, HttpOnly, Secure tras proxy, anti-enumeración por tiempo. |
| 2FA | TOTP RFC 6238 con anti-repetición, códigos de recuperación hasheados, obligatorio para todos los roles al exponer fuera de loopback; el servidor se niega a arrancar expuesto sin 2FA. |
| Control de acceso | RBAC aplicado en la API, vendedor limitado a sus rutas, descargas de documentos con comprobación de ruta e integridad por hash, auditoría de denegaciones. |
| Perímetro | `X-Perimeter-Token` con comparación en tiempo constante; Host/Origin permitidos. |
| Respaldo/restauración | `respaldar` (API de respaldo de SQLite, con servidor en marcha, integrity_check + sha256), `restaurar` verifica suma e integridad, conserva `.pre-restauracion`. |

## 3. Fallos encontrados y corregidos (con pruebas en `tests/test_despliegue.py`)
1. **El healthcheck de la plataforma fallaba (bloqueante).** La validación de Host se hacía antes de `/api/health`; Railway consulta con su propio Host y habría recibido 403 → despliegue marcado como fallido. Ahora `GET/HEAD /api/health` se atiende con cualquier Host (solo devuelve `ok/db`); el resto sigue exigiendo Host y token de perímetro.
2. **Pérdida de datos en migración y respaldo.** `exportar/importar` omitía `documentos`, `salida_documentos`, `cola_impresion` y `agentes_impresion`, y los `.xlsx` de las hojas de visita (v1 de Occidente) viven en disco, no en la BD; `respaldar` tampoco los copiaba. Migrar o restaurar solo la BD habría dejado versiones sin archivo. Ahora el volcado incluye esas tablas y los archivos (base64, validados contra su sha256 antes de escribir nada) y `respaldar` mantiene una copia espejo de `documentos/`. Los volcados antiguos siguen importando.
3. **IP de cliente falsificable detrás del proxy.** Se tomaba la **primera** entrada de `X-Forwarded-For` (la controla el atacante), lo que permitía eludir el bloqueo por intentos. Ahora se toma la entrada que agregó el proxy de confianza (última; `CXC_PROXY_HOPS` si hay más de uno).
4. **Conexiones colgadas (slowloris).** Sin timeout de socket un cliente lento retenía un hilo indefinidamente. Timeout de 30 s.
5. **Dockerfile:** (a) el volumen se monta como root y sobreescribe el `chown` de la imagen → la app (UID 10001) no habría podido escribir la BD; se añadió `deploy/entrypoint.sh` que corrige propiedad y baja privilegios con `setpriv`; (b) se quitó `VOLUME` (la documentación de Railway no confirma que lo respete); (c) dependencias de ejecución fijadas (`openpyxl==3.1.5`, `tzdata`) en lugar de `>=`; (d) `PORTAL_HOST` por defecto desde `RAILWAY_PUBLIC_DOMAIN`; (e) `.gitattributes` fuerza LF en scripts (en Windows un `.sh` con CRLF falla en Linux); (f) `.dockerignore` ampliado (BD, WAL, credenciales, `.env`).

## 4. Qué falta (no corregible localmente o fuera de alcance)
**Bloqueantes antes de datos reales**
- **Construir y probar la imagen en Linux** (requiere arrancar Docker Desktop: pendiente de su autorización) y correr la suite dentro. Hasta entonces el `entrypoint.sh` (`setpriv`) y el Dockerfile son **no verificados**. Alternativa de la plataforma: variable `RAILWAY_RUN_UID=0` (documentada por Railway) a cambio de ejecutar como root.
- **Respaldo programado externo.** Existe el comando, pero nada lo ejecuta ni lo saca del volumen. Falta un job diario (cron de la plataforma o servicio aparte) que copie BD + `documentos/` a almacenamiento **cifrado** externo. Los respaldos contienen hashes de clave y **semillas 2FA en claro**: deben cifrarse.
- **Primer administrador en la nube:** hoy se crea por CLI dentro del contenedor (`railway ssh` + `admin crear-usuario`, con clave por teclado, no `--generar-clave`: ese modo deja un `credenciales_iniciales.txt` en el volumen).
- **Cómo llegan documentos y datos al servidor:** `importar_documento` lee de `<datos>/01_Rutas` del disco; en la nube no hay un mecanismo de carga. O se migra con `exportar/importar` (contiene datos reales → requiere su aprobación) o se construye una carga autenticada.
- **Decisión de datos financieros** (sección 6).
**Recomendados**
- Costo de scrypt N=2^14 por debajo de la guía actual de OWASP (≥2^17 con r=8, o 2^16/p=2). Subirlo exige memoria (128 MB a 2^17) y mejor probarlo con el tamaño de instancia elegido; los hashes guardan sus parámetros, así que se puede migrar al iniciar sesión.
- Cifrar en reposo las semillas TOTP (hoy en claro en la BD).
- Límite de intentos y retos 2FA en memoria: válidos solo con 1 réplica (ya es la configuración) y se reinician con el proceso.
- Migraciones: `CREATE IF NOT EXISTS` + `ALTER ADD COLUMN`, sin versionado real (`schema_version` fija en 1). Suficiente para el piloto; antes de Postgres conviene un sistema de migraciones.
- Alerta externa sobre `/api/health` y tope de gasto en la plataforma.
- `vite` 5.4.11 → parche 5.4.x para limpiar `npm audit` (solo desarrollo).

## 5. Alternativas de alojamiento
| Opción | Seguridad | Costo | Mantenimiento | PC / Mac / iPhone |
|---|---|---|---|---|
| **A. PC + Tailscale** (`tailscale serve`) | Sin puertos abiertos; datos no salen de la PC; solo dispositivos autorizados + login + 2FA | US$0 (plan personal gratis: hasta 6 usuarios, dispositivos ilimitados — oficial) | Bajo, pero **depende de que la PC esté encendida**, sin SLA ni respaldo propio; cada empleado necesita la app Tailscale | Sí (apps oficiales) |
| **B. PC + Cloudflare Tunnel/Access** | Sin puertos abiertos; URL pública protegida por Access | Túnel gratis; dominio ≈ US$10/año (**estimación**); Access gratis hasta 50 usuarios (**no verificado hoy**) | Medio; PC encendida | Sí, solo navegador |
| **C. Railway Hobby + volumen + SQLite** (recomendada para producción) | TLS gestionado, aislado, datos financieros en un tercero | Suscripción US$5 con US$5 de uso incluido (**oficial**); RAM US$10/GB, CPU US$20/vCPU, volumen US$0.15/GB, salida US$0.05/GB (**oficial**). Para ~0.25 GB RAM, CPU baja y 1 GB: **≈ US$5–8/mes (estimación mía)** | Bajo; 1 réplica obligatoria (Railway no permite réplicas con volumen — oficial) | Sí, cualquier navegador |
| D. Render (+Postgres gestionado) | Similar a C | ≈ US$27–35/mes (**estimación sin verificar**; la página no entregó precios) | Bajo; Postgres con respaldos | Sí |
| E. VPS (Hetzner u otro) | Máxima flexibilidad, tú aplicas parches/TLS | ≈ EUR 4–7 + respaldos (**estimación sin verificar**) | Alto | Sí |

## 6. Recomendación
**Fase 1 (piloto, sin sacar datos financieros): opción A.** Misma imagen/proceso, cero costo y cero datos fuera de la PC; sirve desde el iPhone y la Mac con Tailscale. Limitación honesta: disponibilidad = PC encendida. Requisitos previos: servicio de Windows de arranque automático y respaldo diario cifrado a otra ubicación.
**Fase 2 (producción): opción C**, proyecto Railway propio de Cosprobell (separado de BLC), 1 réplica, SQLite en volumen, respaldo diario externo cifrado, tope de gasto y Cloudflare Access opcional. Pasar a Postgres solo cuando haya concurrencia real o varias réplicas. Motivo: es la ruta más barata que da HTTPS, reinicios y disponibilidad sin administrar servidores. Esta fase **no debe iniciar** hasta cerrar los bloqueantes de la sección 4 y la decisión de datos.

## 7. Decisiones que requieren su aprobación
1. Fase 1 (Tailscale en la PC y dispositivos, instalar software) **o** ir directo a nube.
2. ¿Pueden salir datos financieros/clientes (snapshot SAP, BD con Occidente) a un proveedor externo? ¿Requiere visto bueno de contabilidad/BLC?
3. Arrancar Docker Desktop para construir y probar la imagen en Linux.
4. Cuenta/facturación de Railway a nombre de Cosprobell, tope de gasto, y dominio/subdominio.
5. Hacer `git push` (el código está sin push; la nube necesita una fuente).
6. Almacenamiento externo cifrado para respaldos y quién guarda la clave.
7. Cuentas reales de usuarios y quién administra el 2FA.
