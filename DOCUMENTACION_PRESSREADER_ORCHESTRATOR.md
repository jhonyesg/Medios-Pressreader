# PressReader Downloader Orchestrator

## Resumen del Proyecto

El **PressReader Downloader Orchestrator** es un sistema unificado para descargar, procesar y distribuir periódicos y revistas de PressReader. Este sistema centralizado reemplaza los múltiples scripts individuales (`tiempo.py`, `espectador.py`, `opinion.py`, `publimetro_press.py`, `Motor.py`) con un único script orquestador configurable y robusto.

### Características Principales

- **Descarga inteligente**: Sistema de reintentos con timeout y watchdog para evitar procesos colgados
- **Procesamiento OCR**: Extracción de texto usando Tesseract con correcciones automáticas
- **Validación de calidad**: Verificación automática de imágenes descargadas y PDFs generados
- **Reinicio inteligente**: Permite continuar descargas interrumpidas sin repetir trabajo
- **Organización de archivos**: Estructura clara con subcarpetas `imagenes/` y `pdf_paginas_ocr/`
- **FTP automático**: Subida de imágenes, PDFs individuales y PDF final
- **Mejora de imágenes**: Soporte para realesrgan-ncnn-vulkan para mejor calidad
- **PDF de validación**: Generación de PDF con solo texto para validar calidad OCR

## Archivos del Proyecto

### 1. `press.ini`
Archivo de configuración central que contiene todas las variables específicas de cada medio. Incluye:

- **Configuración global** ([DEFAULT]): DPI, calidad de imagen, user agent, rutas base
- **Configuración FTP** ([FTP]): Host, usuario, contraseña
- **Configuración por medio**: Tokens, escalas de calidad, rutas FTP, configuraciones OCR

### 2. `pressreader_downloader.py`
Script orquestador principal (~1976 líneas) que implementa:

- Clase [`PressReaderDownloader`](pressreader_downloader.py:60) con todas las funcionalidades
- Procesamiento de argumentos de línea de comandos
- Manejo de archivos de lock para evitar ejecuciones concurrentes
- Soporte para procesamiento especial (OCR, mejora de imágenes)
- Subida automática a FTP con subcarpetas organizadas

## Arquitectura del Sistema

### Flujo de Trabajo Estándar

```
┌─────────────────────────────────────────────────────────────┐
│  1. Cargar configuración del medio desde press.ini          │
├─────────────────────────────────────────────────────────────┤
│  2. Configurar archivo de lock (evita ejecuciones dobles)  │
├─────────────────────────────────────────────────────────────┤
│  3. Procesar fecha (usar --fecha o fecha actual)           │
├─────────────────────────────────────────────────────────────┤
│  4. Configurar rutas de trabajo                            │
│     - Directorio base: rutafin/                            │
│     - Subcarpeta imagenes/                                  │
│     - Subcarpeta pdf_paginas_ocr/                           │
├─────────────────────────────────────────────────────────────┤
│  5. Verificar si PDF ya existe (si existe, saltar)         │
├─────────────────────────────────────────────────────────────┤
│  6. Obtener PageKeys desde API de PressReader              │
├─────────────────────────────────────────────────────────────┤
│  7. Descargar imágenes con reintentos y timeout            │
│     - Validar y verificar progreso previo                  │
│     - Probar múltiples escalas de calidad                  │
│     - Guardar en imagenes/                                  │
├─────────────────────────────────────────────────────────────┤
│  8. Crear PDFs individuales con OCR                        │
│     - Extraer texto de cada imagen                         │
│     - Aplicar correcciones del JSON                        │
│     - Guardar en pdf_paginas_ocr/                          │
├─────────────────────────────────────────────────────────────┤
│  9. Unir PDFs en PDF final                                 │
├─────────────────────────────────────────────────────────────┤
│ 10. Crear PDF de validación de texto (extra)               │
├─────────────────────────────────────────────────────────────┤
│ 11. Subir todo al FTP                                       │
│     - Imágenes a imagenes/                                  │
│     - PDFs individuales a pdf_paginas_ocr/                 │
│     - PDF final a raíz de fecha/                            │
├─────────────────────────────────────────────────────────────┤
│ 12. Limpiar temporales y liberar lock                       │
└─────────────────────────────────────────────────────────────┘
```

### Estructura de Directorios

