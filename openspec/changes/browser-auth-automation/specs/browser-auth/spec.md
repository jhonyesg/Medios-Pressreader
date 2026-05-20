## ADDED Requirements

### Requirement: Login automatizado en PressReader
El sistema SHALL proveer una clase `BrowserAuthenticator` en `pressreader_auth.py` que use Playwright para abrir `https://www.pressreader.com/es`, hacer clic en el botón "Conectarse", completar el formulario de login con las credenciales configuradas en `auth.env`, y retornar el token/cookie de sesión válido tras login exitoso.

#### Scenario: Login exitoso con credenciales válidas
- **WHEN** `BrowserAuthenticator.get_fresh_token()` es llamado con credenciales válidas en `auth.env`
- **THEN** el método retorna un string no vacío con el token de acceso de PressReader

#### Scenario: Login fallido con credenciales incorrectas
- **WHEN** `BrowserAuthenticator.get_fresh_token()` es llamado con credenciales inválidas
- **THEN** el método lanza una excepción `AuthenticationError` con mensaje descriptivo

#### Scenario: Modal de login no aparece
- **WHEN** el botón "Conectarse" no es encontrado en la página dentro del timeout configurado
- **THEN** el método lanza una excepción con mensaje indicando que el selector del botón falló

### Requirement: Extracción de token de sesión
El sistema SHALL extraer el token de acceso interceptando las requests de red hacia `ingress.pressreader.com` que contienen el parámetro `accessToken`, o como fallback extrayendo la cookie de sesión relevante del storage de Playwright.

#### Scenario: Token extraído por intercepción de red
- **WHEN** el login es exitoso y PressReader realiza la primera request autenticada
- **THEN** el token es extraído del parámetro `accessToken` de la URL interceptada y retornado como string

#### Scenario: Fallback a extracción de cookie
- **WHEN** no se intercepta ninguna request con `accessToken` en los primeros 10 segundos post-login
- **THEN** el sistema extrae la cookie de sesión de Playwright como token alternativo

### Requirement: Ejecución en modo headless
El sistema SHALL ejecutar Playwright en modo headless por defecto, con opción de habilitar modo headed vía variable de entorno `PRESSREADER_HEADED=true` para depuración.

#### Scenario: Ejecución headless por defecto
- **WHEN** `BrowserAuthenticator` es instanciado sin configuración especial
- **THEN** Playwright se ejecuta sin abrir ventana de navegador visible

#### Scenario: Modo headed para depuración
- **WHEN** la variable de entorno `PRESSREADER_HEADED=true` está definida
- **THEN** Playwright abre una ventana de Chromium visible durante el proceso de login

### Requirement: Credenciales desde archivo seguro
El sistema SHALL leer las credenciales de login desde el archivo `auth.env` en el directorio del proyecto, usando las variables `PRESSREADER_EMAIL` y `PRESSREADER_PASSWORD`. Este archivo NO SHALL ser incluido en control de versiones.

#### Scenario: Credenciales cargadas correctamente
- **WHEN** `auth.env` existe con `PRESSREADER_EMAIL` y `PRESSREADER_PASSWORD` definidos
- **THEN** `BrowserAuthenticator` usa esas credenciales para el login

#### Scenario: Archivo auth.env ausente
- **WHEN** `auth.env` no existe en el directorio del proyecto
- **THEN** el sistema lanza una excepción clara indicando que `auth.env` es requerido
