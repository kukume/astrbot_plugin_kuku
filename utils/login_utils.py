from __future__ import annotations

import base64
import hashlib
import json
import math
import random
import re
import string
from io import BytesIO
from typing import Any
from urllib.parse import urljoin

import httpx

from .http_client import DEFAULT_UA


class QrcodeException(RuntimeError):
    pass


class QrcodeScanException(QrcodeException):
    pass


class QrcodeNotScannedException(QrcodeScanException):
    pass


class QrcodeScannedException(QrcodeScanException):
    pass


class QrcodeExpireException(QrcodeException):
    def __init__(self, message: str = "二维码已过期"):
        super().__init__(message)


def md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def random_num(n: int) -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(n))


def random_letter(n: int) -> str:
    chars = string.ascii_letters
    return "".join(random.choice(chars) for _ in range(n))


def jsonp_to_json(text: str) -> Any:
    match = re.search(r"\{(?:[^{}]|\{[^{}]*})*}", text)
    if not match:
        raise RuntimeError("json not found")
    return json.loads(match.group(0))


def parse_json_or_jsonp(text: str) -> Any:
    stripped = (text or "").strip()
    if not stripped:
        raise RuntimeError("empty json")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return jsonp_to_json(stripped)


def render_set_cookie(response: httpx.Response) -> str:
    parts: list[str] = []
    for header in response.headers.get_list("set-cookie"):
        nv = header.split(";", 1)[0].strip()
        if not nv or "=" not in nv:
            continue
        name, value = nv.split("=", 1)
        if value == "deleted":
            continue
        parts.append(f"{name}={value}; ")
    return "".join(parts)


def find_set_cookie(response: httpx.Response, name: str) -> str | None:
    prefix = name + "="
    for header in response.headers.get_list("set-cookie"):
        nv = header.split(";", 1)[0].strip()
        if nv.startswith(prefix):
            return nv[len(prefix) :]
    return None


def location_of(response: httpx.Response) -> str | None:
    loc = response.headers.get("location") or response.headers.get("Location")
    if not loc:
        return None
    if loc.startswith("http://") or loc.startswith("https://"):
        return loc
    if loc.startswith("//"):
        return "https:" + loc
    return urljoin(str(response.url), loc)


def rsa_encrypt(text: str, public_key_b64: str) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    der = base64.b64decode(public_key_b64)
    key = serialization.load_der_public_key(der)
    encrypted = key.encrypt(text.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(encrypted).decode("ascii")


def rsa_encrypt_to_hex(text: str, public_key_b64: str) -> str:
    return base64.b64decode(rsa_encrypt(text, public_key_b64)).hex()


def aes_cbc_decrypt_nopadding(key: bytes, iv: bytes, data: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    return decryptor.update(data) + decryptor.finalize()


def hostloc_prepare_cookie(html: str) -> str:
    group = re.findall(r'(?<=toNumbers\(").*?(?="\))', html)
    if not group:
        return ""
    a = bytes(int(group[0][i : i + 2], 16) for i in range(0, len(group[0]), 2))
    b = bytes(int(group[1][i : i + 2], 16) for i in range(0, len(group[1]), 2))
    c = bytes(int(group[2][i : i + 2], 16) for i in range(0, len(group[2]), 2))
    decrypted = aes_cbc_decrypt_nopadding(a, b, c)
    return f"cnsL7={decrypted.hex()}; "


def aes_cbc_encrypt(plain: str, key: str, iv: str) -> bytes:
    from cryptography.hazmat.primitives import padding as sym_padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    padder = sym_padding.PKCS7(128).padder()
    data = padder.update(plain.encode("utf-8")) + padder.finalize()
    cipher = Cipher(algorithms.AES(key.encode("utf-8")), modes.CBC(iv.encode("utf-8")))
    encryptor = cipher.encryptor()
    return encryptor.update(data) + encryptor.finalize()


def kugou_rsa_encrypt(plaintext: str) -> str:
    modulus = int(
        "B1B1EC76A1BBDBF0D18E8CD9A87E53FA3881E2F004C67C9DDA2CA677DBEFA3D61DF8463FE12D84FF4B4699E02C9D41CAB917F5A8FB9E35580C4BDF97763A0420A476295D763EE10174E6F9EBF7DF8A77BA5B20CDA4EE705DEF5BBA3C88567B9656E52C9CD5CD95CA735FF2D25F762B133273EEEB7B4F3EA8B6DA29040F3B67CD",
        16,
    )
    exp = int("10001", 16)
    data = plaintext.encode("utf-8")
    chunk_size = 128
    if len(data) > chunk_size:
        raise ValueError(f"RSA plaintext too long: {len(data)}")
    u = bytearray(chunk_size)
    n = 0
    o = chunk_size - 1
    while n < len(data):
        u[o] = data[n]
        n += 1
        o -= 1
    m = int.from_bytes(bytes(reversed(u)), "big")
    return format(pow(m, exp, modulus), "x").zfill(256)


def kugou_encrypt_mobile_code(phone: str, time_ms: int, fixed_key: str | None = None) -> tuple[str, str]:
    key_chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if fixed_key is None:
        key = "".join(key_chars[min(math.ceil(35 * random.random()), len(key_chars) - 1)] for _ in range(16))
    else:
        key = fixed_key
    aes_key_material = md5(key)
    iv_material = aes_key_material[-16:]
    params = aes_cbc_encrypt(f'{{"mobile":"{phone}"}}', aes_key_material, iv_material).hex()
    pk = kugou_rsa_encrypt(f'{{"clienttime_ms":{time_ms},"key":"{key}"}}')
    return params, pk


def make_qrcode(text: str) -> bytes:
    import qrcode

    img = qrcode.make(text, box_size=6, border=1)
    buf = BytesIO()
    img.convert("RGB").save(buf, format="JPEG")
    return buf.getvalue()


def default_headers(**extra: str) -> dict[str, str]:
    headers = {"User-Agent": DEFAULT_UA}
    headers.update(extra)
    return headers
