# SaaS de reservas con precio cerrado — taxistas del APC de Madrid

MVP del plan v2 (canal web propio, sin WhatsApp): el pasajero reserva en la
web del taxista desde un enlace o QR, el motor calcula el precio cerrado con
las tarifas oficiales y se emite el justificante del art. 22 bis ORT antes
del servicio.

## Qué incluye este MVP (fase F1 del plan)

- **Motor de precio cerrado** (`app/pricing/`): tabla oficial 2026 versionada
  (BOCM-290-2025), T1/T2 por día/hora con festivos de Madrid capital,
  velocidad de arrastre por tramo, cambio de tarifa en ruta, suplemento de
  Navidad, peaje consentido, descuento NO₂ del 10 %, redondeo a 0,05 €.
  Todo en `Decimal` y con payload auditable persistido por cálculo.
- **Formulario de reserva del pasajero** (`/t/{slug}`): origen, destino,
  fecha/hora, decisión de peaje, oferta con condiciones y línea RGPD.
  Sin registro ni JavaScript obligatorio; accesible (WCAG 2.1 AA de base).
- **Justificante de precontratación**: numeración correlativa por serie con
  bloqueo de fila, HTML archivado con hash SHA-256 (PDF automático si
  WeasyPrint está instalado), enlace permanente `/r/{token}` con cancelación.
- **Panel del taxista** (`/panel`): agenda, reserva telefónica asistida
  (mismo motor), flag manual de escenario NO₂, QR del enlace de reserva,
  cambio de estado y descarga de justificantes.
- **Notificaciones por email** (plan §5): confirmación con el justificante
  adjunto, aviso de cancelación y recordatorio previo a la recogida
  (`python -m app.jobs recordatorios`, para cron). Proveedores conmutables:
  `console` (desarrollo) o `resend`; el envío nunca bloquea la reserva y
  queda registrado en `notificaciones`.
- **Web Push** (plan §5): el pasajero puede activar avisos en su dispositivo
  desde la página de la reserva (service worker + VAPID). Recordatorio y
  cancelación llegan por push además de por email. Claves con
  `python -m app.jobs generar-vapid`; sin claves, el botón no se ofrece y
  nada se rompe.
- **API JSON pública** (`/api/t/{slug}/...`) según el plan §11, con rate
  limit por IP, honeypot y límite de reservas activas por teléfono.
- **Multi-tenant** por `tenant_id` en PostgreSQL/SQLite (RLS pendiente para
  producción).
- **Textos legales** (checklist §17): aviso legal y política de cookies del
  sitio (`/aviso-legal`, `/cookies`; sin banner: no hay cookies de terceros),
  política de privacidad por taxista (`/t/{slug}/privacidad`, enlazada desde
  la línea RGPD del formulario) y plantilla del contrato de encargo art. 28
  RGPD (`docs/contrato-encargo-tratamiento.md`). Los datos del proveedor se
  configuran con `TAXI_PROVEEDOR_*`. **Borradores pendientes de revisión
  letrada antes del lanzamiento.**

- **Taxistas favoritos**: el pasajero registrado guarda taxistas (estrella en
  su página de reserva) y los tiene a un toque en /mis-reservas.
- **Bolsa de viajes** (/viaje): el pasajero publica un trayecto con precio
  estimado y el primer taxista disponible que lo acepte se lo lleva (bloqueo
  de fila contra dobles asignaciones); la solicitud se convierte en reserva
  normal con justificante y aviso al pasajero. Cada taxista activa o
  desactiva la bolsa desde su panel. **Nota regulatoria**: esta pieza acerca
  la plataforma a la intermediación (el resto es marca blanca por taxista);
  revisar su encaje antes del lanzamiento comercial.
- **Autocompletado y mapas**: Leaflet servido en local (sin CDN), sugerencias
  de direcciones al escribir, botón «usar mi ubicación» (geolocalización +
  geocodificación inversa) y ruta dibujada en la oferta.
- **Perfiles de taxista**: foto, presentación y valoraciones de pasajeros
  (1–5 estrellas tras servicio completado, una por reserva). Buscador
  público en /taxistas y perfil en /t/{slug}/perfil. El taxista edita su
  perfil desde el panel.
- **Bolsa con cercanía**: sección propia del panel (/panel/bolsa) con botón
  de geolocalización para ordenar las solicitudes por distancia a la
  recogida. Publicar un viaje exige cuenta de pasajero.
- **PostgreSQL de serie**: driver psycopg incluido, URLs `postgres://` de
  Render/Heroku normalizadas automáticamente y `pool_pre_ping` activado.

- **Reserva con confirmación del taxista**: la web muestra al pasajero solo
  el **precio máximo**; la reserva queda pendiente hasta que el taxista la
  acepta (puede aplicar un descuento) o la rechaza. El justificante se emite
  al aceptar y el pasajero sigue el estado en su enlace `/s/{token}`.
