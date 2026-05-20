## ADDED Requirements

### Requirement: Detección de token expirado o inválido
El sistema SHALL detectar automáticamente cuando un token de acceso ha expirado o es inválido, identificando respuestas HTTP 401, 403, o respuestas vacías/con 0 páginas de la API `GetPageKeys` de PressReader.

#### Scenario: Token expirado detectado por código HTTP
- **WHEN** la API `GetPageKeys` retorna HTTP 401 o 403 con el token configurado
- **THEN** el sistema registra el evento como "token expirado" e inicia el proceso de refresco

#### Scenario: Token inválido detectado por respuesta vacía
- **WHEN** la API `GetPageKeys` retorna HTTP 200 pero con 0 páginas o lista de claves vacía
- **THEN** el sistema trata la respuesta como token potencialmente inválido y reintenta con refresco si no había descargado ninguna página

### Requirement: Refresco automático de token
El sistema SHALL llamar a `BrowserAuthenticator.get_fresh_token()` automáticamente cuando detecta un token inválido, actualizar el token en memoria para el resto de la sesión de descarga, y reintentar la descarga fallida con el nuevo token. El refresco SHALL ocurrir máximo una vez por sesión de descarga para evitar bucles infinitos.

#### Scenario: Refresco exitoso y reintento
- **WHEN** el token está expirado y `get_fresh_token()` retorna un token válido
- **THEN** el sistema reintenta la descarga con el nuevo token y continúa normalmente

#### Scenario: Refresco falla tras login
- **WHEN** `get_fresh_token()` lanza una excepción durante el refresco
- **THEN** el sistema registra el error, NO reintenta el refresco, y termina la descarga con mensaje de error claro

#### Scenario: Límite de un refresco por sesión
- **WHEN** ya se realizó un refresco en la sesión actual y el nuevo token también falla
- **THEN** el sistema NO llama a `get_fresh_token()` nuevamente y termina con error

### Requirement: Persistencia opcional de token actualizado
El sistema SHALL ofrecer un modo donde el token obtenido por refresco se escribe de vuelta en `press.ini` para que la próxima ejecución no requiera autenticación por navegador, controlado por la opción `save_refreshed_token = True` en la sección del medio.

#### Scenario: Token guardado en press.ini tras refresco
- **WHEN** `save_refreshed_token = True` está configurado y el refresco es exitoso
- **THEN** el nuevo token se escribe en el campo `token` o `tokens` del medio en `press.ini`

#### Scenario: Token no guardado por defecto
- **WHEN** `save_refreshed_token` no está configurado o es `False`
- **THEN** el token fresco se usa solo en memoria para la sesión actual, sin modificar `press.ini`
