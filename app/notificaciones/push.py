"""Web Push (capa 1 del plan §5): gratis, VAPID + service worker.

Funciona bien en Android y escritorio; en iPhone solo si el usuario añade la
web a la pantalla de inicio (iOS 16.4+). Por eso se ofrece, pero nada depende
de él: el enlace /r/{token} sigue siendo la fuente de verdad.

Proveedores conmutables como en email: `console` (desarrollo) y `webpush`
(pywebpush con claves VAPID). Las claves se generan con
`python -m app.jobs generar-vapid`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol


class SuscripcionCaducada(Exception):
    """El endpoint ya no existe (404/410): hay que borrar la suscripción."""


@dataclass(frozen=True)
class MensajePush:
    titulo: str
    cuerpo: str
    url: str


class PushSender(Protocol):
    def enviar(self, suscripcion: dict, mensaje: MensajePush) -> None:
        """`suscripcion` es el objeto PushSubscription del navegador
        (endpoint + keys). Lanza SuscripcionCaducada si el endpoint murió."""
        ...


class ConsolePushSender:
    def enviar(self, suscripcion: dict, mensaje: MensajePush) -> None:
        endpoint = suscripcion.get("endpoint", "?")
        print(
            f"PUSH (console) endpoint={endpoint[:60]}… titulo={mensaje.titulo!r} "
            f"cuerpo={mensaje.cuerpo!r}",
            flush=True,
        )


class WebPushSender:
    def __init__(self, vapid_private_key: str, contacto_email: str):
        self.vapid_private_key = vapid_private_key
        self.claims = {"sub": f"mailto:{contacto_email}"}

    def enviar(self, suscripcion: dict, mensaje: MensajePush) -> None:
        from pywebpush import WebPushException, webpush

        try:
            webpush(
                subscription_info=suscripcion,
                data=json.dumps(
                    {"titulo": mensaje.titulo, "cuerpo": mensaje.cuerpo, "url": mensaje.url}
                ),
                vapid_private_key=self.vapid_private_key,
                vapid_claims=dict(self.claims),
                timeout=10,
            )
        except WebPushException as e:
            codigo = getattr(e.response, "status_code", None)
            if codigo in (404, 410):
                raise SuscripcionCaducada(str(e)) from e
            raise