```
rutafin/
├── imagenes/                    # Imágenes JPG descargadas
│   ├── 01.jpg
│   ├── 02.jpg
│   └── ...
├── pdf_paginas_ocr/           # PDFs individuales con OCR
│   ├── 01.pdf
│   ├── 02.pdf
│   └── ...
├── Medio_YYYYMMDD.pdf          # PDF final unificado
├── Medio_YYYYMMDD_texto_validacion.pdf  # PDF para validar OCR
└── (archivos temporales eliminados al final)
```

## Uso del Orquestador

### Comandos Básicos

```bash
# Procesar TODOS los medios en secuencia
python pressreader_downloader.py

# Descargar el medio "Tiempo" para hoy
python pressreader_downloader.py Tiempo

# Descargar para una fecha específica
python pressreader_downloader.py Tiempo --fecha 2024-06-01

# Descargar con fecha personalizada (alias)
python pressreader_downloader.py Opinion --custom-fecha 2024-12-25

# Ver todos los medios disponibles y ayuda
python pressreader_downloader.py --help

# Especificar archivo de configuración personalizado
python pressreader_downloader.py Tiempo --config mi_config.ini
```

### Medios Disponibles

| Medio | Tipo | Tokens | OCR | Características Especiales |
|-------|------|--------|-----|---------------------------|
| `Tiempo` | Periódico | Múltiples (14) | ✅ | Escalas fijas [181,184,180] |
| `Espectador` | Periódico | Único | ✅ | Escalas [179,180] |
| `Opinion` | Periódico | Único | ✅ | Escalas dinámicas |
| `Publimetro` | Periódico | Múltiples (3) | ✅ | Escalas [176,174,180] |
| `Motor` | Revista | Único | ✅ | Mejora de imágenes (realesrgan) |

### Argumentos de Línea de Comandos

| Argumento | Tipo | Descripción | Ejemplo |
|-----------|------|-------------|---------|
| `medio` | opcional | Medio a procesar (si se omite, procesa todos) | `Tiempo` |
| `--fecha` | opcional | Fecha en formato YYYY-MM-DD | `--fecha 2024-06-01` |
| `--custom-fecha` | opcional | Alias para `--fecha` | `--custom-fecha 2024-12-25` |
| `--config` | opcional | Archivo de configuración (default: press.ini) | `--config custom.ini` |

## Configuración del Archivo `press.ini`

### Estructura Completa

```ini
[DEFAULT]
# Configuración global para todos los medios
dpi = 250
calidad = 90
user_agent = Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
rutabase = C:/Users/
base_lock = /Downloads/

[FTP]
# Configuración FTP común
host = 192.168.0.118
user = jaquebot
pass = SDmaw7YsyF7SYiSw

[Tiempo]
# Configuración específica del medio
impreso = Tiempo
libro = 9gsw
use_tokens = True
tokens = token1,token2,token3,...
url_ids = 00000000001001,00000051001001
tm = 181,184,180
ftp_path = /Impresos/2025/02
base02 = /Downloads/Diarios_local/Tiempo/
lock_file = Tiempo.lock
ocr_enabled = True
correcciones_json = /Downloads/correciones_tiempos.json
```

### Parámetros de Configuración

#### Parámetros Obligatorios

| Parámetro | Tipo | Descripción | Ejemplo |
|-----------|------|-------------|---------|
| `impreso` | string | Nombre del medio (usado en nombre de archivos) | `"Tiempo"` |
| `libro` | string | Código del libro en PressReader | `"9gsw"` |
| `token` o `tokens` | string | Token(s) de acceso a la API | `"abc123..."` |
| `url_ids` | string | IDs de URL separados por coma | `"00000000001001,00000051001001"` |

#### Parámetros de Descarga

| Parámetro | Tipo | Descripción | Ejemplo |
|-----------|------|-------------|---------|
| `tm` | string | Escalas de calidad fijas | `"181,184,180"` |
| `tm_range_inicial` | int | Escala inicial para rango dinámico | `208` |
| `tm_range_mas` | int | Escalas adicionales hacia arriba | `8` |
| `tm_range_menos` | int | Escalas adicionales hacia abajo | `7` |
| `use_tokens` | bool | Usar múltiples tokens | `True` o `False` |

#### Parámetros de Procesamiento

| Parámetro | Tipo | Descripción | Ejemplo |
|-----------|------|-------------|---------|
| `ocr_enabled` | bool | Habilitar procesamiento OCR | `True` o `False` |
| `correcciones_json` | string | Archivo JSON con correcciones OCR | `"/Downloads/correcciones.json"` |
| `special_processing` | string | Procesamiento especial | `null` o `"selenium"` |
| `requires_enhancement` | bool | Mejorar imágenes con realesrgan | `True` o `False` |

#### Parámetros de Sistema

