from pathlib import Path

from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Configuración por variables de entorno (prefijo TAXI_)."""

    database_url: str = f"sqlite:///{BASE_DIR / 'var' / 'taxi.db'}"
    secret_key: str = "cambia-esto-en-produccion"
    base_url: str = "http://localhost:8000"

    # fake | google — con 'google' hace falta la API key
    route_provider: str = "fake"
    google_maps_api_key: str = ""

    # Caducidad de la cotización (plan §3): 15 minutos
    cotizacion_ttl_min: int = 15

    # Antifraude básico (plan §11); en producción, Redis
    rate_limit_por_ip_hora: int = 30
    max_reservas_activas_por_telefono: int = 3

    # Carpeta donde se archivan los justificantes (PDF/HTML + hash)
    justificantes_dir: Path = BASE_DIR / "var" / "justificantes"

    # Crea un tenant de demostración al arrancar (solo desarrollo)
    seed_demo: bool = False

    model_config = {"env_prefix": "TAXI_", "env_file": ".env"}


settings = Settings()
