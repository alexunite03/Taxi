"""Hash de contraseñas del panel con scrypt (stdlib, sin dependencias)."""
import hashlib
import hmac
import secrets

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    sal = secrets.token_hex(16)
    clave = hashlib.scrypt(password.encode(), salt=sal.encode(), n=_N, r=_R, p=_P)
    return f"scrypt${sal}${clave.hex()}"


def verify_password(password: str, almacenado: str) -> bool:
    try:
        _, sal, esperado = almacenado.split("$")
    except ValueError:
        return False
    clave = hashlib.scrypt(password.encode(), salt=sal.encode(), n=_N, r=_R, p=_P)
    return hmac.compare_digest(clave.hex(), esperado)
