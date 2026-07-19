from .base import Geocoder, Lugar, RouteProvider, RutaCalculada
from .fake import FakeGeocoder, FakeRouteProvider


def crear_proveedores() -> tuple[Geocoder, RouteProvider]:
    """Fábrica según configuración: 'osm' (por defecto), 'google' o 'fake'."""
    from app.config import settings

    if settings.route_provider == "osm":
        from .osm import OSRMRouteProvider, PhotonGeocoder

        return (
            PhotonGeocoder(settings.photon_url),
            OSRMRouteProvider(settings.osrm_url),
        )
    if settings.route_provider == "google":
        from .google import GoogleGeocoder, GoogleRouteProvider

        if not settings.google_maps_api_key:
            raise RuntimeError("TAXI_GOOGLE_MAPS_API_KEY es obligatoria con route_provider=google")
        return (
            GoogleGeocoder(settings.google_maps_api_key),
            GoogleRouteProvider(settings.google_maps_api_key),
        )
    return FakeGeocoder(), FakeRouteProvider()


__all__ = [
    "Geocoder",
    "Lugar",
    "RouteProvider",
    "RutaCalculada",
    "FakeGeocoder",
    "FakeRouteProvider",
    "crear_proveedores",
]
