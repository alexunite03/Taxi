"""Jobs de línea de comandos, pensados para cron.

    */5 * * * *  cd /srv/taxi-saas && .venv/bin/python -m app.jobs recordatorios
"""
import sys

from .db import SessionLocal, init_db
from .notificaciones import crear_email_sender
from .services.notificaciones import enviar_recordatorios


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] != "recordatorios":
        print("Uso: python -m app.jobs recordatorios", file=sys.stderr)
        return 2
    init_db()
    with SessionLocal() as db:
        enviados = enviar_recordatorios(db, crear_email_sender())
    print(f"Recordatorios procesados: {enviados}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
