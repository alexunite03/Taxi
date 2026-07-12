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
- **API JSON pública** (`/api/t/{slug}/...`) según el plan §11, con rate
  limit por IP, honeypot y límite de reservas activas por teléfono.
- **Multi-tenant** por `tenant_id` en PostgreSQL/SQLite (RLS pendiente para
  producción).

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
| `TAXI_DATABASE_URL` | SQLite en `var/` | PostgreSQL en producción |
| `TAXI_ROUTE_PROVIDER` | `fake` | `google` para Geocoding + Routes reales |
| `TAXI_GOOGLE_MAPS_API_KEY` | — | obligatoria con `google` |
| `TAXI_SECRET_KEY` | insegura | cámbiala en producción |
| `TAXI_BASE_URL` | `http://localhost:8000` | para el QR y enlaces |
| `TAXI_SEED_DEMO` | `0` | crea el tenant demo al arrancar |

El proveedor `fake` geocodifica y enruta de forma determinista (sin red):
todo el flujo funciona en local sin API key.

## Pendiente (fases F2–F3 del plan)

- Web Push (VAPID + service worker); SMS opcional por tenant.
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
