#!/usr/bin/env python3
"""
Orquestador de descarga de PressReader.
1. Valida los tokens de cada medio contra la API.
2. Si un token falla → obtiene uno fresco vía browser auth → lo guarda en press.ini.
3. Ejecuta el downloader principal.

Uso:
    python3 run_descarga.py              # todos los medios
    python3 run_descarga.py Espectador   # medio específico
"""

import configparser
import json
import sys
import requests
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------- #
PRESS_INI = Path(__file__).parent / "press.ini"
MEDIOS_CON_PLATAFORMA_PROPIA = {"Motor", "Tiempo"}  # casaeditorialeltiempo.pressreader.com
GETPAGEKEYS_URL = "https://ingress.pressreader.com/services/IssueInfo/GetPageKeys"
# --------------------------------------------------------------------------- #


def leer_ini() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read(PRESS_INI, encoding="utf-8")
    return cfg


def guardar_token_en_ini(medio: str, nuevo_token: str):
    """Sobreescribe el token / tokens del medio en press.ini."""
    lines = PRESS_INI.read_text(encoding="utf-8").splitlines()
    seccion_activa = None
    resultado = []
    reemplazado = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            seccion_activa = stripped[1:-1]
        if seccion_activa == medio and not reemplazado:
            if stripped.lower().startswith("tokens =") or stripped.lower().startswith("token ="):
                clave = "tokens" if stripped.lower().startswith("tokens") else "token"
                line = f"{clave} = {nuevo_token}"
                reemplazado = True
        resultado.append(line)
    PRESS_INI.write_text("\n".join(resultado) + "\n", encoding="utf-8")
    print(f"  💾 Token guardado en press.ini [{medio}]")


def validar_token(token: str, libro: str, url_id: str) -> bool:
    """
    Llama GetPageKeys y devuelve True si la respuesta tiene PageKeys.
    Prueba hoy y, si la edición aún no está publicada (madrugada), prueba ayer.
    Esto evita falsos negativos que desencadenarían un refresco de token innecesario.
    """
    from datetime import timedelta
    for delta in (0, -1):
        fecha = (datetime.now() + timedelta(days=delta)).strftime("%Y%m%d")
        issue = f"{libro}{fecha}{url_id}"
        try:
            if token.startswith("ey"):
                url = f"{GETPAGEKEYS_URL}?issue={issue}&pageNumber=0&preview=true"
                headers = {"Authorization": f"Bearer {token}", "Referer": "https://www.pressreader.com/"}
                r = requests.get(url, headers=headers, timeout=15)
            else:
                url = f"{GETPAGEKEYS_URL}?accessToken={token}&issue={issue}&pageNumber=0&preview=true"
                r = requests.get(url, timeout=15)
            if r.status_code == 200 and r.json().get("PageKeys"):
                if delta < 0:
                    print(f"  ℹ️  Edición de hoy aún no disponible — token válido con edición de ayer ({fecha})")
                return True
        except Exception:
            pass
    return False


def refrescar_token(medio: str) -> str | None:
    """Obtiene un JWT Bearer fresco y lo guarda en press.ini."""
    from pressreader_auth import BrowserAuthenticator, AuthenticationError
    print(f"  🌐 Obteniendo token fresco para {medio} vía browser auth...")
    try:
        auth = BrowserAuthenticator()
        token = auth.get_fresh_token()
        guardar_token_en_ini(medio, token)
        print(f"  ✅ Token fresco obtenido ({len(token)} chars)")
        return token
    except (AuthenticationError, FileNotFoundError) as e:
        print(f"  ❌ No se pudo refrescar token de {medio}: {e}")
        return None


def validar_y_refrescar_medios(medios_a_procesar: list[str], cfg: configparser.ConfigParser):
    """
    Para cada medio: prueba el token actual.
    Si falla → refresca. Skippea medios de plataforma propia.
    """
    print("\n" + "="*60)
    print("PASO 1: Validando tokens")
    print("="*60)
    for medio in medios_a_procesar:
        if medio in MEDIOS_CON_PLATAFORMA_PROPIA:
            print(f"\n[{medio}] ⏭️  Plataforma propia — se omite validación")
            continue
        if medio not in cfg:
            print(f"\n[{medio}] ⚠️  No encontrado en press.ini — se omite")
            continue

        sec = cfg[medio]
        libro = sec.get("libro", "")
        url_ids = [u.strip() for u in sec.get("url_ids", "").split(",") if u.strip()]
        url_id = url_ids[0] if url_ids else "00000000001001"

        # Obtener el primer token disponible
        tokens_raw = sec.get("tokens", sec.get("token", ""))
        token = tokens_raw.split(",")[0].strip()

        if not token:
            print(f"\n[{medio}] ⚠️  Sin token configurado — se intentará refrescar")
            refrescar_token(medio)
            continue

        print(f"\n[{medio}] 🔍 Validando token contra API...")
        try:
            ok = validar_token(token, libro, url_id)
        except Exception as e:
            ok = False
            print(f"  ⚠️  Error de red al validar: {e}")

        if ok:
            print(f"  ✅ Token válido")
        else:
            print(f"  ❌ Token inválido o caducado — refrescando...")
            refrescar_token(medio)


def ejecutar_downloader(medios_a_procesar: list[str]):
    """Importa y corre el downloader directamente."""
    print("\n" + "="*60)
    print("PASO 2: Ejecutando descarga")
    print("="*60 + "\n")
    import argparse
    import pressreader_downloader as dl

    # Construir args equivalente al CLI del downloader
    args = argparse.Namespace(
        medio=None,
        fecha=None,
        custom_fecha=None,
        config="press.ini",
        medios=medios_a_procesar if medios_a_procesar else None,
    )

    downloader = dl.PressReaderDownloader()

    if medios_a_procesar and len(medios_a_procesar) == 1:
        args.medio = medios_a_procesar[0]
        downloader.process_single_medium(medios_a_procesar[0], args)
    else:
        # Parchamos get_available_media para respetar el subset solicitado
        if medios_a_procesar:
            downloader.get_available_media = lambda: medios_a_procesar
        downloader.process_all_media(args)


def main():
    medios_arg = sys.argv[1:] if len(sys.argv) > 1 else []
    cfg = leer_ini()

    medios_disponibles = [s for s in cfg.sections() if s not in ("DEFAULT", "FTP")]
    medios_a_procesar = medios_arg if medios_arg else medios_disponibles

    invalidos = [m for m in medios_arg if m not in medios_disponibles]
    if invalidos:
        print(f"❌ Medios no encontrados en press.ini: {invalidos}")
        print(f"   Disponibles: {medios_disponibles}")
        sys.exit(1)

    print(f"🗞️  Medios a procesar: {', '.join(medios_a_procesar)}")
    print(f"📅 Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    validar_y_refrescar_medios(medios_a_procesar, cfg)
    ejecutar_downloader(medios_a_procesar)


if __name__ == "__main__":
    main()