| Parámetro | Tipo | Descripción | Ejemplo |
|-----------|------|-------------|---------|
| `ftp_path` | string | Ruta FTP donde subir archivos | `"/Impresos/2025/02"` |
| `base02` | string | Ruta base local para archivos | `"/Downloads/Diarios_local/Tiempo/"` |
| `lock_file` | string | Nombre del archivo de lock | `"Tiempo.lock"` |

## Funcionalidades Avanzadas

### 1. Sistema de Descarga Inteligente

#### Validación de PageKeys
El sistema valida exhaustivamente la respuesta de PageKeys antes de descargar:

```python
def validar_pagekeys_completos(datoskey, medio_name):
    # Validación robusta de PageKeys
    # - Verifica que PageKeys existe
    # - Valida que no está vacío
    # - Comprueba que hay más de 1 PageKey
    # - Valida estructura de cada PageKey
```

#### Reinicio de Descargas
Si una descarga es interrumpida, el sistema puede continuar:

- Detecta páginas ya descargadas
- Elimina imágenes truncadas o inválidas
- Reanuda desde la primera página faltante
- Limpia archivos residuales fuera del rango esperado

#### Reintentos y Timeout
Las descargas incluyen:

- **Timeout por defecto**: 30 segundos por intento
- **Máximo de reintentos**: 3 intentos por escala
- **Watchdog**: Mata procesos colgados automáticamente
- **Espera entre reintentos**: 3 segundos

### 2. Procesamiento OCR

#### Método Nación (sin reducción)
El sistema usa un método optimizado para OCR:

```python
def crear_pdf_con_texto_superpuesto(imagen_path, salida_pdf_path, 
                                     y_adjustment=10, font_scale=0.45):
    # 1. Cargar imagen original (sin reducción)
    # 2. Convertir a escala de grises
    # 3. Aplicar autocontrast (menos agresivo)
    # 4. OCR con Tesseract (parámetros optimizados)
    # 5. Crear PDF con imagen de fondo + texto superpuesto invisible
```

#### Características del OCR

- **Correcciones automáticas**: Aplica correcciones desde JSON
- **Ajuste de espacios**: Corrige espacios entre minúsculas y mayúsculas
- **Factor de escala dinámico**: Ajusta según altura del texto
- **Validación de confianza**: Solo usa texto con confianza > 0

#### Formato de Archivo de Correcciones

```json
{
    "palabra_incorrecta": "palabra_correcta",
    "otro_termino": "termino_corregido"
}
```

### 3. PDF de Validación de OCR

El sistema genera automáticamente un PDF especial para validar la calidad del OCR:

- **Ubicación**: `rutafin/Medio_YYYYMMDD_texto_validacion.pdf`
- **Contenido**: Solo texto (sin imagen de fondo)
- **Propósito**: Validar rápidamente la calidad del OCR
- **Subida al FTP**: Se sube automáticamente junto con el PDF final

### 4. Subida a FTP

El sistema organiza los archivos en el FTP con una estructura clara:

```
FTP/ftp_path/YYMMDD/
├── Medio_YYYYMMDD.pdf              # PDF final
├── Medio_YYYYMMDD_texto_validacion.pdf  # PDF de validación
├── imagenes/                       # Imágenes JPG descargadas
│   ├── 01.jpg
│   ├── 02.jpg
│   └── ...
└── pdf_paginas_ocr/               # PDFs individuales con OCR
    ├── 01.pdf
    ├── 02.pdf
    └── ...
```

### 5. Procesamiento Especial para Motor

La revista Motor tiene un flujo especial que incluye:

1. **Descarga de imágenes**: Similar a otros medios
2. **Mejora de imágenes**: Uso de realesrgan-ncnn-vulkan
3. **OCR**: Aplicación de OCR a imágenes mejoradas
4. **Optimización**: Compresión de PDFs con Ghostscript
5. **Unión**: Combinación de todos los PDFs

```python
def mejorar_jpg(input_folder, output_folder, workers=4):
    # Mejora imágenes usando realesrgan-ncnn-vulkan
    # - Procesamiento en paralelo (4 workers por defecto)
    # - Verificación de validez de imágenes
    # - Reporte de éxitos y fallos
```

## Clases y Métodos Principales

### Clase: PressReaderDownloader

#### Inicialización
```python
def __init__(self, config_file='press.ini'):
    # Carga configuración desde archivo INI
    # Configura consola para UTF-8
    # Inicializa variables de instancia
```

#### Métodos de Configuración

