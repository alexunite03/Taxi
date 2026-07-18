from pathlib import Path

from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Configuración por variables de entorno (prefijo TAXI_)."""

    database_url: str = f"sqlite:///{BASE_DIR / 'var' / 'taxi.db'}"
    secret_key: str = "cambia-esto-en-produccion"
    base_url: str = "http://localhost:8000"

    # Proveedor de geocodificación y rutas:
    #   osm    → Nominatim + OSRM públicos (gratis, sin API key; por defecto)
    #   google → Geocoding + Routes con tráfico (producción, requiere key)
    #   fake   → determinista sin red (tests)
    route_provider: str = "osm"
    google_maps_api_key: str = ""
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    osrm_url: str = "https://router.project-osrm.org"

    # Caducidad de la cotización (plan §3): 15 minutos
    cotizacion_ttl_min: int = 15

    # Email transaccional (plan §5): console (desarrollo) | resend | smtp
    email_provider: str = "console"
    resend_api_key: str = ""
    email_from: str = "Reservas <reservas@example.com>"
    # SMTP (p. ej. Gmail con contraseña de aplicación) — la vía más rápida
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""

    # Web Push (plan §5): console (desarrollo) | webpush
    # Claves con `python -m app.jobs generar-vapid`
    push_provider: str = "console"
    vapid_private_key: str = ""
    vapid_public_key: str = ""
    vapid_email: str = "reservas@example.com"

    # Avisos al taxista por Telegram: console (desarrollo) | telegram
    telegram_provider: str = "console"
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""      # sin @, para el enlace t.me de vinculación
    telegram_webhook_secret: str = ""    # valida los updates entrantes del bot

    # Radio (km) de los avisos de la bolsa cuando el taxista comparte
    # su ubicación por Telegram
    bolsa_radio_km: float = 15.0

    # Recordatorio previo a la recogida (minutos antes)
    recordatorio_min: int = 30

    # Antifraude básico (plan §11); en producción, Redis
    rate_limit_por_ip_hora: int = 30
    max_reservas_activas_por_telefono: int = 3

    # Carpeta donde se archivan los justificantes (PDF/HTML + hash)
    justificantes_dir: Path = BASE_DIR / "var" / "justificantes"

    # Fotos de perfil de los taxistas
    fotos_dir: Path = BASE_DIR / "var" / "fotos"

    # Datos del proveedor SaaS para los textos legales (LSSI / RGPD).
    # Rellenar al constituir la SL (checklist §17 del plan).
    proveedor_nombre: str = "[RAZÓN SOCIAL DEL PROVEEDOR, S.L.]"
    proveedor_nif: str = "[NIF]"
    proveedor_domicilio: str = "[DOMICILIO SOCIAL]"
    proveedor_email: str = "[EMAIL DE CONTACTO]"
    proveedor_registro: str = "[DATOS DE INSCRIPCIÓN EN EL REGISTRO MERCANTIL]"

    # Crea un tenant de demostración al arrancar (solo desarrollo)
    seed_demo: bool = False

    model_config = {"env_prefix": "TAXI_", "env_file": ".env"}


settings = Settings()
