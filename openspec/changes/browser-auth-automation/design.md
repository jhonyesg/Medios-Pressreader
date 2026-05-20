## Context

El sistema actual (`pressreader_downloader.py`) descarga ediciones de periódicos usando tokens de acceso estáticos almacenados en `press.ini`. Estos tokens son cookies/JWTs de sesión de PressReader que caducan con el tiempo. Cuando caducan, las descargas fallan silenciosamente y se requiere intervención manual para obtener nuevos tokens (inspeccionar el navegador, copiar la cookie, pegar en el INI).

El flujo actual:
1. Leer token de `press.ini`
2. Construir URL con token: `https://ingress.pressreader.com/services/IssueInfo/GetPageKeys?accessToken=<TOKEN>&issue=...`
3. Descargar páginas como imágenes

Publimetro no tiene token estático funcional identificado, por lo que su sección en `press.ini` existe pero no descarga correctamente.

## Goals / Non-Goals

**Goals:**
- Login automatizado en PressReader.com vía Playwright sin intervención humana.
- Extracción del token/cookie de sesión tras login exitoso.
- Refresco automático de token cuando el configurado falla (HTTP 401/403 o respuesta vacía).
- Soporte de descarga para Publimetro usando la sesión autenticada.
- Credenciales almacenadas fuera de `press.ini` en archivo seguro.

**Non-Goals:**
- Reemplazar el motor de descarga existente (las URLs de API y lógica de páginas no cambian).
- Soporte multi-cuenta o rotación de cuentas.
- Autenticación 2FA (la cuenta no la usa).
- Headless permanente en producción si Playwright es bloqueado (se parte headless, se puede cambiar a headed si hay detección).

## Decisions

### 1. Playwright sobre Selenium
**Decisión:** Usar `playwright` (Python) en lugar de Selenium.

**Rationale:** Playwright tiene soporte nativo para esperar elementos, interceptar requests de red, y extraer cookies con una API más limpia. Tiene menos overhead de setup (no requiere chromedriver separado) y maneja mejor los SPAs como PressReader. `playwright install` descarga el binario de Chromium automáticamente.

**Alternativa descartada:** Selenium — requiere geckodriver/chromedriver sincronizados con la versión del browser instalado; más frágil en CI/entornos headless.

### 2. Extracción de token vía intercepción de red
**Decisión:** Tras el login, interceptar las requests de red de Playwright para capturar el header `Authorization` o el parámetro `accessToken` de la primera llamada a `ingress.pressreader.com`.

**Rationale:** Es más robusto que parsear cookies por nombre (los nombres pueden cambiar). El token ya aparece en las URLs que el propio script construye, así que interceptar la primera request autenticada garantiza obtener el formato correcto.

**Alternativa:** Extraer la cookie `pr_pfreader` del storage de Playwright — válido como fallback si la intercepción falla.

### 3. Flujo de refresco lazy (on-demand)
**Decisión:** No refrescar el token en un cronjob separado. En cambio, el downloader intenta con el token configurado; si falla (HTTP 401/403 o 0 páginas descargadas), llama a `BrowserAuthenticator.get_fresh_token()` y reintenta una vez.

**Rationale:** Mantiene el flujo simple. No requiere proceso daemon ni scheduler adicional. El refresco solo ocurre cuando es necesario (tokens duran días/semanas).

### 4. Almacenamiento de credenciales en archivo `.env`
**Decisión:** Crear `auth.env` (excluido de git/Nextcloud sync si se desea) con `PRESSREADER_EMAIL` y `PRESSREADER_PASSWORD`. Cargado con `python-dotenv`.

**Rationale:** Separar credenciales de configuración operacional (`press.ini`). El `.env` es un estándar reconocido para secretos locales.

### 5. Clase `BrowserAuthenticator` en módulo separado
**Decisión:** Crear `pressreader_auth.py` con la clase `BrowserAuthenticator`. El downloader la importa.

**Rationale:** Separación de responsabilidades. El archivo principal ya tiene ~1600 líneas; agregar autenticación ahí lo haría más difícil de mantener.

### 6. Descarga de Publimetro vía sesión autenticada
**Decisión:** Para Publimetro, usar Playwright para navegar a la URL del periódico y extraer las URLs de imágenes de páginas directamente desde el DOM/red, en lugar de usar la API `GetPageKeys`.

**Rationale:** Si la API `GetPageKeys` requiere un token que no se puede obtener estáticamente para Publimetro, la alternativa es usar la sesión del browser para acceder a las páginas directamente. Playwright puede interceptar las requests de imágenes o extraer las URLs del visor de PressReader.

## Risks / Trade-offs

- **[Riesgo] Detección de bot por PressReader** → Mitigation: Playwright en modo headless con `--disable-blink-features=AutomationControlled` y user-agent real. Si se detecta, cambiar a headed (oculto) o agregar delays humanizados entre acciones.
- **[Riesgo] Cambio en el DOM del modal de login** → Mitigation: Usar selectores semánticos (atributo `name`, `type`, `aria-label`) en lugar de clases CSS que cambian frecuentemente. Agregar fallback con selector XPath.
- **[Riesgo] Token de Publimetro con formato diferente** → Mitigation: Loguear las requests interceptadas en modo debug para identificar el patrón de token correcto antes de hardcodear el extractor.
- **[Riesgo] Playwright no instalado en el entorno** → Mitigation: Documentar `pip install playwright && playwright install chromium` como paso de setup. Agregar check al inicio del script.
- **[Riesgo] Cambio de contraseña de la cuenta** → Mitigation: El script falla con mensaje claro indicando que las credenciales en `auth.env` deben actualizarse.

## Migration Plan

1. Instalar dependencias: `pip install playwright python-dotenv && playwright install chromium`
2. Crear `auth.env` con credenciales.
3. Agregar `pressreader_auth.py` (nueva clase, sin romper nada existente).
4. Modificar `pressreader_downloader.py`: en el flujo de descarga, si el token falla, llamar al autenticador y reintentar.
5. Probar manualmente con `Tiempo` (tiene tokens conocidos para comparar) y luego con `Publimetro`.
6. Los tokens en `press.ini` quedan como valores por defecto/caché; si están vacíos, el sistema los obtiene automáticamente.

**Rollback:** Revertir el import del autenticador y restaurar el comportamiento original de solo leer tokens de `press.ini`. No hay cambios destructivos en la configuración existente.

## Open Questions

- ¿El token que usa la API `GetPageKeys` es la misma cookie de sesión que Playwright puede extraer, o es un token separado generado por el backend de PressReader? Responder inspeccionando las requests de red tras el login manual.
- ¿Publimetro Colombia usa la misma API `GetPageKeys` con token, o requiere un flujo diferente (e.g., descarga directa de PDF)? Verificar en `https://www.pressreader.com/es/newspapers/n/publimetro-colombia`.