| Método | Línea | Descripción |
|--------|-------|-------------|
| [`_setup_console()`](pressreader_downloader.py:80) | 80-109 | Configura consola Windows para UTF-8 |
| [`_setup_console_title(title)`](pressreader_downloader.py:111) | 111-116 | Establece título de la consola |
| [`load_medio_config(medio_name)`](pressreader_downloader.py:118) | 118-151 | Carga configuración del medio específico |
| [`setup_lock_file()`](pressreader_downloader.py:153) | 153-178 | Crea archivo de bloqueo |
| [`cleanup_lock_file()`](pressreader_downloader.py:180) | 180-187 | Elimina archivo de bloqueo |

#### Métodos de Descarga

| Método | Línea | Descripción |
|--------|-------|-------------|
| [`parse_date_args(args)`](pressreader_downloader.py:189) | 189-201 | Procesa argumentos de fecha |
| [`calidades(...)`](pressreader_downloader.py:461) | 461-549 | Descarga imagen probando diferentes escalas con reintentos |
| [`intentar_descargar_imagenes(...)`](pressreader_downloader.py:861) | 861-946 | Intenta descargar todas las imágenes con un token específico |
| [`descargar_con_tokens(...)`](pressreader_downloader.py:948) | 948-963 | Descarga probando múltiples tokens y URL IDs |
| [`verificar_progreso_descarga(...)`](pressreader_downloader.py:764) | 764-859 | Verifica qué páginas ya están descargadas |

#### Métodos de Procesamiento

| Método | Línea | Descripción |
|--------|-------|-------------|
| [`crear_pdf_con_texto_superpuesto(...)`](pressreader_downloader.py:1005) | 1005-1126 | Crea PDF con texto OCR superpuesto (método Nación) |
| [`crear_pdf_texto_desde_imagenes(...)`](pressreader_downloader.py:1128) | 1128-1259 | Crea PDF completo de solo texto para validación |
| [`crear_pdf_con_texto_superpuesto_para_carpeta(...)`](pressreader_downloader.py:1261) | 1261-1308 | Procesa todas las imágenes de una carpeta |
| [`crear_pdf_final_directo(...)`](pressreader_downloader.py:570) | 570-709 | Crea PDF final desde imágenes (método Fitz) |
| [`unir_pdfs(...)`](pressreader_downloader.py:551) | 551-568 | Une múltiples PDFs en uno solo |

#### Métodos de Utilidad

| Método | Línea | Descripción |
|--------|-------|-------------|
| [`is_image_valid(path)`](pressreader_downloader.py:450) | 450-459 | Verifica si un JPG es válido |
| [`runcmd(cmd, verbose, timeout)`](pressreader_downloader.py:203) | 203-231 | Ejecuta comando shell con timeout |
| [`validar_pagekeys_completos(...)`](pressreader_downloader.py:722) | 722-762 | Validación robusta de PageKeys |
| [`cargar_correcciones(archivo_json)`](pressreader_downloader.py:965) | 965-977 | Carga correcciones OCR desde JSON |
| [`corregir_texto(texto, correcciones)`](pressreader_downloader.py:979) | 979-985 | Aplica correcciones al texto OCR |

#### Métodos de FTP

| Método | Línea | Descripción |
|--------|-------|-------------|
| [`upload_images_to_ftp(...)`](pressreader_downloader.py:276) | 276-321 | Sube imágenes a subcarpeta imagenes/ |
| [`upload_pdf_to_ftp(...)`](pressreader_downloader.py:323) | 323-349 | Sube PDF al FTP |
| [`upload_pdfs_to_ftp(...)`](pressreader_downloader.py:351) | 351-397 | Sube PDFs a subcarpeta pdf_paginas_ocr/ |

#### Métodos Principales de Flujo

| Método | Línea | Descripción |
|--------|-------|-------------|
| [`process_single_medium(medio_name, args)`](pressreader_downloader.py:1554) | 1554-1842 | Procesa un solo medio (flujo principal de 10 pasos) |
| [`process_all_media(args)`](pressreader_downloader.py:1844) | 1844-1906 | Procesa todos los medios en secuencia |
| [`main(args)`](pressreader_downloader.py:1908) | 1908-1915 | Punto de entrada principal |

## Detalle del Flujo Principal (10 Pasos)

### PASO 1: Cargar Configuración del Medio
```python
# Líneas 1566-1568
print(f"📋 PASO 1/10: Cargando configuración del medio {medio_name}...")
self.load_medio_config(medio_name)
```

