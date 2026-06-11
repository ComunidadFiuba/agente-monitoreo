"""
Script 2: Monitoreo de productos de MercadoLibre.

Carga la sesion guardada por login_y_guardar_sesion.py, visita una lista
de URLs de productos, extrae titulo / precio / disponibilidad y compara
con el ultimo estado guardado en estado/estado_{id}.json. Si detecta
cambios, los imprime por consola y envia un mail de aviso via smtplib.

Pensado para correr de forma periodica (ej: con un scheduler / cron) en
modo headless.
"""

import json
import re
import smtplib
import sys
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Page, sync_playwright
import os

# --------------------------------------------------------------------------
# Configuracion
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
ARCHIVO_SESION = BASE_DIR / "session.json"
DIR_ESTADO = BASE_DIR / "estado"
ARCHIVO_PRODUCTOS = BASE_DIR / "productos.json"

load_dotenv(BASE_DIR / ".env")

MAIL_USER = os.getenv("MAIL_USER")
MAIL_PASS = os.getenv("MAIL_PASS")
MAIL_DESTINO = os.getenv("MAIL_DESTINO")


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------

def cargar_productos() -> list[str]:
    """Lee la lista de URLs de productos a monitorear desde productos.json."""
    if not ARCHIVO_PRODUCTOS.exists():
        print(f"ERROR: no se encontro '{ARCHIVO_PRODUCTOS.name}' con la lista de productos.")
        sys.exit(1)
    with open(ARCHIVO_PRODUCTOS, "r", encoding="utf-8") as f:
        return json.load(f)


def slug_desde_url(url: str) -> str:
    """Genera un identificador corto y seguro para usar como nombre de archivo."""
    match = re.search(r"(MLA-?\d+)", url, re.IGNORECASE)
    if match:
        return match.group(1).replace("-", "").upper()
    # Fallback: usar los ultimos segmentos de la URL, sin caracteres invalidos.
    limpio = re.sub(r"[^a-zA-Z0-9]+", "_", url.rstrip("/").split("/")[-1])
    return limpio or "producto"


def extraer_datos_producto(pagina: Page) -> dict:
    """Extrae titulo, precio y disponibilidad de la pagina de un producto."""

    # Titulo: preferir el h1 principal de la ficha, con fallback al <title>.
    titulo = None
    h1 = pagina.locator("h1.ui-pdp-title").first
    if h1.count() > 0:
        titulo = h1.inner_text().strip()
    if not titulo:
        titulo = pagina.title().strip()

    # Precio: el contenedor de precio principal expone fraccion (y opcionalmente
    # centavos) dentro de meta tags / spans con clases estables de la ficha.
    precio = None
    contenedor_precio = pagina.locator(
        ".ui-pdp-price__second-line .andes-money-amount__fraction"
    ).first
    if contenedor_precio.count() > 0:
        precio_texto = contenedor_precio.inner_text().strip()
        precio = re.sub(r"[^\d]", "", precio_texto)

    # Disponibilidad: se busca el boton de compra ("Comprar ahora") o, en su
    # defecto, mensajes de stock / producto pausado o sin stock.
    disponible = pagina.get_by_role("button", name=re.compile("comprar ahora", re.IGNORECASE)).count() > 0

    if not disponible:
        sin_stock = pagina.get_by_text(
            re.compile("sin stock|producto pausado|no disponible|agotado", re.IGNORECASE)
        ).count() > 0
        disponibilidad = "sin stock" if sin_stock else "desconocido"
    else:
        disponibilidad = "disponible"

    return {
        "titulo": titulo,
        "precio": precio,
        "disponibilidad": disponibilidad,
    }


def cargar_estado_anterior(slug: str) -> dict | None:
    archivo = DIR_ESTADO / f"estado_{slug}.json"
    if not archivo.exists():
        return None
    with open(archivo, "r", encoding="utf-8") as f:
        return json.load(f)


def guardar_estado(slug: str, datos: dict) -> None:
    DIR_ESTADO.mkdir(exist_ok=True)
    archivo = DIR_ESTADO / f"estado_{slug}.json"
    with open(archivo, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)