- **Avisos al taxista**: email y Telegram (Bot API gratuito; token global
  `TAXI_TELEGRAM_BOT_TOKEN`, cada taxista vincula su chat desde el perfil)
  al recibir una reserva y cuando entra un viaje nuevo en la bolsa. Los
  mensajes de Telegram llevan la **hoja de ruta** completa (con la
  distancia hasta la recogida si el taxista comparte su ubicación) y un
  **menú de botones inline**: aceptar al precio máximo, abrir la recogida
  en el mapa, rechazar, o «Ajustar el precio…» con descuentos del 5 al
  30 % (importe resultante ya calculado en cada botón) e incluso escribir
  un **precio exacto** en el chat. El precio pactado nunca supera el
  máximo regulado y queda reflejado en el payload auditable. Los avisos
  salen en segundo plano: la reserva no espera al SMTP.
- **Autocompletado con Photon** (komoot): búsqueda de calles pensada para
  autocompletar (entiende «gran vi» a medias), con sesgo a Madrid y caché;
  OSRM sigue calculando la ruta. `TAXI_PHOTON_URL` para instancia propia.
- **Política de precios del taxista**: descuento comercial (0–30 %, legal
  porque el precio cerrado es un máximo) y suplemento de recogida entre 0 y
  el tope reglamentario de 5,00 €. Se configura en el perfil y puede
  ajustarse viaje a viaje al aceptar en la bolsa.
- **QR social**: el QR del taxista lleva a su perfil público (foto,
  valoraciones y botón de reserva), no directo al formulario.
- **Cuenta de intermediario** (/registro/intermediario): hoteles y
  establecimientos piden taxis para sus clientes; sus solicitudes van a la
  bolsa identificadas con el establecimiento y siguen el estado con enlace
  al justificante.

- **Modelo de listado neutro (Star Taxi App)**: el pasajero elige taxista y
  contrata con él (así se indica en la oferta y en el pie); la plataforma
  solo calcula el máximo oficial. El listado ordena por antigüedad de alta
  (invariante con test). Términos en `/terminos`.
- **Cumplimiento**: tabla tarifaria externalizada a
  `app/pricing/datos/tarifas-2026.json` (`TAXI_TARIFAS_FICHERO` para
  cambiarla sin tocar código) con **zonas excluidas** — los trayectos con
  origen/destino en el aeropuerto (tarifa fija T3/T4) se rechazan con
  mensaje claro; **verificación DSA art. 30**: el taxista nuevo queda
  «pendiente» (no listado) hasta que el titular lo aprueba
  (`/api/admin/pendientes` y `/api/admin/verificar`, con
  `TAXI_ADMIN_TOKEN`); **quejas** (`/quejas`, también canal de notificación
  y retirada) y **exportación art. 47 ORT** por rango de fechas
  (`/api/admin/export/art47.csv?conjunto=servicios|demandas|quejas`);
  **retención RGPD**: anonimizado automático vía cron pasados
  `TAXI_RETENCION_MESES` (12) meses; aviso PMR (flag `TAXI_PMR_ACTIVADO`,
  pendiente de decisión del titular).

## Arranque rápido

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
TAXI_SEED_DEMO=1 .venv/bin/uvicorn app.main:app --reload
# → http://localhost:8000/t/demo  (pasajero)
# → http://localhost:8000/panel   (demo@example.com / demo1234)
```

Tests: `.venv/bin/python -m pytest`

## Configuración (variables `TAXI_*`)

| Variable | Por defecto | Uso |
|---|---|---|
| `TAXI_DATABASE_URL` | SQLite en `var/` | pega la URL de PostgreSQL tal cual (acepta `postgres://…`) |
| `TAXI_ROUTE_PROVIDER` | `fake` | `google` para Geocoding + Routes reales |
| `TAXI_GOOGLE_MAPS_API_KEY` | — | obligatoria con `google` |
| `TAXI_SECRET_KEY` | insegura | cámbiala en producción |
| `TAXI_BASE_URL` | `http://localhost:8000` | para el QR y enlaces |
| `TAXI_SEED_DEMO` | `0` | crea el tenant demo al arrancar |

El proveedor `fake` geocodifica y enruta de forma determinista (sin red):
todo el flujo funciona en local sin API key.

## Activar el correo real

**En Render (y otros PaaS) el SMTP saliente está bloqueado** — Gmail por
SMTP da `Network is unreachable`. Usa un proveedor por API HTTP:

### Opción A: Brevo (gratis 300/día, sin dominio propio) — para Render

