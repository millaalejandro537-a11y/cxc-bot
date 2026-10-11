# Alojamiento permanente en la nube — evaluación y propuesta (2026-10-11)

Estado: **propuesta para revisión. No se desplegó nada, no se compró nada, no se tocó DNS ni se creó ningún recurso.** `Dockerfile`, `.dockerignore` y `railway.toml` son borradores **no construidos** (Docker Desktop está apagado y su arranque requiere aprobación).

## 1. Qué corre en Linux y qué depende de Windows (auditoría del código real)
| Componente | Linux | Observación |
|---|---|---|
| `cxc_portal` (API, BD propia, seguridad, 2FA, documentos, cola de impresión) | Sí (por diseño) | Solo biblioteca estándar + `openpyxl`; rutas con `pathlib`; sin `msvcrt`/`win32`/rutas `C:\` en el código de ejecución. **No probado en Linux** (no hay Linux disponible sin arrancar Docker). |
| `cxc_bot` (semáforo, generador de Excel, validadores) | Sí | `zoneinfo` requiere `tzdata` solo en Windows; en Linux usa el sistema. |
| `portal-web` (React/Vite) | Sí | Se construye en la imagen (Node) y se sirve como estático por el mismo servidor. |
| `cxc-reporting-core` | Sí (Python ≥ 3.9, `openpyxl`) | Hoy se lee por `CXC_REPORTING_SRC`. En nube: instalarlo **fijado a un commit** desde su repo privado (`pip install git+…@<commit>`), sin copiarlo ni duplicarlo. |
| Pruebas | Parcial | Algunas rutas de prueba son de Windows (`CORE_SRC`) y se omiten solas; la regresión con datos reales necesita los datos privados. |
| Windows-only reales | — | **Ninguno en la app.** Lo local de Windows es el entorno de trabajo (OneDrive/Syncthing/Docker Desktop) y la futura **impresión** en la oficina. |
| OneDrive | No lo usa el portal | `Cosprobell_CXC_final.xlsx` (OneDrive) es del sistema Excel heredado; el portal no lo toca. |

**¿Todo en nube o un trabajador local de CXC BOT?** Todo en nube. La generación de Excel (`openpyxl`) y el semáforo no dependen de la PC. El único componente que *debe* quedar local es el **agente de impresión** (impresoras de la oficina), que se conecta hacia afuera a la API con su propio token (ya diseñado: `/api/agente/*`); no recibe conexiones entrantes. No se necesita un worker local para generar rutas.

## 2. Base de datos: persistencia, concurrencia, respaldos, PostgreSQL
- **Hoy:** SQLite (WAL) en `estado/portal.sqlite3`, con respaldo consistente (`admin respaldar`: sha256 + integrity_check), restauración verificada y exportación lógica portable (`admin exportar/importar`), todo probado. Preserva decisiones, usuarios, visitas, documentos y auditoría.
- **Piloto (≤ ~15 usuarios, escrituras ocasionales):** SQLite en un **volumen persistente** con **1 réplica** es suficiente y es lo más barato y simple. Respaldos: volumen con respaldo programado de la plataforma **más** `admin respaldar` diario a almacenamiento externo cifrado (la plataforma no es la única copia).
- **PostgreSQL:** recomendado al pasar a varios usuarios simultáneos, >1 réplica o exigencias de recuperación puntual (PITR). La migración está acotada: el SQL está concentrado en `estado*.py`; los diferencias a portar son `INSERT OR IGNORE/REPLACE`, `lastrowid`, `PRAGMA` y `COLLATE NOCASE`. Camino seguro ya construido: `admin exportar` → (adaptador Postgres) → `importar` en una BD vacía, sin mezclar. **No implementado ni probado** (no hay Postgres disponible aquí); estimación: 1–2 días + pruebas.
- Restauración: se prueba en cada release con `admin restaurar` a una BD temporal (ya cubierto por pruebas automáticas).

## 3. Comparación (precios consultados el 2026-10-11; confirmar antes de contratar)
| Opción | Qué incluye | Costo mensual estimado (piloto) | Pros / contras |
|---|---|---|---|
| **Railway (Hobby)** | $5/mes con $5 de uso incluido; uso: RAM $10/GB, CPU $20/vCPU, volumen $0.15/GB, salida $0.05/GB; volúmenes con respaldos programables (diario 6 d, semanal 1 mes, mensual 3 meses) | **≈ US$5–10** con SQLite+volumen (≈0.25 GB RAM, CPU casi ociosa, 1 GB de volumen). Con Postgres gestionado ≈ US$10–20 | + HTTPS/dominio incluidos, despliegue desde Docker, healthcheck y reinicios, proyecto aislado. − 1 réplica con volumen; Hobby = una persona (usar cuenta de Cosprobell). Figuras de uso son **mi estimación**, no publicadas. |
| Render | Starter $7 + disco ~$0.25/GB; Postgres Basic ~$19–20 | ≈ US$27–35 | + Postgres gestionado con respaldos. − Más caro; cuota de workspace. |
| VPS (p. ej. Hetzner CX/CPX) | Máquina completa; respaldos +20 % | ≈ EUR 4–7 + respaldos | + Más barato. − Administrar SO, parches, TLS, respaldos y monitoreo uno mismo; precios US inciertos (cambiaron en abril 2026). |
| Cloudflare Access (opcional) | Gratis hasta 50 usuarios; USD 7/usuario/mes después | US$0 (piloto) | + Segunda barrera antes de llegar al servidor. − Requiere dominio en Cloudflare. |

**Recomendación: Railway (Hobby) con la imagen única + 1 volumen + SQLite para el piloto**, proyecto propio de Cosprobell, y pasar a Postgres cuando haya concurrencia real. Es la opción más barata que cumple HTTPS, reinicios y respaldos sin administrar servidores. Render es la alternativa si se quiere Postgres gestionado desde el día 1.

### Limitar el gasto
Tope de gasto/“usage limit” en el proyecto de Railway (alerta y corte duro, p. ej. US$15/mes) · 1 réplica fija · sin servicios extra · volumen pequeño (1–2 GB) · alertas de facturación al correo de Cosprobell · revisar uso a los 7 días.

## 4. Seguridad de acceso (qué existe y qué se activa)
- **Ya implementado y probado:** autenticación (scrypt), sesiones con expiración, CSRF, bloqueo por intentos, **2FA TOTP** con códigos de recuperación (obligatorio para todos los roles al exponer fuera de loopback), RBAC en la API, cabeceras de seguridad, host/origen, TLS directo o tras proxy, **token de perímetro** (`--exigir-perimetro`, `CXC_PERIMETER_TOKEN`) que rechaza cualquier petición que no pase por el perímetro, `/api/health` con prueba de BD, respaldo/restauración.
- **Sin Cloudflare (solo Railway):** TLS y dominio de Railway (`*.up.railway.app` o un subdominio propio), `--hosts-permitidos` con ese nombre; el servidor **es** público, protegido por login + 2FA + límites. Es aceptable para un piloto con pocos usuarios, pero la superficie es Internet.
- **Con Cloudflare Access (adicional, no obligatorio):** DNS del subdominio en Cloudflare + política Access (correos autorizados + MFA) + regla de transformación que agrega `X-Perimeter-Token`; así el origen de Railway rechaza el acceso directo. **Cloudflare Tunnel no es necesario** si Railway aloja la app.
- **Secretos:** solo variables de entorno de la plataforma (token de perímetro, credenciales de BD de lectura cuando existan); nunca en Git. Rotación documentada.
- **Monitoreo/recuperación:** healthcheck de la plataforma, reinicio automático (`ON_FAILURE`), alerta externa gratuita (UptimeRobot/Cloudflare) sobre `/api/health`, respaldo diario externo, `admin restaurar` probado.

## 5. Datos financieros en la nube — decisión que requiere aprobación
Subir el snapshot SAP y la BD propia a un proveedor externo saca datos financieros de la PC. Requiere decisión de Alejandro (y, si aplica, de contabilidad/BLC). Alternativa de menor riesgo para el piloto: **mantener los datos en la PC y exponer solo el acceso** (Tailscale/Cloudflare Tunnel, ver `docs/DESPLIEGUE-SEGURO.md`). Con datos vivos, la fuente será de solo lectura contra Cosprobell BD con credencial SELECT; desde un proyecto Railway separado solo sería alcanzable por su dirección pública con SSL (no por red privada), a revisar con BLC.

## 6. Pasos para activar (todos requieren aprobación previa)
1. Cuenta de Railway de Cosprobell (facturación y tope de gasto) → proyecto nuevo e independiente.
2. Repositorio privado/fuente de despliegue: hoy el código está en `cxc-bot` (GitHub) con 5+ commits **sin push**; hace falta autorizar el push (o desplegar desde una imagen construida localmente).
3. Construir y probar la imagen **en local con Docker** (requiere arrancar Docker Desktop) y correr la suite dentro del contenedor Linux.
4. Variables: `PORTAL_HOST`, (`CXC_PERIMETER_TOKEN`), `CXC_FUENTE=snapshot`; volumen en `/data`; subir `/data/datos` (snapshot) solo si se aprueba el punto 5.
5. Crear usuarios (`admin crear-usuario`), activar 2FA, ensayar respaldo y restauración.
6. Subdominio autorizado (DNS) y, si se decide, Cloudflare Access + token de perímetro.
7. Ensayo de aceptación con usuarios reales y plan de reversa (apagar el servicio; los datos siguen en la PC y en el respaldo).