def comparar_estados(anterior: dict, actual: dict) -> list[str]:
    """Devuelve una lista de descripciones de los campos que cambiaron."""
    cambios = []
    for campo in ("titulo", "precio", "disponibilidad"):
        valor_anterior = anterior.get(campo)
        valor_actual = actual.get(campo)
        if valor_anterior != valor_actual:
            cambios.append(f"{campo}: '{valor_anterior}' -> '{valor_actual}'")
    return cambios


def enviar_mail(asunto: str, cuerpo: str) -> None:
    if not (MAIL_USER and MAIL_PASS and MAIL_DESTINO):
        print("Faltan variables de entorno MAIL_USER / MAIL_PASS / MAIL_DESTINO, no se envia mail.")
        return

    mensaje = MIMEText(cuerpo, "plain", "utf-8")
    mensaje["Subject"] = asunto
    mensaje["From"] = MAIL_USER
    mensaje["To"] = MAIL_DESTINO

    with smtplib.SMTP("smtp.gmail.com", 587) as servidor:
        servidor.starttls()
        servidor.login(MAIL_USER, MAIL_PASS)
        servidor.send_message(mensaje)


def sesion_parece_expirada(pagina: Page) -> bool:
    """Heuristica simple: si aparece el link de 'Ingresa' / 'Iniciar sesion'
    en el header, es probable que la sesion guardada haya expirado."""
    return pagina.get_by_role("link", name=re.compile("ingres", re.IGNORECASE)).count() > 0


# --------------------------------------------------------------------------
# Programa principal
# --------------------------------------------------------------------------

def main() -> None:
    if not ARCHIVO_SESION.exists():
        print(f"ERROR: no se encontro '{ARCHIVO_SESION.name}'. "
              f"Corre primero login_y_guardar_sesion.py para generarlo.")
        sys.exit(1)

    productos = cargar_productos()

    with sync_playwright() as p:
        # Se usa Chrome (no el Chromium "vanilla") en modo "headed" pero con
        # la ventana posicionada fuera de pantalla. MercadoLibre bloquea con
        # un error/captcha al Chrome headless de verdad incluso con sesion
        # guardada, pero no detecta este modo "headed offscreen".
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-position=-32000,-32000",
                "--window-size=1280,800",
            ],
        )
        contexto = browser.new_context(
            storage_state=str(ARCHIVO_SESION), locale="es-AR", viewport=None
        )
        pagina = contexto.new_page()

        for url in productos:
            slug = slug_desde_url(url)
            print(f"\n[{slug}] Visitando {url}")

            try:
                pagina.goto(url, wait_until="domcontentloaded")
            except Exception as exc:
                print(f"[{slug}] ERROR al cargar la pagina: {exc}")
                continue

            if sesion_parece_expirada(pagina):
                print(f"[{slug}] ERROR: la sesion guardada parece haber expirado. "
                      f"Volve a correr login_y_guardar_sesion.py.")
                continue

            try:
                datos_actuales = extraer_datos_producto(pagina)
            except Exception as exc:
                print(f"[{slug}] ERROR al extraer datos: {exc}")
                continue

            if datos_actuales["disponibilidad"] == "desconocido":
                print(f"[{slug}] omitido: no se pudo determinar la disponibilidad "
                      f"(la pagina no tiene boton 'Comprar ahora' / mensaje de stock).")
                continue

            datos_anteriores = cargar_estado_anterior(slug)

            if datos_anteriores is None:
                print(f"[{slug}] Primer chequeo, guardando estado inicial: {datos_actuales}")
                guardar_estado(slug, datos_actuales)
                continue

            cambios = comparar_estados(datos_anteriores, datos_actuales)

            if cambios:
                detalle = "\n".join(cambios)
                print(f"[{slug}] CAMBIOS DETECTADOS:\n{detalle}")

                enviar_mail(
                    asunto=f"[Monitoreo ML] Cambios en {datos_actuales.get('titulo') or slug}",
                    cuerpo=(
                        f"Se detectaron cambios en el producto:\n{url}\n\n"
                        f"{detalle}\n"
                    ),
                )

                guardar_estado(slug, datos_actuales)
            else:
                print(f"[{slug}] sin novedades")

        browser.close()


if __name__ == "__main__":
    main()
