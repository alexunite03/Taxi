from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from .api import cron, panel, publico, telegram
from .config import settings
from .db import init_db
from .notificaciones import crear_email_sender, crear_push_sender, crear_telegram_sender
from .routing import crear_proveedores
from .web import bolsa as web_bolsa
from .web import cuentas as web_cuentas
from .web import intermediarios as web_intermediarios
from .web import perfiles as web_perfiles
from .web import routes as web_routes


def crear_app() -> FastAPI:
    app = FastAPI(title="Reservas de taxi con precio cerrado", docs_url="/api/docs")
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        https_only=settings.base_url.startswith("https"),
        same_site="lax",
    )

    app.state.geocoder, app.state.rutas = crear_proveedores()

    # Un canal de avisos mal configurado no debe impedir el arranque de la
    # web: se degrada a consola y se deja constancia clara en los logs.
    import logging

    from .notificaciones import ConsoleEmailSender, ConsolePushSender, ConsoleTelegramSender

    log = logging.getLogger("taxi.arranque")

    def _con_respaldo(nombre, fabrica, respaldo):
        try:
            return fabrica()
        except Exception as e:
            log.error(
                "Canal %s mal configurado (%s). La web arranca igualmente "
                "con el proveedor de consola: revisa las variables TAXI_*.",
                nombre, e,
            )
            print(f"AVISO: canal {nombre} mal configurado: {e}. "
                  "Usando proveedor de consola.", flush=True)
            return respaldo()

    app.state.email_sender = _con_respaldo("email", crear_email_sender, ConsoleEmailSender)
    app.state.push_sender = _con_respaldo("push", crear_push_sender, ConsolePushSender)
    app.state.telegram_sender = _con_respaldo(
        "telegram", crear_telegram_sender, ConsoleTelegramSender
    )

    if settings.secret_key == "cambia-esto-en-produccion" and \
            "localhost" not in settings.base_url:
        print("AVISO: TAXI_SECRET_KEY sigue siendo la de ejemplo. Pon una "
              "cadena aleatoria larga antes de abrir la web al público.",
              flush=True)

    # Deja en los logs qué proveedor quedó activo en cada canal: si pone
    # "Console…", ese canal NO envía nada real (faltan variables TAXI_*).
    for canal, sender in (("email", app.state.email_sender),
                          ("push", app.state.push_sender),
                          ("telegram", app.state.telegram_sender)):
        clase = type(sender).__name__
        aviso = " — SOLO CONSOLA, no envía nada real" if clase.startswith("Console") else ""
        print(f"Canal {canal}: {clase}{aviso}", flush=True)

    app.mount(
        "/static",
        StaticFiles(directory=Path(__file__).resolve().parent / "web" / "static"),
        name="static",
    )
    app.include_router(publico.router)
    app.include_router(cron.router)
    app.include_router(telegram.router)
    app.include_router(web_routes.router)
    app.include_router(web_cuentas.router)
    app.include_router(web_bolsa.router)
    app.include_router(web_perfiles.router)
    app.include_router(web_intermediarios.router)
    app.include_router(panel.router)

    @app.exception_handler(StarletteHTTPException)
    async def redirigir_login(request: Request, exc: StarletteHTTPException):
        # Sesión de panel caducada en una página HTML → al login
        if exc.status_code == 401 and request.url.path.startswith("/panel"):
            return RedirectResponse("/panel/login", status_code=303)
        from fastapi.exception_handlers import http_exception_handler

        return await http_exception_handler(request, exc)

    @app.on_event("startup")
    def startup() -> None:
        init_db()
        if settings.seed_demo:
            from .seed import crear_tenant_demo

            crear_tenant_demo()

    return app


app = crear_app()