**Salida esperada:**
```
Configuración cargada para medio: Tiempo
Tokens disponibles: 14
Escalas de calidad (tm): [181, 184, 180]
OCR habilitado: True
Procesamiento especial: None
```

### PASO 2: Configurar Archivo de Lock
```python
# Líneas 1571-1573
print(f"🔒 PASO 2/10: Configurando archivo de lock para evitar ejecuciones concurrentes...")
self.setup_lock_file()
```

**Salida esperada:**
```
Archivo de lock creado: C:/Users/usuario/Downloads/Tiempo.lock
```

### PASO 3: Procesar Fecha
```python
# Líneas 1577-1583
print(f"📅 PASO 3/10: Procesando fecha...")
now = self.parse_date_args(args)
year = format(now.year)
mes = f"{now.month:02d}"
dia = f"{now.day:02d}"
```

**Salida esperada:**
```
Fecha seleccionada: 2025-02-23
```

### PASO 4: Configurar Rutas de Trabajo
```python
# Líneas 1586-1606
print(f"💾 PASO 4/10: Configurando rutas de trabajo...")
rutafin = os.path.join(rutabase, username, base02, f"{year}{mes}{dia}")
output = os.path.join(rutabase, username, base02,
                      f"{self.medio_config['impreso']}_{year}{mes}{dia}.pdf")
```

**Salida esperada:**
```
Ruta de trabajo: C:/Users/usuario/Downloads/Diarios_local/Tiempo/20250223
Archivo de salida: C:/Users/usuario/Downloads/Diarios_local/Tiempo/Tiempo_20250223.pdf
Directorios creados
```

### PASO 5: Crear Subcarpetas de Trabajo
```python
# Líneas 1609-1616
ruta_imagenes = os.path.join(rutafin, "imagenes")
ruta_paginas_ocr = os.path.join(rutafin, "pdf_paginas_ocr")
os.makedirs(ruta_imagenes, exist_ok=True)
os.makedirs(ruta_paginas_ocr, exist_ok=True)
```

**Salida esperada:**
```
PASO 5/10: Creando subcarpetas de trabajo...
Carpetas creadas: imagenes/, pdf_paginas_ocr/
```

### PASO 6: Verificar Si PDF Ya Existe
```python
# Líneas 1619-1624
if os.path.exists(output):
    file_size = os.path.getsize(output) / 1024 / 1024
    print(f"✅ PDF final ya existe: {output} ({file_size:.1f} MB)")
    print("⏭️  Saltando proceso de descarga")
    return True  # Consideramos éxito si ya existe
```

**Salida esperada (si existe):**
```
PDF final ya existe: C:/Users/.../Tiempo_20250223.pdf (45.2 MB)
Saltando proceso de descarga
```

### PASO 7: Configurar URLs y Parámetros
```python
# Líneas 1629-1638
url_base = 'https://ingress.pressreader.com/services/IssueInfo/GetPageKeys?accessToken='
libro = self.medio_config['libro']
url_ids = [id.strip() for id in self.medio_config.get('url_ids', '').split(',') if id.strip()]
cad01 = f'https://i.prcdn.co/img?file={libro}'
cad03 = '&ticket='
```

**Salida esperada:**
```
PASO 7/10: Configurando URLs y parámetros de descarga...
Libro: 9gsw
ID URLs: ['00000000001001', '00000051001001']
Configuración de URLs completada
```

### PASO 8: Descargar Imágenes
```python
# Líneas 1644-1672
if self.medio_config.get('use_tokens', 'False').lower() == 'true':
    # Probar con múltiples tokens
    if not self.descargar_con_tokens(url_base, libro, year, mes, dia,
                                      url_ids, cad01, cad03, rutafin):
        print("\n❌ Error: No se pudo completar la descarga")
        return False
```

**Salida esperada:**
```
PASO 8/10: Descargando imágenes desde PressReader...
──────────────────────────────────────────────────────────────
Modo: Múltiples tokens (14 tokens disponibles)
Token: token1
✅ PageKeys válidas para Tiempo: 45 páginas
Número de páginas esperadas: 45
Iniciando descarga completa de 45 páginas...
📥 Descargando página 01...
  📁 Ruta de descarga: C:/.../20250223/imagenes/01.jpg
Intentando descarga con wget (scale=181, timeout_watchdog=30s): https://...
  Intento 1/3...
  ✅ Descarga exitosa: C:/.../20250223/imagenes/01.jpg (102400 bytes)
...
──────────────────────────────────────────────────────────────
PASO 8/10 completado: Descarga de imágenes finalizada exitosamente
```

