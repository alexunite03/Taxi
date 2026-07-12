"""Jobs de línea de comandos, pensados para cron.

    */5 * * * *  cd /srv/taxi-saas && .venv/bin/python -m app.jobs recordatorios

`generar-vapid` imprime un par de claves para Web Push (una sola vez por
instalación; se guardan en las variables TAXI_VAPID_*).
"""
import sys


def _recordatorios() -> int:
    from .db import SessionLocal, init_db
    from .notificaciones import crear_email_sender, crear_push_sender
    from .services.notificaciones import enviar_recordatorios

    init_db()
    with SessionLocal() as db:
        enviados = enviar_recordatorios(db, crear_email_sender(), crear_push_sender())
    print(f"Recordatorios procesados: {enviados}")
    return 0


def _generar_vapid() -> int:
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    def b64url(datos: bytes) -> str:
        return base64.urlsafe_b64encode(datos).rstrip(b"=").decode()

    clave = ec.generate_private_key(ec.SECP256R1())
    privada = clave.private_numbers().private_value.to_bytes(32, "big")
    publica = clave.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    print("Añade a tu entorno (.env):")
    print(f"TAXI_PUSH_PROVIDER=webpush")
    print(f"TAXI_VAPID_PRIVATE_KEY={b64url(privada)}")
    print(f"TAXI_VAPID_PUBLIC_KEY={b64url(publica)}")
    print("TAXI_VAPID_EMAIL=tu-contacto@dominio.es")
    return 0


def main() -> int:
    orden = sys.argv[1] if len(sys.argv) > 1 else ""
    if orden == "recordatorios":
        return _recordatorios()
    if orden == "generar-vapid":
        return _generar_vapid()
    print("Uso: python -m app.jobs [recordatorios|generar-vapid]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
