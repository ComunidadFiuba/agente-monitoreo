"""
Script 1: Login manual y guardado de sesion.

Abre un navegador Chromium visible en MercadoLibre para que el usuario
inicie sesion manualmente (incluyendo captcha si aparece). Cuando el
usuario presiona ENTER en la terminal, guarda el estado completo de la
sesion (cookies, localStorage, etc.) en session.json para que
monitorear.py pueda reutilizarla sin volver a loguearse.
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

URL_LOGIN = "https://www.mercadolibre.com.ar"
ARCHIVO_SESION = Path(__file__).parent / "session.json"


def main() -> None:
    with sync_playwright() as p:
        # Se usa el Google Chrome instalado (channel="chrome") y se desactivan
        # las marcas de automatizacion, porque el reCAPTCHA de MercadoLibre/Google
        # suele no renderizarse bien con el Chromium "vanilla" de Playwright.
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        contexto = browser.new_context(
            viewport=None,  # usa el tamanio real de la ventana en vez de uno fijo
            locale="es-AR",
        )
        pagina = contexto.new_page()

        pagina.goto(URL_LOGIN)

        print("Se abrio el navegador en MercadoLibre.")
        print("Inicia sesion manualmente (resolve el captcha si aparece).")
        input("Cuando hayas iniciado sesion, volve a esta terminal y presiona ENTER...")

        contexto.storage_state(path=str(ARCHIVO_SESION))
        print(f"Sesion guardada en: {ARCHIVO_SESION}")

        browser.close()


if __name__ == "__main__":
    main()