### PASO 9: Crear PDFs Individuales con OCR
```python
# Líneas 1675-1688
print(f"📄 PASO 9/10: Creando PDFs individuales con OCR desde imágenes...")
self.crear_pdf_con_texto_superpuesto_para_carpeta(
    ruta_imagenes, ruta_paginas_ocr, y_adjustment=10, font_scale=0.45)
```

**Salida esperada:**
```
PASO 9/10: Creando PDFs individuales con OCR desde imágenes...
Leyendo imágenes desde: C:/.../20250223/imagenes/
Se procesarán 45 imágenes con OCR
──────────────────────────────────────────────────────────────
[PDF_CON_OCR] Procesando imagen 1/45: C:/.../imagenes/01.jpg
  🖼️ Procesando imagen: 01.jpg
  📐 Dimensiones: (2048, 3072)
  🔍 Iniciando OCR (método Nación - sin reducción)...
  ✅ OCR completado - 125 bloques de texto detectados
  ✅ PDF con OCR creado: C:/.../pdf_paginas_ocr/01.pdf
...
──────────────────────────────────────────────────────────────
PASO 9/10 completado: PDFs individuales con OCR creados
```

### PASO 10: Subir a FTP
```python
# Líneas 1772-1821
print(f"📤 PASO 10/10: Subiendo archivos al FTP desde subcarpetas...")
self.upload_images_to_ftp(ruta_imagenes, ftp_path)
self.upload_pdfs_to_ftp(ruta_paginas_ocr, ftp_path)
self.upload_pdf_to_ftp(output, ftp_path)
```

**Salida esperada:**
```
PASO 10/10: Subiendo archivos al FTP desde subcarpetas...
Host FTP: 192.168.0.118
Ruta FTP: /Impresos/2025/02
  📷 Subiendo 45 imágenes desde: imagenes/
Imagen 01.jpg subida a imagenes/ con éxito
...
  📄 Subiendo 45 PDFs de páginas desde: pdf_paginas_ocr/
  ✅ PDF 01.pdf subido a pdf_paginas_ocr/ con éxito
...
  📝 Subiendo PDF de validación de OCR al FTP...
     📄 Archivo: Tiempo_20250223_texto_validacion.pdf
     ✅ PDF de validación subido al FTP exitosamente
  📄 Subiendo PDF final al FTP...
✅ Archivo PDF subido al FTP exitosamente
✅ PASO 10/10 completado: Subida al FTP finalizada
```

## Resolución de Problemas

### Problemas Comunes y Soluciones

#### 1. "Script already running"
**Causa:** Archivo de lock existente de una ejecución previa que no terminó correctamente.

**Solución:**
```bash
# 1. Verificar que no hay procesos en ejecución
tasklist | findstr python

# 2. Si no hay procesos activos, eliminar el archivo de lock manualmente
# El lock file está en: rutabase/base_lock/medio.lock
# Ejemplo: C:/Users/usuario/Downloads/Tiempo.lock
del "C:/Users/usuario/Downloads/Tiempo.lock"
```

#### 2. "Error FTP"
**Causa:** Problemas de conectividad, credenciales incorrectas o ruta FTP no válida.

**Solución:**
```bash
# 1. Verificar configuración FTP en press.ini
# 2. Probar conexión manualmente
ftp 192.168.0.118
# usuario: jaquebot
# contraseña: SDmaw7YsyF7SYiSw

# 3. Verificar que la ruta FTP existe
# Si no existe, el script intentará crearla automáticamente
```

#### 3. "No se pudo completar la descarga"
**Causa:** Tokens expirados, URLs inválidas o problemas de conectividad.

**Solución:**
```bash
# 1. Verificar tokens en press.ini
# 2. Actualizar tokens si están expirados
# 3. Verificar conexión a internet
# 4. Revisar logs para identificar página específica que falló

# El sistema automáticamente:
# - Prueba múltiples tokens
# - Prueba múltiples escalas de calidad
# - Reintenta hasta 3 veces por escala
# - Permite continuar desde donde se quedó
```

#### 4. "Error de OCR"
**Causa:** Archivos de correcciones faltantes o formato incorrecto.

**Solución:**
```bash
# 1. Verificar que correcciones_json existe en la ruta especificada
# 2. Validar formato del JSON (debe ser un diccionario simple)
# 3. Verificar encoding UTF-8

# Ejemplo de formato correcto:
{
    "palabra_incorrecta": "palabra_correcta",
    "otro_termino": "termino_corregido"
}
```

#### 5. "Timeout en descarga"
**Causa:** Conexión lenta o problema con el servidor de PressReader.

