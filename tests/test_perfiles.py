"""Tests de perfiles de taxista, valoraciones, buscador y geolocalización."""
import io

from sqlalchemy import select

from app.models import Reserva, Valoracion

from .test_api import pedir_cotizacion, reservar_directa
from .test_bolsa import login_panel


def reservar_y_completar(client, db, telefono="600111222"):
    cot = pedir_cotizacion(client).json()
    r = reservar_directa(client, db, cot["cotizacion_id"], telefono=telefono)
    reserva = db.execute(
        select(Reserva).where(Reserva.token_publico == r["reserva_token"])
    ).scalar_one()
    login_panel(client)
    client.post(f"/panel/reservas/{reserva.id}/estado", data={"estado": "completada"})
    return r["reserva_token"]


def test_perfil_publico_y_foto_placeholder(client):
    r = client.get("/t/demo/perfil")
    assert r.status_code == 200
    assert "Taxi Demo" in r.text
    assert "Sin valoraciones todavía" in r.text

    foto = client.get("/t/demo/foto")
    assert foto.status_code == 200
    assert foto.headers["content-type"] == "image/svg+xml"


def test_valorar_tras_completar(client, db):
    token = reservar_y_completar(client, db)

    # La página de la reserva completada ofrece el formulario
    pagina = client.get(f"/r/{token}")
    assert "Qué tal fue el viaje" in pagina.text

    client.post(f"/r/{token}/valorar", data={"puntuacion": 5, "comentario": "Puntual y amable"})
    v = db.execute(select(Valoracion)).scalar_one()
    assert v.puntuacion == 5 and v.comentario == "Puntual y amable"

    # No se puede valorar dos veces
    client.post(f"/r/{token}/valorar", data={"puntuacion": 1})
    assert len(db.execute(select(Valoracion)).scalars().all()) == 1

    # Y el perfil muestra la media
    perfil = client.get("/t/demo/perfil")
    assert "★ 5.0" in perfil.text and "Puntual y amable" in perfil.text


def test_no_se_valora_sin_completar(client, db):
    cot = pedir_cotizacion(client).json()
    r = reservar_directa(client, db, cot["cotizacion_id"], nombre="Ana")
    pagina = client.get(f"/r/{r['reserva_token']}")
    assert "Qué tal fue el viaje" not in pagina.text
    client.post(f"/r/{r['reserva_token']}/valorar", data={"puntuacion": 5})
    assert db.execute(select(Valoracion)).first() is None


def test_buscador_de_taxistas(client, db):
    r = client.get("/taxistas")
    assert r.status_code == 200 and "Taxi Demo" in r.text
    assert client.get("/taxistas?q=Demo").text.count("Taxi Demo") >= 1
    assert "Taxi Demo" not in client.get("/taxistas?q=Inexistente").text


def test_editar_perfil_con_foto(client, db):
    login_panel(client)
    # PNG mínimo válido (1x1)
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d4944415478da63fcffff3f030005fe02fea72d99440000000049454e44"
        "ae426082"
    )
    r = client.post(
        "/panel/perfil",
        data={"bio": "Veinte años al volante."},
        files={"foto": ("yo.png", io.BytesIO(png), "image/png")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    perfil = client.get("/t/demo/perfil")
    assert "Veinte años al volante." in perfil.text
    foto = client.get("/t/demo/foto")
    assert foto.headers["content-type"] == "image/png"


def test_foto_invalida_rechazada(client):
    login_panel(client)
    r = client.post(
        "/panel/perfil",
        data={"bio": ""},
        files={"foto": ("malo.gif", io.BytesIO(b"GIF89a"), "image/gif")},
    )
    assert r.status_code == 422 and "JPG o PNG" in r.text


def test_reverse_geocode(client):
    r = client.get("/api/reverse?lat=40.4168&lng=-3.7038")
    assert r.status_code == 200
    assert "Mi ubicación" in r.json()["texto"]
    assert client.get("/api/reverse?lat=999&lng=0").status_code == 422
