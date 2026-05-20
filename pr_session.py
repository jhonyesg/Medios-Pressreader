#!/usr/bin/env python3
"""
Sesión interactiva de Playwright para explorar PressReader.
Lee comandos de /tmp/pr_cmd, ejecuta en el navegador, escribe resultado en /tmp/pr_out.
"""
import json, os, re, time
from playwright.sync_api import sync_playwright

CMD_FILE = "/tmp/pr_cmd"
OUT_FILE = "/tmp/pr_out"

def write_out(data):
    with open(OUT_FILE, "w") as f:
        f.write(str(data) + "\n")
    print(f"[OUT] {str(data)[:200]}")

def run():
    # Limpiar archivos previos
    for f in [CMD_FILE, OUT_FILE]:
        if os.path.exists(f): os.remove(f)

    p = sync_playwright().start()
    browser = p.chromium.launch(headless=False, slow_mo=300)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 900},
    )
    page = context.new_page()

    # Interceptar TODAS las requests a dominios relevantes
    captured_tokens = []
    all_requests = []
    pagekeys_headers = {}
    pagekeys_responses = []  # (url, response_body)

    def on_request(req):
        url = req.url
        if any(d in url for d in ["pressreader.com", "prcdn.co", "ingress."]):
            all_requests.append(url)
            if "GetPageKeys" in url:
                pagekeys_headers.update(dict(req.headers))
                print(f"[GETPAGEKEYS] {url[:120]}")
                print(f"[GETPAGEKEYS HEADERS] {dict(req.headers)}")
            if "accessToken=" in url:
                m = re.search(r"accessToken=([^&]+)", url)
                if m:
                    tok = m.group(1)
                    if tok not in captured_tokens:
                        captured_tokens.append(tok)
                        print(f"[TOKEN CAPTURADO] {tok[:60]}...")

    def on_response(resp):
        url = resp.url
        if "GetPageKeys" in url or "pagesMetadata" in url:
            try:
                body = resp.text()
                pagekeys_responses.append((url, body))
                print(f"[PAGEKEYS/META RESP] {body[:400]}")
            except Exception as e:
                print(f"[PAGEKEYS/META RESP ERROR] {e}")

    page.on("request", on_request)
    page.on("response", on_response)

    write_out("LISTO - sesion iniciada")
    print("Sesión Playwright lista. Esperando comandos en /tmp/pr_cmd ...")

    while True:
        time.sleep(0.5)
        if not os.path.exists(CMD_FILE):
            continue
        with open(CMD_FILE) as f:
            cmd = f.read().strip()
        os.remove(CMD_FILE)

        if not cmd:
            continue

        print(f"[CMD] {cmd}")
        parts = cmd.split("|", 2)
        action = parts[0].upper()

        try:
            if action == "GOTO":
                page.goto(parts[1], timeout=30000)
                page.wait_for_load_state("domcontentloaded")
                write_out(f"OK goto {parts[1]}")

            elif action == "SCREENSHOT":
                path = parts[1] if len(parts) > 1 else "/tmp/pr_step.png"
                page.screenshot(path=path)
                write_out(f"OK screenshot {path}")

            elif action == "BUTTONS":
                btns = page.query_selector_all("button, a.btn")
                result = []
                for b in btns:
                    try:
                        txt = b.inner_text().strip().replace("\n", " ")
                        cls = b.get_attribute("class") or ""
                        if txt and b.is_visible():
                            result.append(f"[{txt[:40]}] class={cls[:40]}")
                    except: pass
                write_out("\n".join(result[:20]))

            elif action == "CLICK":
                page.click(parts[1], timeout=10000)
                time.sleep(1)
                write_out(f"OK click {parts[1]}")

            elif action == "FORCE_CLICK":
                page.click(parts[1], force=True, timeout=10000)
                time.sleep(1)
                write_out(f"OK force_click {parts[1]}")

            elif action == "FILL":
                sel, val = parts[1], parts[2]
                page.fill(sel, val, timeout=10000)
                write_out(f"OK fill {sel}")

            elif action == "PRESS":
                page.keyboard.press(parts[1])
                time.sleep(0.5)
                write_out(f"OK press {parts[1]}")

            elif action == "INPUTS":
                inputs = page.query_selector_all("input")
                result = []
                for i in inputs:
                    try:
                        t = i.get_attribute("type") or ""
                        n = i.get_attribute("name") or ""
                        ph = i.get_attribute("placeholder") or ""
                        vis = i.is_visible()
                        result.append(f"type={t} name={n} ph={ph[:30]} visible={vis}")
                    except: pass
                write_out("\n".join(result[:15]) or "No inputs")

            elif action == "COOKIES":
                cookies = context.cookies()
                result = []
                for c in cookies:
                    result.append(f"{c['name']}={c['value'][:60]}")
                write_out("\n".join(result) or "Sin cookies")

            elif action == "COOKIES_JSON":
                cookies = context.cookies()
                write_out(json.dumps({c['name']: c['value'] for c in cookies}))

            elif action == "STORAGE":
                ls = page.evaluate("() => JSON.stringify(Object.entries(localStorage))")
                ss = page.evaluate("() => JSON.stringify(Object.entries(sessionStorage))")
                ls_data = json.loads(ls)
                ss_data = json.loads(ss)
                result = ["=== localStorage ==="]
                for k, v in ls_data:
                    result.append(f"  {k}: {v[:80]}")
                result.append("=== sessionStorage ===")
                for k, v in ss_data:
                    result.append(f"  {k}: {v[:80]}")
                write_out("\n".join(result) or "Storage vacío")

            elif action == "TOKENS":
                write_out("\n".join(captured_tokens) or "Sin tokens capturados aún")

            elif action == "PAGEKEYS":
                lines = ["=== Headers enviados a GetPageKeys ==="]
                for k, v in pagekeys_headers.items():
                    lines.append(f"  {k}: {v}")
                lines.append(f"\n=== Respuestas GetPageKeys ({len(pagekeys_responses)}) ===")
                for url, body in pagekeys_responses[-3:]:
                    lines.append(f"  URL: {url[:100]}")
                    lines.append(f"  BODY: {body[:400]}")
                write_out("\n".join(lines) or "Sin datos GetPageKeys aún")

            elif action == "NET":
                # Mostrar todas las requests capturadas (filtrando por texto opcional)
                filtro = parts[1].lower() if len(parts) > 1 else ""
                result = [u for u in all_requests if filtro in u.lower()] if filtro else all_requests
                write_out("\n".join(result[-40:]) or "Sin requests capturadas")

            elif action == "API_GET":
                # Hace GET usando el contexto del navegador (con todas las cookies, sin CORS)
                url_api = parts[1]
                resp = context.request.get(url_api)
                body = resp.text()
                write_out(f"HTTP {resp.status}\n{body[:1000]}")

            elif action == "NET_CLEAR":
                all_requests.clear()
                captured_tokens.clear()
                write_out("OK requests y tokens limpiados")

            elif action == "EVAL":
                result = page.evaluate(parts[1])
                write_out(str(result)[:500])

            elif action == "DISMISS_POPUP":
                # Clic en botón dentro del popup (idioma/región)
                done = False
                for sel in [".popup-container-panel button", "button.btn-underline", "a.btn-underline"]:
                    try:
                        btn = page.query_selector(sel)
                        if btn and btn.is_visible():
                            btn.click(timeout=3000)
                            time.sleep(0.5)
                            done = True
                            write_out(f"OK popup cerrado con {sel}")
                            break
                    except: pass
                if not done:
                    # Escape como fallback
                    page.keyboard.press("Escape")
                    write_out("OK Escape enviado (popup no encontrado)")

            elif action == "OVERLAY":
                tint = page.query_selector(".dialog-tint")
                popup = page.query_selector(".popup-container-panel")
                write_out(f"dialog-tint={tint is not None}, popup-container-panel={popup is not None}")

            elif action == "URL":
                write_out(page.url)

            elif action == "QUIT":
                write_out("OK cerrando")
                break

            else:
                write_out(f"Comando desconocido: {action}")

        except Exception as e:
            write_out(f"ERROR: {e}")

    browser.close()
    p.stop()
    print("Sesión cerrada.")

if __name__ == "__main__":
    run()