**Solución:**
```bash
# El sistema maneja automáticamente:
# - Timeout de 30 segundos por intento
# - Watchdog que mata procesos colgados
# - Reintentos automáticos (3 por escala)

# Si persiste el problema:
# 1. Verificar conexión a internet
# 2. Ejecutar el script nuevamente (continuará desde donde se quedó)
```

#### 6. "Imágenes inválidas/truncadas"
**Causa:** Descargas incompletas o archivos corruptos.

**Solución:**
```bash
# El sistema automáticamente:
# - Valida cada imagen descargada
# - Elimina imágenes inválidas
# - Reintenta descargarlas

# Manualmente:
# 1. Eliminar la carpeta imagenes/
# 2. Ejecutar el script nuevamente
```

### Debugging Avanzado

#### Habilitar Logs Detallados
El script ya incluye salida detallada por defecto:

```bash
python pressreader_downloader.py Tiempo
```

**Niveles de logging:**
- 📋 PASO N/10: Indica el paso actual del proceso
- ✅ Éxito: Operación completada correctamente
- ❌ Error: Problema encontrado
- ⚠️ Advertencia: Situación no crítica pero digna de atención
- 🔍 Información: Detalles del proceso
- 🔄 En progreso: Operación en curso

#### Verificar Configuración Cargada
```bash
python pressreader_downloader.py Tiempo
```

**Salida esperada:**
```
Configuración cargada para medio: Tiempo
Tokens disponibles: 14
Escalas de calidad (tm): [181, 184, 180]
OCR habilitado: True
Procesamiento especial: None
```

#### Analizar Traceback de Errores
El script incluye manejo robusto de errores con stack traces detallados:

```
❌ ERROR PROCESANDO Tiempo
══════════════════════════════════════════════════════════════
💥 Error: NameError: name 'undefined_variable' is not defined
📍 Tipo de error: NameError

📋 Traceback completo:
Traceback (most recent call last):
  File "pressreader_downloader.py", line 1842, in process_single_medium
    # código problemático
NameError: name 'undefined_variable' is not defined
══════════════════════════════════════════════════════════════
```

#### Verificar Estado de Descarga
```bash
# El sistema muestra el progreso automáticamente:
📊 Estado de descarga: 30/45 páginas completadas
🔄 Páginas pendientes: [31, 32, 33, 34, 35, 36, 37, 38, 39, 40]
```

## Mantenimiento

### Actualizar Tokens

1. Editar `press.ini`
2. Buscar la sección del medio (ej: `[Tiempo]`)
3. Actualizar `token` o `tokens`
4. Guardar archivo

```ini
[Tiempo]
tokens = nuevo_token1,nuevo_token2,nuevo_token3,...
```

### Agregar Nuevo Medio

1. Agregar nueva sección en `press.ini`
2. Configurar todos los parámetros necesarios
3. Probar con `--help` para verificar que aparece en la lista

```ini
[NuevoMedio]
impreso = NuevoMedio
libro = codigo_libro
use_tokens = True
tokens = token1,token2
url_ids = 00000000001001
tm = 181,184,180
ftp_path = /Impresos/2025/02
base02 = /Downloads/Diarios_local/NuevoMedio/
lock_file = NuevoMedio.lock
ocr_enabled = True
correcciones_json = /Downloads/correciones_nuevo.json
```

### Modificar Escalas de Calidad

1. Editar `press.ini`
2. Buscar `tm` o `tm_range_*` en la sección del medio
3. Modificar valores
4. Guardar archivo

```ini
[Tiempo]
# Escalas fijas
tm = 181,184,180

# O escalas dinámicas
tm_range_inicial = 208
tm_range_mas = 8
tm_range_menos = 7
```

### Configurar OCR

1. Agregar `ocr_enabled = True` en la sección del medio
2. Especificar `correcciones_json` con la ruta correcta
3. Crear archivo JSON con correcciones

```ini
[Tiempo]
ocr_enabled = True
correcciones_json = /Downloads/correciones_tiempos.json
```

### Verificar Archivos de Correcciones OCR

```bash
# El archivo JSON debe tener el siguiente formato:
{
    "palabra_incorrecta": "palabra_correcta",
    "otro_termino": "termino_corregido"
}

# Validar que sea un JSON válido
python -c "import json; json.load(open('correcciones.json'))"
```

## Comparación con Scripts Antiguos

### Funcionalidades

