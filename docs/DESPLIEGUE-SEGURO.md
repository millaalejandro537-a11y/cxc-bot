# Despliegue seguro del portal CxC — estado y opciones

Estado (2026-10-10): **nada publicado, nada desplegado, ningún recurso ni costo creado.** El portal corre solo en `127.0.0.1:8780` en la PC. Este documento deja la aplicación lista y verificable para exponerla cuando se apruebe un destino.

## Revisión de infraestructura existente (solo lectura)
| Recurso | Estado | Uso posible |
|---|---|---|
| PC Windows (esta) | Disponible, Docker Desktop apagado | Anfitrión del portal y de los datos (los datos financieros no salen de la PC) |
| JARVIS: WireGuard (UDP 51820, red 10.66.66.0/24), HTTPS por túnel (8443) | Configurado para iPhone; es de **JARVIS** | Podría dar acceso privado desde móviles, pero es infraestructura ajena: no se toca sin decisión de su dueño |
| Vercel (cosprobell-web) | Existe para la **tienda** | No mezclar: otro proyecto, y Next.js no ejecuta este backend Python |
| Railway (Cosprobell BD de BLC / proyecto Helsing) | Administrado por BLC | **No** alojar el portal ahí; solo lectura de datos con credencial SELECT cuando exista |
| Tailscale / cloudflared / Caddy / nginx | No instalados | Ver opciones |
| openssl, Python 3.12, Node 24 | Instalados | Certificados de prueba y construcción |

## Lo que la aplicación ya cumple (verificado con pruebas)
- Autenticación obligatoria (usuario + contraseña scrypt, política ≥ 12 caracteres/3 de 4 clases, bloqueo tras 5 intentos, sesiones de 30 min de inactividad / 8 h máximo, cambio obligatorio en el primer acceso, cierre de sesiones al cambiar clave/rol/desactivar).
- Roles **aplicados en la API** (admin / gerencia / vendedor), CSRF en toda mutación, cabeceras (CSP, X-Frame-Options, nosniff, HSTS con HTTPS), Host/Origin permitidos (anti DNS-rebinding), sin CORS, errores sin detalle interno, auditoría de accesos y cambios.
- BD propia SQLite **separada** (usuarios, rutas, decisiones, visitas, auditoría) en el directorio de estado privado, nunca en Git.
- Exposición: `--host` fuera de loopback **se rechaza** salvo con TLS (`--tls-cert/--tls-key`) o `--detras-de-proxy-https` **y** `--hosts-permitidos`.

## Opciones de acceso desde cualquier dispositivo (decisión de Alejandro)
| Opción | Cómo | Costo | Seguridad | Dispositivos |
|---|---|---|---|---|
| **A. Tailscale en la PC + los dispositivos** (recomendada para el piloto) | `tailscale serve` publica `https://<pc>.<tailnet>.ts.net` (certificado válido) solo dentro de la red privada; la app corre `--detras-de-proxy-https --hosts-permitidos <pc>.<tailnet>.ts.net` | Gratis (plan personal) | Sin puertos abiertos; solo dispositivos autorizados + login de la app | Windows, Mac, iPhone, iPad, Android (app Tailscale) |
| B. Cloudflare Tunnel + Access | `cloudflared` publica un dominio propio; Access añade un 2.º factor | Dominio (~USD 10/año); túnel gratis | Sin puertos abiertos; URL pública protegida por Access | Cualquier navegador, sin instalar nada en el dispositivo |
| C. Servidor en la nube (VM/PaaS) | Copiar la app y los datos a un proveedor | **Costo mensual** y datos financieros fuera de la PC | Requiere endurecimiento, respaldos, parches | Cualquiera |
**Los tres exigen aprobación**: instalar software en la PC, y/o dominio/costo, y/o sacar datos financieros de la PC (C). Mientras la PC haga de servidor debe estar encendida y con el portal en ejecución.

## Puesta en marcha (cuando se apruebe A o B)
1. `python -m cxc_portal.admin crear-usuario --estado <estado> --usuario <u> --rol admin|gerencia|vendedor [--vendedor "Nombre"] --generar-clave` (clave inicial en `<estado>/credenciales_iniciales.txt`; se cambia en el primer acceso; borrar el archivo después).
2. Arrancar detrás del proxy: `python -m cxc_portal.server --datos <datos> --estado <estado> --reporting-src <core>\src --detras-de-proxy-https --hosts-permitidos <host público>` (escucha en 127.0.0.1:8780; el proxy termina TLS). Ejemplos: `deploy/Caddyfile.example`, `deploy/portal.env.example`.
3. Comprobar: `curl -I https://<host>/api/rutas` ⇒ 401; login; `Secure` en la cookie; rol vendedor sin acceso a reportes; `Host` ajeno ⇒ 403.
4. Respaldos: copiar `<estado>/portal.sqlite3` (WAL: con el servidor detenido o usando `.backup`) a un destino cifrado.

## Pendiente antes de exponerlo a empleados reales
Decidir A/B/C · crear las cuentas reales (admin, gerencia, vendedores) · pruebas de aceptación con usuarios · política de contraseñas/2FA (la app hoy no tiene 2.º factor; en B lo aporta Cloudflare Access) · respaldo y restauración probados · registro de quién tiene acceso · servicio de Windows/arranque automático del portal.
