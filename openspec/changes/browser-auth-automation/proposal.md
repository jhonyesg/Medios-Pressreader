## Why

Los tokens de acceso de PressReader caducan periódicamente y requieren actualización manual en `press.ini`, interrumpiendo las descargas automáticas de periódicos. Además, algunos medios como Publimetro no tienen un token estático identificable, lo que impide su descarga automatizada. Automatizar el login vía navegador elimina esta fricción operacional y hace el sistema verdaderamente autónomo.

## What Changes

- Se agrega un módulo de autenticación por navegador (Playwright) que hace login en `https://www.pressreader.com/es` y extrae cookies/tokens de sesión activos.
- El sistema de tokens en `press.ini` pasa a ser opcional: si está vacío o expirado, el módulo de autenticación obtiene uno nuevo automáticamente antes de la descarga.
- Se agrega soporte para descarga de **Publimetro** (`https://www.pressreader.com/es/newspapers/n/publimetro-colombia`) usando la sesión autenticada del navegador en lugar de tokens estáticos.
- Las credenciales de login se almacenan en un archivo de configuración seguro separado (no en `press.ini`).
- El flujo de descarga existente en `pressreader_downloader.py` se adapta para recibir tokens frescos del módulo de autenticación cuando los configurados fallan.

## Capabilities

### New Capabilities

- `browser-auth`: Login automatizado en PressReader vía Playwright: abre el modal de login, ingresa credenciales y extrae el token/cookie de sesión válido.
- `token-refresh`: Lógica de refresco automático de token: detecta token expirado o ausente y dispara `browser-auth` para obtener uno nuevo antes de reintentar la descarga.
- `publimetro-download`: Descarga de la edición diaria de Publimetro Colombia usando sesión autenticada por navegador (sin depender de token estático en `press.ini`).

### Modified Capabilities

<!-- No hay specs existentes que cambien de requisitos -->

## Impact

- **Código**: `pressreader_downloader.py` — se agrega un nuevo módulo/clase `BrowserAuthenticator` y se modifica el flujo de descarga para usar token fresco cuando el configurado falla.
- **Configuración**: `press.ini` — se agrega sección `[Auth]` para credenciales; los tokens en secciones de medios pasan a ser opcionales.
- **Dependencias nuevas**: `playwright` (Python), `python-dotenv` o equivalente para gestión segura de credenciales.
- **Credenciales**: Se crea archivo `.env` o `auth.ini` (excluido de git) con `email=alextorresja@gmail.com` y `password=...`.
- **Sistemas externos**: PressReader.com — el bot de Playwright debe pasar como navegador real para evitar detección.