| Funcionalidad | Scripts Antiguos | Orquestador |
|---------------|------------------|-------------|
| **Descarga de imágenes** | ✅ | ✅ (mejorada con reintentos) |
| **Múltiples tokens** | ✅ | ✅ (mejorada) |
| **Conversión a PDF** | ✅ | ✅ (mejorada) |
| **Optimización PDF** | ✅ | ✅ (Ghostscript) |
| **Subida a FTP** | ✅ | ✅ (mejorada con subcarpetas) |
| **OCR texto** | ❌/✅ (algunos) | ✅ (todos, método Nación) |
| **Mejora de imágenes** | ❌ | ✅ (realesrgan-ncnn-vulkan) |
| **Selenium** | ✅ (Motor) | ✅ (integrado) |
| **Manejo de lock** | ✅ | ✅ (mejorado) |
| **Configuración centralizada** | ❌ | ✅ (press.ini) |
| **Argumentos de línea de comandos** | ❌ | ✅ |
| **Manejo de errores** | ⚠️ | ✅ (robusto con traceback) |
| **Reinicio inteligente** | ❌ | ✅ (continuar desde donde se quedó) |
| **Timeout y watchdog** | ❌ | ✅ (evita procesos colgados) |
| **PDF de validación OCR** | ❌ | ✅ (texto completo) |
| **Subcarpetas organizadas** | ❌ | ✅ (imagenes/, pdf_paginas_ocr/) |
| **Validación de PageKeys** | ❌ | ✅ (robusta) |
| **Correcciones OCR** | ⚠️ | ✅ (desde JSON) |

### Flujo de Trabajo

#### Antes (Scripts Individuales)
```
1. Ejecutar python tiempo.py para Tiempo
2. Ejecutar python espectador.py para Espectador
3. etc.
4. Cada script tenía su propia configuración hardcodeada
5. Mantenimiento manual de cada archivo
6. No había sistema de reinicio
```

#### Ahora (Orquestador)
```
1. python pressreader_downloader.py              (todos los medios)
2. python pressreader_downloader.py Tiempo --fecha 2024-06-01
3. python pressreader_downloader.py Motor
4. Configuración centralizada en press.ini
5. Mantenimiento simplificado
6. Sistema de reinicio automático
7. Procesamiento unificado
```

## Ventajas del Nuevo Sistema

### Para el Desarrollador
- **Menos código duplicado**: Una sola implementación de funciones comunes (~1976 líneas vs múltiples scripts)
- **Fácil mantenimiento**: Cambios centralizados en un archivo
- **Debugging simplificado**: Un solo punto de falla con traceback detallado
- **Extensibilidad**: Agregar nuevos medios sin tocar código, solo configuración en press.ini
- **Documentación integrada**: Código bien documentado con docstrings y comentarios

### Para el Usuario
- **Comandos simples**: Una sola herramienta para todos los medios
- **Flexibilidad de fechas**: Argumentos de línea de comandos --fecha y --custom-fecha
- **Mejor feedback**: Mensajes de estado más claros con emojis y colores
- **Menos errores**: Validaciones automáticas y reintentos
- **Reinicio inteligente**: Continuar desde donde se quedó sin repetir trabajo

### Para las Operaciones
- **Consistencia**: Todos los medios siguen el mismo proceso unificado
- **Monitoreo centralizado**: Un solo log de operaciones con estado claro
- **Configuración uniforme**: Sin configuraciones hardcodeadas
- **Backup simplificado**: Solo dos archivos principales (press.ini y pressreader_downloader.py)
- **Organización clara**: Subcarpetas definidas (imagenes/, pdf_paginas_ocr/)

## Conclusión

El nuevo sistema de orquestación PressReader Downloader representa una mejora significativa en mantenibilidad, extensibilidad y usabilidad. Al centralizar la configuración en `press.ini` y unificar el código en un solo script de ~1976 líneas, se reduce drásticamente la complejidad del sistema mientras se mantienen (y mejoran) todas las funcionalidades originales.

### Mejoras Clave
- ✅ Sistema de descarga inteligente con reintentos y timeout
- ✅ OCR mejorado con método Nación y correcciones automáticas
- ✅ Reinicio inteligente para continuar descargas interrumpidas
- ✅ Organización de archivos con subcarpetas claras
- ✅ PDF de validación para verificar calidad OCR
- ✅ Subida al FTP con estructura organizada
- ✅ Validación robusta de PageKeys
- ✅ Manejo de errores con traceback detallado
- ✅ Procesamiento paralelo para mejoras de imagen

El sistema está listo para producción y puede escalarse fácilmente agregando nuevos medios simplemente añadiendo configuraciones en `press.ini` sin necesidad de modificar el código Python.