1. Crea cuenta en [brevo.com](https://www.brevo.com) y verifica tu email
   de remitente (Settings → Senders; vale tu Gmail).
2. Genera una API key (Settings → SMTP & API → API Keys).
3. Variables de entorno:
   - `TAXI_EMAIL_PROVIDER=brevo`
   - `TAXI_BREVO_API_KEY=<tu api key>`
   - `TAXI_EMAIL_FROM=Reservas <turemitente@gmail.com>` (el verificado)
4. Redespliega y comprueba con el botón «Enviar un aviso de prueba» del
   panel → Mi perfil.

### Opción B: SMTP con Gmail (solo si tu servidor permite SMTP saliente)

1. Google: Seguridad → Verificación en dos pasos → Contraseñas de
   aplicaciones → crea una para «Correo».
2. `TAXI_EMAIL_PROVIDER=smtp` + `TAXI_SMTP_USER` + `TAXI_SMTP_PASSWORD`
   (sin espacios) + `TAXI_EMAIL_FROM`.

### Opción C: Resend (volumen, requiere dominio propio con SPF/DKIM)

`TAXI_EMAIL_PROVIDER=resend` + `TAXI_RESEND_API_KEY`.

## Activar Telegram (10 minutos)

1. En Telegram, habla con **@BotFather** → `/newbot` → elige nombre y
   usuario (p. ej. `taximad_bot`). Te da el **token**.
2. Variables de entorno:
   - `TAXI_TELEGRAM_PROVIDER=telegram`
   - `TAXI_TELEGRAM_BOT_TOKEN=<token de BotFather>`
   - `TAXI_TELEGRAM_BOT_USERNAME=taximad_bot` (sin @)
   - `TAXI_TELEGRAM_WEBHOOK_SECRET=<cadena aleatoria>`
   - `TAXI_BASE_URL` con tu URL pública (https)
3. Redespliega y registra el webhook una vez:
   `python -m app.jobs telegram-webhook`
   (en Render: pestaña Shell del servicio).
4. Cada taxista pulsa «Vincular Telegram con un toque» en su panel → Mi
   perfil, y desde ese momento recibe cada reserva y los viajes de la bolsa
   en su móvil. El bot también responde a `/id` para vincular a mano.

## Tareas periódicas y arranque en frío (Render free)

La reserva directa **caduca** si el taxista no responde en
`TAXI_SOLICITUD_TTL_MIN` minutos (20 por defecto): el pasajero recibe un
email y puede enviar el viaje a la bolsa con un clic desde su enlace.

Para los recordatorios y el barrido de caducidades sin cron de sistema:

1. Pon `TAXI_CRON_TOKEN=<cadena aleatoria>` en el entorno.
2. En [cron-job.org](https://cron-job.org) (gratis) crea un job cada 5
   minutos a `https://TU-APP.onrender.com/api/cron?token=<esa cadena>`.

Cada llamada envía los recordatorios pendientes, caduca las solicitudes
sin respuesta **y mantiene la instancia despierta** (adiós al arranque en
frío de ~50 s del plan gratuito).

## Despliegue

**Esto es un servidor Python, no una web estática: Netlify, GitHub Pages o
similares no pueden ejecutarlo.** Necesita una plataforma que corra procesos:

### Para probar (gratis/barato): Render o Railway

Ambos detectan el `Dockerfile` automáticamente.

1. Entra en [render.com](https://render.com) (o railway.app) con tu cuenta
   de GitHub.
2. New → Web Service → elige el repo `alexunite03/taxi`.
3. Variables de entorno mínimas:
   - `TAXI_SECRET_KEY` → una cadena larga aleatoria
   - `TAXI_SEED_DEMO=1` → crea el taxista de prueba (`/t/demo`, panel
     `demo@example.com` / `demo1234`)
   - `TAXI_BASE_URL` → la URL que te asigne la plataforma (p. ej.
     `https://taxi-xxxx.onrender.com`)
4. Deploy. La raíz `/` muestra la página de inicio; la reserva de prueba
   está en `/t/demo`.

**Aviso**: con SQLite (por defecto) los datos se borran en cada redeploy en
estas plataformas. Para el piloto real, añade un PostgreSQL gestionado y pon
su URL en `TAXI_DATABASE_URL` (formato
`postgresql+psycopg://usuario:clave@host/db`, añadiendo `psycopg[binary]` a
requirements).

### Para producción: VPS en la UE (plan §12)

Hetzner + Docker o systemd, PostgreSQL y Redis locales, backups diarios
cifrados fuera del servidor, y cron para `python -m app.jobs recordatorios`.

## Pendiente (fases F2–F3 del plan)

- SMS opcional por tenant (recordatorio crítico).
- Revisión letrada de los textos legales y del contrato de encargo.
- Row Level Security y despliegue (VPS UE, backups cifrados).
- HTMX para mejorar el formulario sin recarga; Places Autocomplete.
- Cobro SEPA (GoCardless), Telegram y caja de texto con LLM (opcionales).
- **Recordatorio anual**: revisar el BOCM de diciembre y publicar la nueva
  versión de la tabla en `app/pricing/tarifas.py`; actualizar
  `app/pricing/festivos.py` con el calendario laboral del año siguiente.

## Nota regulatoria

El SaaS no intermedia, no cobra carreras y no emite facturas (fuera de
VeriFactu). El justificante no es una factura; la factura ocasional se
deriva a la app gratuita de la AEAT.
