## ADDED Requirements

### Requirement: Descarga de Publimetro Colombia mediante sesión autenticada
El sistema SHALL soportar la descarga de la edición diaria de Publimetro Colombia (`https://www.pressreader.com/es/newspapers/n/publimetro-colombia`) usando la sesión autenticada obtenida por `BrowserAuthenticator`, sin depender de un token estático en `press.ini`. La sección `[Publimetro]` de `press.ini` SHALL configurar `use_browser_auth = True` para activar este modo.

#### Scenario: Descarga exitosa con sesión autenticada
- **WHEN** se ejecuta `pressreader_downloader.py Publimetro` y `use_browser_auth = True`
- **THEN** el sistema hace login, obtiene el token de sesión, y descarga las páginas de la edición del día

#### Scenario: Fallback a tokens estáticos si están disponibles
- **WHEN** `use_browser_auth = True` pero los tokens en `press.ini` son válidos y no han expirado
- **THEN** el sistema usa los tokens estáticos primero (más rápido) y solo usa browser auth si fallan

### Requirement: Identificación de la edición del día para Publimetro
El sistema SHALL determinar el `issue` (identificador de edición) de Publimetro para la fecha solicitada, navegando a la página del periódico con Playwright e interceptando la request de la API que contiene el `issue` de la edición más reciente.

#### Scenario: Issue identificado correctamente
- **WHEN** Playwright navega a la URL de Publimetro con sesión autenticada
- **THEN** el sistema intercepta la request a la API de PressReader y extrae el `issue` del día actual

#### Scenario: Edición no disponible para la fecha solicitada
- **WHEN** no existe edición de Publimetro para la fecha solicitada
- **THEN** el sistema registra "Edición no disponible para [fecha]" y termina sin error fatal

### Requirement: Compatibilidad con el pipeline existente de descarga
Las páginas descargadas de Publimetro mediante browser auth SHALL ser procesadas por el mismo pipeline de imágenes/PDF existente en `pressreader_downloader.py`, sin cambios en los pasos de composición, FTP upload, y lock file.

#### Scenario: Páginas procesadas igual que otros medios
- **WHEN** las URLs de imágenes de Publimetro son obtenidas vía browser auth
- **THEN** las imágenes se descargan, procesan y envían por FTP usando el mismo código que Tiempo y Espectador
