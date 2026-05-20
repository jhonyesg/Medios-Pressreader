#!/usr/bin/env python3
"""
Autenticación automatizada para PressReader usando Playwright.
- Guarda cookies de sesión en browser_session.json para reutilizarlas.
- Si las cookies son válidas, omite el login (más rápido).
- Si no, hace login completo y guarda las cookies nuevas.
- Retorna el JWT Bearer para usar con Authorization: Bearer en la API.
"""

import base64
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


COOKIES_FILE = Path(__file__).parent / "browser_session.json"


class AuthenticationError(Exception):
    pass


class BrowserAuthenticator:
    def __init__(self, auth_env_path: str = None):
        if auth_env_path is None:
            auth_env_path = Path(__file__).parent / "auth.env"
        auth_env_path = Path(auth_env_path)
        if not auth_env_path.exists():
            raise FileNotFoundError(
                f"Archivo auth.env no encontrado: {auth_env_path}\n"
                f"Cópialo de .env.example y completa las credenciales."
            )
        load_dotenv(auth_env_path, override=True)
        self.email = os.getenv("PRESSREADER_EMAIL", "").strip()
        self.password = os.getenv("PRESSREADER_PASSWORD", "").strip()
        if not self.email or not self.password:
            raise AuthenticationError(
                "PRESSREADER_EMAIL y PRESSREADER_PASSWORD deben estar definidos en auth.env"
            )
        self.headed = os.getenv("PRESSREADER_HEADED", "").lower() == "true"

    # ------------------------------------------------------------------ #
    # Punto de entrada principal
    # ------------------------------------------------------------------ #

    def get_fresh_token(self, timeout_ms: int = 30000, epaper_url: str = None) -> str:
        """
        Retorna un JWT Bearer autenticado para usar con la API de PressReader.
        Intenta primero con cookies guardadas; si fallan, hace login completo.
        """
        print("🌐 Iniciando autenticación en PressReader...")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=not self.headed,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
            )

            # Cargar cookies guardadas si existen
            cookies_cargadas = self._cargar_cookies(context)

            page = context.new_page()

            # Interceptar requests — captura el JWT Bearer autenticado (sub > 0)
            captured_jwt = []
            def on_request(request):
                if "ingress.pressreader.com" in request.url and not captured_jwt:
                    auth = request.headers.get("authorization", "")
                    if auth.startswith("Bearer "):
                        tok = auth[7:]
                        try:
                            payload = json.loads(base64.b64decode(tok.split('.')[1] + '=='))
                            if payload.get("sub", 0) > 0:
                                captured_jwt.append(tok)
                        except Exception:
                            pass
            page.on("request", on_request)

            try:
                if cookies_cargadas:
                    token = self._intentar_con_cookies(page, context, timeout_ms, epaper_url, captured_jwt)
                    if token:
                        browser.close()
                        return token
                    print("  ⚠️  Cookies guardadas ya no son válidas — haciendo login completo...")

                # Login completo
                token = self._hacer_login(page, context, timeout_ms, epaper_url, captured_jwt)
                browser.close()
                return token

            except AuthenticationError:
                browser.close()
                raise
            except PlaywrightTimeoutError as e:
                browser.close()
                raise AuthenticationError(f"Timeout durante la autenticación: {e}")
            except Exception as e:
                browser.close()
                raise AuthenticationError(f"Error inesperado durante la autenticación: {e}")

    # ------------------------------------------------------------------ #
    # Flujo con cookies guardadas (rápido, sin login)
    # ------------------------------------------------------------------ #

    def _intentar_con_cookies(self, page, context, timeout_ms, epaper_url, captured_jwt) -> str:
        """Navega directo al periódico usando cookies guardadas. Retorna JWT o '' si falló."""
        print("  🍪 Usando cookies de sesión guardadas...")
        target = epaper_url or "https://www.pressreader.com/colombia/el-espectador"
        try:
            page.goto(target, timeout=timeout_ms)
            page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:
            return ""

        deadline = time.time() + 10
        while time.time() < deadline and not captured_jwt:
            time.sleep(0.5)

        if captured_jwt:
            print(f"  ✅ JWT capturado con cookies guardadas ({len(captured_jwt[0])} chars)")
            return captured_jwt[0]
        return ""

    # ------------------------------------------------------------------ #
    # Flujo de login completo
    # ------------------------------------------------------------------ #

    def _hacer_login(self, page, context, timeout_ms, epaper_url, captured_jwt) -> str:
        """Login completo: navega, descarta popup, rellena formulario, captura JWT."""
        # Ir a la página principal
        print("  Navegando a pressreader.com/es ...")
        page.goto("https://www.pressreader.com/es", timeout=timeout_ms)
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        time.sleep(3)

        # Descartar overlay si aparece
        self._dismiss_popup_if_present(page)

        # Clic en "Conectarse"
        print("  Abriendo modal de login...")
        page.click("button.btn-login", timeout=timeout_ms)
        page.wait_for_selector("input[type='email']", timeout=15000)
        time.sleep(0.3)

        # Rellenar credenciales
        print("  Rellenando credenciales...")
        page.fill("input[type='email']", self.email, timeout=timeout_ms)
        page.fill("input[type='password']", self.password, timeout=timeout_ms)
        page.press("input[type='password']", "Enter")
        print("  Esperando confirmación de login...")

        self._wait_login_success(page, timeout_ms)

        # Guardar cookies inmediatamente tras login exitoso
        self._guardar_cookies(context)

        # Esperar JWT autenticado en requests post-login
        deadline = time.time() + 8
        while time.time() < deadline and not captured_jwt:
            time.sleep(0.5)

        # Si no llegó aún, navegar al ePaper para forzarlo
        if not captured_jwt:
            target = epaper_url or "https://www.pressreader.com/colombia/el-espectador"
            print(f"  Navegando al ePaper para obtener JWT: {target}")
            page.goto(target, timeout=timeout_ms)
            page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            deadline = time.time() + 15
            while time.time() < deadline and not captured_jwt:
                time.sleep(0.5)

        if captured_jwt:
            print(f"  ✅ JWT Bearer autenticado capturado ({len(captured_jwt[0])} chars)")
            return captured_jwt[0]

        raise AuthenticationError(
            "Login completado pero no se capturó el JWT Bearer. "
            "Activa PRESSREADER_HEADED=true en auth.env para depurar."
        )

    # ------------------------------------------------------------------ #
    # Persistencia de cookies
    # ------------------------------------------------------------------ #

    def _guardar_cookies(self, context):
        """Guarda las cookies de sesión en browser_session.json."""
        try:
            cookies = context.cookies()
            COOKIES_FILE.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
            print(f"  💾 Cookies guardadas ({len(cookies)} cookies → {COOKIES_FILE.name})")
        except Exception as e:
            print(f"  ⚠️  No se pudieron guardar cookies: {e}")

    def _cargar_cookies(self, context) -> bool:
        """Carga cookies desde browser_session.json. Retorna True si se cargaron."""
        if not COOKIES_FILE.exists():
            return False
        try:
            cookies = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
            if not cookies:
                return False
            context.add_cookies(cookies)
            print(f"  🍪 Cookies cargadas desde {COOKIES_FILE.name} ({len(cookies)} cookies)")
            return True
        except Exception as e:
            print(f"  ⚠️  No se pudieron cargar cookies: {e}")
            return False

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _dismiss_popup_if_present(self, page):
        """Desactiva pointer-events del popup-container-panel para liberar el botón de login."""
        try:
            disabled = page.evaluate(
                "() => { const p = document.querySelector('.popup-container-panel'); "
                "if (p) { p.style.pointerEvents = 'none'; return true; } return false; }"
            )
            if disabled:
                print("  popup-container-panel desactivado (click-through).")
                time.sleep(0.2)
        except Exception:
            pass

    def _wait_login_success(self, page, timeout_ms: int):
        """Espera a que el campo de contraseña desaparezca (señal de login exitoso)."""
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            try:
                pw = page.query_selector("input[type='password']")
                if pw is None or not pw.is_visible():
                    return
                err = page.query_selector(".error-message, [class*='alert-error']")
                if err and err.is_visible():
                    raise AuthenticationError(
                        "Credenciales rechazadas por PressReader. Revisa auth.env."
                    )
            except AuthenticationError:
                raise
            except Exception:
                pass
            time.sleep(0.5)
        print("  Advertencia: no se confirmó cierre del modal, continuando...")


if __name__ == "__main__":
    auth = BrowserAuthenticator()
    token = auth.get_fresh_token()
    print(f"\nToken ({len(token)} chars):")
    print(token[:80] + "..." if len(token) > 80 else token)
