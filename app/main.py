from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from .api import panel, publico
from .config import settings
from .db import init_db
from .routing import crear_proveedores
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

    app.mount(
        "/static",
        StaticFiles(directory=Path(__file__).resolve().parent / "web" / "static"),
        name="static",
    )
    app.include_router(publico.router)
    app.include_router(web_routes.router)
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
