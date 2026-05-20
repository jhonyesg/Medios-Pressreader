#!/usr/bin/env python3
"""
PressReader Downloader Orchestrator
Script base para descargar y procesar periódicos/revistas de PressReader
Permite orquestar múltiples medios usando configuración en press.ini

Uso:
    python pressreader_downloader.py [medio] [--fecha YYYY-MM-DD] [--custom-fecha YYYY-MM-DD]
    
Ejemplos:
    python pressreader_downloader.py Tiempo
    python pressreader_downloader.py Espectador --fecha 2024-06-01
    python pressreader_downloader.py Motor
"""

import argparse
import configparser
import ctypes
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from ftplib import FTP_TLS, error_perm
from getpass import getuser
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from pressreader_auth import BrowserAuthenticator, AuthenticationError

# Escalas candidatas para auto-descubrimiento, de mayor a menor calidad.
# El servidor devuelve la imagen al tamaño real si la escala pedida es mayor que el máximo disponible,
# así que probamos de arriba abajo y usamos la primera que descarga una imagen válida.
ESCALAS_CANDIDATAS = [
    400, 380, 360, 350, 340, 320, 300, 280, 260, 250, 240, 230, 220,
    215, 210, 208, 205, 201, 200, 195, 190, 185, 184, 181, 180, 176,
    174, 170, 165, 160, 155, 150, 140, 130, 120,
]
from PIL import Image, ImageFile, ImageOps
# Permitir cargar imágenes truncadas o incompletas (común en descargas interrumpidas)
ImageFile.LOAD_TRUNCATED_IMAGES = True
# Compatibilidad: algunas versiones de Pillow exponen LANCZOS en Image.Resampling,
# otras directamente en Image. Usar getattr evita referencias directas que confunden al linter.
try:
    RESAMPLE_LANCZOS = Image.Resampling.LANCZOS
except Exception:
    # Evitar referenciar atributos desconocidos directamente (p.ej. ANTIALIAS) para no generar
    # falsos positivos en Pylance. Se intenta LANCZOS, luego ANTIALIAS, luego BICUBIC.
    RESAMPLE_LANCZOS = getattr(
        Image,
        'LANCZOS',
        getattr(Image, 'ANTIALIAS', getattr(Image, 'BICUBIC', 3))
    )

import fitz  # PyMuPDF
import pytesseract
import cv2
import numpy as np
from natsort import natsorted


class PressReaderDownloader:
    def __init__(self, config_file='press.ini'):
        """Inicializar el descargador con configuración desde archivo INI"""
        self.config = configparser.ConfigParser()
        self.config.read(config_file)
        
        # Obtener configuración por defecto
        self.default_config = dict(self.config.items('DEFAULT'))
        self.ftp_config = dict(self.config.items('FTP'))
        
        # Variables de instancia para el medio actual
        self.medio_config = {}
        self.tokens = []
        self.tm = []
        # Control de refresco de token por browser auth (se resetea por cada medio)
        self._token_refreshed = False
        self._authenticator = None

        # Configuración de consola
        self._setup_console()
        
    def _setup_console(self):
        """Configurar la ventana de consola de Windows para UTF-8"""
        if os.name == 'nt':
            try:
                # Intentar establecer UTF-8 en la consola
                os.system('chcp 65001 > nul')
                # Reconfigurar stdout/stderr para usar utf-8 de forma más robusta
                try:
                    sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # type: ignore
                    sys.stderr.reconfigure(encoding='utf-8', errors='replace')  # type: ignore
                except (AttributeError, io.UnsupportedOperation):
                    pass  # Compatibilidad con versiones antiguas de Python
            except Exception:
                try:
                    # Fallback para versiones antiguas de Python
                    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
                    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
                except Exception:
                    pass
        try:
            console_handle = ctypes.windll.kernel32.GetConsoleWindow()
            if console_handle:
                window_width = 600
                window_height = 400
                ctypes.windll.user32.SetWindowPos(
                    console_handle, ctypes.c_int(0), ctypes.c_int(0), ctypes.c_int(0),
                    ctypes.c_int(window_width), ctypes.c_int(window_height), ctypes.c_uint(0x0002)
                )
        except Exception:
            pass  # Ignorar errores en sistemas que no son Windows
    
    def _setup_console_title(self, title):
        """Establecer título de la consola"""
        try:
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass
    
    def load_medio_config(self, medio_name):
        """Cargar configuración específica del medio"""
        if medio_name not in self.config.sections():
            raise ValueError(f"Medio '{medio_name}' no encontrado en configuración")
        
        self.medio_config = dict(self.config.items(medio_name))
        self.medio_name = medio_name

        # Resetear estado de refresco para el nuevo medio
        self._token_refreshed = False

        # Procesar tokens
        if self.medio_config.get('use_tokens', 'False').lower() == 'true':
            token_list = self.medio_config.get('tokens', '')
            self.tokens = [t.strip() for t in token_list.split(',') if t.strip()]
        else:
            self.tokens = [self.medio_config.get('token', '')]
        
        # Procesar escalas de calidad (tm)
        # auto_scale=True ignora tm/tm_range y usa ESCALAS_CANDIDATAS (alto→bajo) para que
        # cada página encuentre su propio tope de resolución de forma automática.
        if self.medio_config.get('auto_scale', 'False').lower() == 'true':
            self.tm = ESCALAS_CANDIDATAS
            self.auto_scale = True
        elif 'tm' in self.medio_config:
            tm_list = self.medio_config.get('tm', '')
            self.tm = [int(t.strip()) for t in tm_list.split(',') if t.strip().isdigit()]
            self.auto_scale = False
        elif all(key in self.medio_config for key in ['tm_range_inicial', 'tm_range_mas', 'tm_range_menos']):
            inicial = int(self.medio_config['tm_range_inicial'])
            mas = int(self.medio_config['tm_range_mas'])
            menos = int(self.medio_config['tm_range_menos'])
            self.tm = list(range(inicial - menos, inicial + mas + 1))
            self.auto_scale = False
        else:
            self.auto_scale = False

        print(f"Configuración cargada para medio: {medio_name}")
        print(f"Tokens disponibles: {len(self.tokens)}")
        if self.auto_scale:
            print(f"Escalas de calidad (tm): AUTO ({len(self.tm)} candidatas, {self.tm[0]}→{self.tm[-1]})")
        else:
            print(f"Escalas de calidad (tm): {self.tm}")
    
    def _validar_token_actual(self) -> bool:
        """
        Verifica si el token actual sigue funcionando contra la API de GetPageKeys.
        Prueba hoy y, si la edición aún no está publicada (madrugada), prueba ayer.
        Retorna True si el token es válido para cualquiera de las dos fechas.
        """
        if not self.tokens or not self.tokens[0]:
            return False
        token = self.tokens[0]
        libro = self.medio_config.get('libro', '')
        url_ids = [u.strip() for u in self.medio_config.get('url_ids', '').split(',') if u.strip()]
        url_id = url_ids[0] if url_ids else '00000000001001'
        from datetime import datetime, timedelta
        for delta in (0, -1):
            fecha = (datetime.now() + timedelta(days=delta)).strftime('%Y%m%d')
            issue = f"{libro}{fecha}{url_id}"
            try:
                if token.startswith('ey'):
                    url = (f'https://ingress.pressreader.com/services/IssueInfo/GetPageKeys'
                           f'?issue={issue}&pageNumber=0&preview=true')
                    r = requests.get(url, headers={
                        'Authorization': f'Bearer {token}',
                        'Referer': 'https://www.pressreader.com/',
                    }, timeout=10)
                else:
                    url = (f'https://ingress.pressreader.com/services/IssueInfo/GetPageKeys'
                           f'?accessToken={token}&issue={issue}&pageNumber=0&preview=true')
                    r = requests.get(url, timeout=10)
                if r.status_code == 200 and r.json().get('PageKeys'):
                    if delta < 0:
                        print(f"  ℹ️  Edición de hoy aún no disponible — token válido con edición de ayer ({fecha})")
                    return True
            except Exception:
                pass
        return False

    def _try_refresh_token(self) -> bool:
        """
        Obtiene un token fresco vía browser auth y lo guarda en press.ini.
        Máximo un refresco por sesión para evitar bucles.
        """
        if self._token_refreshed:
            print("⚠️  Ya se intentó refresco de token en esta sesión. No se reintentará.")
            return False

        print("🔄 Obteniendo token fresco vía autenticación por navegador...")
        try:
            if self._authenticator is None:
                self._authenticator = BrowserAuthenticator()
            new_token = self._authenticator.get_fresh_token()
            if not new_token:
                print("❌ El autenticador retornó un token vacío.")
                self._token_refreshed = True
                return False

            self.tokens = [new_token]
            self._token_refreshed = True
            print(f"✅ Token fresco obtenido ({len(new_token)} chars)")
            self._save_token_to_config(new_token)  # siempre guarda
            return True

        except (AuthenticationError, FileNotFoundError) as e:
            print(f"❌ Error durante refresco de token: {e}")
            self._token_refreshed = True
            return False
        except Exception as e:
            print(f"❌ Error inesperado durante refresco de token: {e}")
            self._token_refreshed = True
            return False

    def _save_token_to_config(self, token: str):
        """Escribe el token fresco de vuelta en press.ini para la sección del medio actual."""
        try:
            config = configparser.ConfigParser()
            config_path = Path(__file__).parent / 'press.ini'
            config.read(config_path)
            section = self.medio_name
            if section in config:
                if self.medio_config.get('use_tokens', 'False').lower() == 'true':
                    config[section]['tokens'] = token
                else:
                    config[section]['token'] = token
                with open(config_path, 'w') as f:
                    config.write(f)
                print(f"💾 Token guardado en press.ini [{section}]")
        except Exception as e:
            print(f"⚠️  No se pudo guardar token en press.ini: {e}")

    def setup_lock_file(self):
        """Configurar archivo de bloqueo para evitar ejecuciones concurrentes"""
        username = getuser()
        lock_file_pattern = self.medio_config.get('lock_file', f'{self.medio_name}.lock')
        
        # Reemplazar placeholders en el nombre del archivo de lock
        lock_file_name = lock_file_pattern.format(impreso=self.medio_config.get('impreso', self.medio_name))
        
        self.lock_file_path = os.path.join(
            self.default_config['rutabase'], 
            username, 
            self.default_config['base_lock'].lstrip('/'),
            lock_file_name
        )
        
        # Crear directorio si no existe
        os.makedirs(os.path.dirname(self.lock_file_path), exist_ok=True)
        
        # Verificar si ya existe
        if os.path.exists(self.lock_file_path):
            print(f"Script ya en ejecución. Archivo de lock: {self.lock_file_path}")
            sys.exit(1)
        
        # Crear archivo de lock
        open(self.lock_file_path, 'w').close()
        print(f"Archivo de lock creado: {self.lock_file_path}")
    
    def cleanup_lock_file(self):
        """Limpiar archivo de lock al finalizar"""
        try:
            if os.path.exists(self.lock_file_path):
                os.remove(self.lock_file_path)
                print("Archivo de lock eliminado")
        except Exception as e:
            print(f"Error al eliminar archivo de lock: {e}")
    
    def parse_date_args(self, args):
        """Procesar argumentos de fecha"""
        if args.fecha:
            # Usar fecha personalizada
            fecha_personalizada = datetime.strptime(args.fecha, '%Y-%m-%d')
            return fecha_personalizada
        elif args.custom_fecha:
            # Usar fecha específica personalizada
            custom_fecha = datetime.strptime(args.custom_fecha, '%Y-%m-%d')
            return custom_fecha
        else:
            # Usar fecha actual
            return datetime.now()
    
    def runcmd(self, cmd, verbose=False, timeout=None):
        """Ejecutar comando shell y devolver resultado.
        Soporta timeout: si el proceso no termina en `timeout` segundos se mata y
        se devuelve código 124 (similar a timeout de GNU).
        """
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=True
        )
        try:
            std_out, std_err = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except Exception:
                pass
            # Intentar recoger cualquier salida remanente
            try:
                std_out, std_err = process.communicate(timeout=5)
            except Exception:
                std_out, std_err = "", ""
            if verbose:
                print(f"⏰ Comando alcanzó timeout ({timeout}s) y fue terminado: {cmd}")
            return 124, std_out, std_err
        if verbose:
            if std_out:
                print(std_out.strip())
            if std_err:
                print(std_err.strip())
        return process.returncode, std_out, std_err
    
    def upload_images_to_ftp(self, ruta_directorio, ftp_path=None):
        """Subir imágenes al FTP dentro de subcarpeta imagenes/"""
        if not ftp_path:
            ftp_path = self.medio_config.get('ftp_path', '/')
        
        current_date = datetime.now()
        host = self.ftp_config['host']
        user = self.ftp_config['user']
        password = self.ftp_config['pass']
        
        try:
            ftp = FTP_TLS(host)
            ftp.login(user, password)
            ftp.prot_p()
            
            fecha_ftp = f'{ftp_path}/{current_date.strftime("%Y%m%d")}/'
            try:
                ftp.cwd(fecha_ftp)
            except error_perm:
                ftp.mkd(fecha_ftp)
                ftp.cwd(fecha_ftp)
            
            # CREAR SUBCARPETA IMAGENES EN FTP
            try:
                ftp.cwd('imagenes')
            except error_perm:
                ftp.mkd('imagenes')
                ftp.cwd('imagenes')
            
            for file in os.listdir(ruta_directorio):
                if file.endswith('.jpg'):
                    file_path = os.path.join(ruta_directorio, file)
                    with open(file_path, 'rb') as f:
                        ftp.storbinary(f'STOR {file}', f)
                    print(f"Imagen {file} subida a imagenes/ con éxito")
            
            # Volver a raíz del FTP
            ftp.cwd('/')
        
        except error_perm as e:
            print(f"Error FTP: {e}")
        finally:
            try:
                ftp.quit()
            except:
                pass
    
    def upload_pdf_to_ftp(self, output, ftp_path=None):
        """Subir PDF al FTP"""
        if not ftp_path:
            ftp_path = self.medio_config.get('ftp_path', '/')
        
        host = self.ftp_config['host']
        user = self.ftp_config['user']
        password = self.ftp_config['pass']
        
        try:
            ftp = FTP_TLS(host)
            ftp.login(user, password)
            ftp.prot_p()
            
            ftp.cwd(ftp_path)
            
            with open(output, 'rb') as f:
                ftp.storbinary(f'STOR {os.path.basename(output)}', f)
            print(f"PDF {os.path.basename(output)} subido con éxito")
        
        except error_perm as e:
            print(f"Error FTP: {e}")
        finally:
            try:
                ftp.quit()
            except:
                pass
    
    def upload_pdfs_to_ftp(self, ruta_directorio, ftp_path=None):
        """Subir todos los PDFs de un directorio al FTP dentro de subcarpeta pdf_paginas_ocr/"""
        if not ftp_path:
            ftp_path = self.medio_config.get('ftp_path', '/')
        
        current_date = datetime.now()
        host = self.ftp_config['host']
        user = self.ftp_config['user']
        password = self.ftp_config['pass']
        
        try:
            ftp = FTP_TLS(host)
            ftp.login(user, password)
            ftp.prot_p()
            
            # Ir a la carpeta de fecha
            fecha_ftp = f'{ftp_path}/{current_date.strftime("%Y%m%d")}/'
            try:
                ftp.cwd(fecha_ftp)
            except error_perm:
                ftp.mkd(fecha_ftp)
                ftp.cwd(fecha_ftp)
            
            # CREAR SUBCARPETA PDF_PAGINAS_OCR EN FTP
            try:
                ftp.cwd('pdf_paginas_ocr')
            except error_perm:
                ftp.mkd('pdf_paginas_ocr')
                ftp.cwd('pdf_paginas_ocr')
            
            for file in os.listdir(ruta_directorio):
                if file.endswith('.pdf'):
                    file_path = os.path.join(ruta_directorio, file)
                    with open(file_path, 'rb') as f:
                        ftp.storbinary(f'STOR {file}', f)
                    print(f"  ✅ PDF {file} subido a pdf_paginas_ocr/ con éxito")
            
            # Volver a raíz del FTP
            ftp.cwd('/')
        
        except error_perm as e:
            print(f"❌ Error FTP: {e}")
        finally:
            try:
                ftp.quit()
            except:
                pass
    
    def verificar_cantidad_imagenes(self, rutafin, max_page_number):
        """Verificar que la cantidad de imágenes coincide con las páginas esperadas"""
        # SIEMPRE buscar en subcarpeta imagenes/
        ruta_imagenes = os.path.join(rutafin, "imagenes")
        
        if not os.path.isdir(ruta_imagenes):
            print(f"❌ ERROR: No existe la carpeta imagenes/ en {rutafin}")
            return False
        
        cantidad_imagenes = len([item for item in os.listdir(ruta_imagenes) 
                                if item.endswith('.jpg')])
        
        print(f"📊 Verificación: {cantidad_imagenes} imágenes encontradas, se esperaban {max_page_number}")
        
        if cantidad_imagenes == max_page_number:
            print("✅ Las páginas del JSON y las que están en la carpeta son correctas.")
            return True
        else:
            print(f"❌ Discrepancia: {cantidad_imagenes} encontradas, se esperaban {max_page_number}.")
            return False
    
    def is_image_valid(self, path):
        """Verificar si un JPG es válido y no está truncado (usa PIL)."""
        try:
            if not os.path.exists(path) or os.path.getsize(path) < 1024:
                return False
            with Image.open(path) as im:
                im.load()
            return True
        except Exception:
            return False
    
    def calidades(self, inicial, final, rutafin, tm=None, cad03='&ticket=', pag='01', max_reintentos=3):
        """
        Descargar imagen probando diferentes escalas usando wget con timeout y reintentos.
        Devuelve True si se obtiene un archivo válido (>0 bytes).
        """
        if tm is None:
            tm = self.tm
        
        # Crear directorio principal y subcarpeta imagenes/
        os.makedirs(rutafin, exist_ok=True)
        ruta_imagenes = os.path.join(rutafin, "imagenes")
        os.makedirs(ruta_imagenes, exist_ok=True)
        
        user_agent = self.default_config.get('user_agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3')
        
        # SIEMPRE guardar en subcarpeta imagenes/
        file_path = os.path.join(ruta_imagenes, f"{pag}.jpg")
        print(f"  📁 Ruta de descarga: {file_path}")
        
        # Verificar si el archivo ya existe y es válido
        if os.path.isfile(file_path) and os.stat(file_path).st_size > 0:
            print(f"✅ Archivo ya existe: {file_path} ({os.stat(file_path).st_size} bytes) - Saltando descarga")
            return True
        
        success = False
        
        for escala in tm:
            calidad = str(escala)
            # URL encoding del ticket para caracteres especiales como / y =
            linkp = inicial + cad03 + quote(final, safe='') + '&scale=' + calidad
            
            # Configurar wget con timeout y opciones mejoradas
            timeout_seconds = 30  # Watchdog: máximo 30 segundos por intento
            # Mantener --timeout en wget pero también usar timeout en runcmd para matar procesos colgados
            wget_cmd = f'wget --no-check-certificate --timeout={timeout_seconds} --tries=1 --header="User-Agent: {user_agent}" -O "{file_path}" "{linkp}"'
            
            print(f"Intentando descarga con wget (scale={calidad}, timeout_watchdog={timeout_seconds}s): {linkp}")
            
            # Intentar descarga con reintentos
            for intento in range(1, max_reintentos + 1):
                print(f"  Intento {intento}/{max_reintentos}...")
                
                # Limpiar archivo parcial antes del intento
                try:
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                except Exception:
                    pass
                
                # Ejecutar comando con watchdog de timeout_seconds
                rc, out, err = self.runcmd(wget_cmd, verbose=False, timeout=timeout_seconds)
                
                # Verificar si la descarga fue exitosa
                if os.path.isfile(file_path) and os.stat(file_path).st_size > 0:
                    file_size = os.stat(file_path).st_size
                    print(f"  ✅ Descarga exitosa: {file_path} ({file_size} bytes)")
                    success = True
                    break
                else:
                    # Analizar el error o timeout provocado por watchdog
                    combined = ("".join(filter(None, [out, err])) or "").lower()
                    if rc == 124 or "timeout" in combined or "timed out" in combined:
                        print(f"  ⏰ Timeout/watchdog en intento {intento} - posible proceso colgado. Eliminando parcial y reintentando.")
                    elif "connection" in combined:
                        print(f"  🔌 Error de conexión en intento {intento}")
                    else:
                        print(f"  ❌ Fallo en intento {intento} (rc={rc})")
                    
                    # Si hay más reintentos, esperar antes de reintentar
                    if intento < max_reintentos:
                        print(f"  🔄 Esperando 3 segundos antes del siguiente intento...")
                        time.sleep(3)
                    
                    # Limpiar archivo parcial fallido
                    try:
                        if os.path.isfile(file_path):
                            os.remove(file_path)
                            print(f"  🗑️ Archivo parcial eliminado: {file_path}")
                    except Exception:
                        pass
            
            if success:
                break
            
            print(f"  💥 Todas las intentos fallaron para scale={calidad}. Probando siguiente escala...")
        
        if not success:
            print(f"❌ Todas las escalas y reintentos fallaron para la página {pag} (ruta: {file_path}).")
        
        return success

    def validar_pagekeys_completos(self, datoskey, medio_name):
        """Validación robusta de PageKeys para todos los medios"""
        # Validar que existe PageKeys
        if "PageKeys" not in datoskey:
            print(f"❌ ERROR: No se encontró 'PageKeys' en la respuesta del servidor")
            print(f"Medio: {medio_name}")
            return False, 0
        
        page_keys = datoskey["PageKeys"]
        
        # Validar que la lista no está vacía
        if not page_keys:
            print(f"❌ ERROR: La lista de PageKeys está vacía para {medio_name}")
            return False, 0
        
        # Validar que hay más de 1 PageKey (como validación mínima)
        if len(page_keys) <= 1:
            print(f"❌ ERROR: Solo se encontraron {len(page_keys)} PageKey(s) para {medio_name}")
            print("Esto indica que el token puede estar expirado o la fecha no tiene contenido.")
            return False, len(page_keys)
        
        # Validar estructura de cada PageKey
        valid_keys = 0
        for i, page_key in enumerate(page_keys):
            if not isinstance(page_key, dict):
                print(f"❌ PageKey inválida en índice {i}: no es un diccionario")
                return False, 0
            
            if 'PageNumber' not in page_key or 'Key' not in page_key:
                print(f"❌ PageKey inválida en índice {i}: faltan 'PageNumber' o 'Key'")
                print(f"Contenido: {page_key}")
                return False, 0
            
            valid_keys += 1
        
        if valid_keys != len(page_keys):
            print(f"❌ ERROR: Solo {valid_keys} de {len(page_keys)} PageKeys son válidas")
            return False, valid_keys
        
        print(f"✅ PageKeys válidas para {medio_name}: {valid_keys} páginas")
        return True, valid_keys
    
    def verificar_progreso_descarga(self, rutafin, page_count):
        """Verificar qué páginas ya están descargadas para permitir reinicio.
        - Elimina imágenes con número mayor al esperado (archivos residuales).
        - Si la carpeta contiene una secuencia consecutiva desde 01 hasta N y N < page_count,
          se asume que la última (N) pudo quedar truncada por un cierre forzado: se elimina N.jpg
          para forzar la re-descarga desde esa página.
        - Elimina imágenes inválidas (truncadas) detectadas por is_image_valid().
        """
        descargadas = []
        faltantes = []
        
        # SIEMPRE trabajar en subcarpeta imagenes/
        ruta_imagenes = os.path.join(rutafin, "imagenes")
        os.makedirs(ruta_imagenes, exist_ok=True)

        # Detectar archivos JPG presentes en imagenes/
        try:
            jpg_files = [f for f in os.listdir(ruta_imagenes) if f.lower().endswith('.jpg')]
        except FileNotFoundError:
            jpg_files = []

        # Eliminar archivos cuyo número de página sea mayor al esperado (archivos residuales)
        extras = []
        numeric_files = []
        for fname in jpg_files:
            try:
                num = int(fname[:-4])
                numeric_files.append(num)
                if num > page_count:
                    extras.append(fname)
            except Exception:
                # Si el nombre no sigue el patrón NN.jpg, ignorar
                continue

        if extras:
            print(f"⚠️ Se encontraron {len(extras)} imágenes adicionales fuera del rango esperable. Eliminando: {extras}")
            for ex in extras:
                try:
                    os.remove(os.path.join(ruta_imagenes, ex))
                    print(f"  🗑️ Eliminada imagen extra: {ex}")
                except Exception:
                    pass

        # Releer lista de JPG después de limpiar extras
        try:
            jpg_files = [f for f in os.listdir(ruta_imagenes) if f.lower().endswith('.jpg')]
        except FileNotFoundError:
            jpg_files = []

        # Analizar números presentes y detectar secuencia 1..N
        nums = sorted([n for n in numeric_files if isinstance(n, int)]) if numeric_files else []
        if nums:
            min_n, max_n = nums[0], nums[-1]
            # Si hay una secuencia completa 1..N (sin huecos) y N < page_count,
            # probablemente el proceso quedó pegado al intentar descargar la página N+1.
            # En ese caso eliminamos N.jpg para forzar reintento desde N.
            if min_n == 1 and max_n == len(nums) and len(nums) < page_count:
                last_fname = f"{max_n:02d}.jpg"
                last_path = os.path.join(ruta_imagenes, last_fname)
                if os.path.isfile(last_path):
                    try:
                        os.remove(last_path)
                        print(f"⚠️ Se detectó secuencia 01..{max_n} incompleta respecto a {page_count}.")
                        print(f"  🗑️ Eliminada la última imagen potencialmente truncada: {last_fname}")
                    except Exception:
                        pass
                    # Asegurar que el número eliminado quede como faltante
                    # (no seguir considerandola como descargada)
                    if max_n in nums:
                        try:
                            nums.remove(max_n)
                        except ValueError:
                            pass

        # Verificar cada página esperada y validar imagen
        for pag_num in range(1, page_count + 1):
            pag = f"{pag_num:02d}"
            file_path = os.path.join(ruta_imagenes, f"{pag}.jpg")

            if os.path.isfile(file_path) and os.stat(file_path).st_size > 0 and self.is_image_valid(file_path):
                descargadas.append(pag_num)
            else:
                # Si existe pero está inválida, eliminar para forzar reintento
                if os.path.isfile(file_path):
                    try:
                        os.remove(file_path)
                        print(f"  ⚠️ Imagen inválida/eliminada: {file_path}")
                    except Exception:
                        pass
                faltantes.append(pag_num)

        print(f"📊 Estado de descarga: {len(descargadas)}/{page_count} páginas completadas")
        if faltantes:
            print(f"🔄 Páginas pendientes: {faltantes[:10]}{'...' if len(faltantes) > 10 else ''}")

        return descargadas, faltantes

    def intentar_descargar_imagenes(self, token, url_base, libro, year, mes, dia,
                                   current_url_id, cad01, current_cad02, cad03, rutafin, tm=None):
        """Intentar descargar todas las imágenes usando un token específico con reinicio inteligente"""
        if tm is None:
            tm = self.tm
        
        try:
            # JWT Bearer: token empieza con 'ey' (base64 de un JWT)
            # Se usa Authorization: Bearer en header en lugar de accessToken en URL
            if token.startswith('ey'):
                url = (f'https://ingress.pressreader.com/services/IssueInfo/GetPageKeys'
                       f'?issue={libro}{year}{mes}{dia}{current_url_id}&pageNumber=0&preview=true')
                req_headers = {
                    'Authorization': f'Bearer {token}',
                    'Referer': 'https://www.pressreader.com/',
                    'User-Agent': self.default_config.get('user_agent', ''),
                }
                print(f"Obteniendo PageKeys (Bearer JWT) desde: {url[:80]}...")
                response = requests.get(url, headers=req_headers)
            else:
                url = url_base + token + '&issue=' + libro + year + mes + dia + current_url_id + '&pageNumber=0&preview=true'
                print(f"Obteniendo PageKeys desde: {url}")
                response = requests.get(url)
            response.raise_for_status()
            lista = response.text
            datoskey = json.loads(lista)
            
            print(f"Token: {token}")
            
            # Aplicar validación robusta de PageKeys
            is_valid, page_count = self.validar_pagekeys_completos(datoskey, self.medio_name)
            
            if not is_valid:
                return False
            
            # Verificar progreso previo y permitir reinicio
            descargadas, faltantes = self.verificar_progreso_descarga(rutafin, page_count)
            
            if not faltantes:
                print(f"✅ Todas las páginas ya están descargadas correctamente")
                return self.verificar_cantidad_imagenes(rutafin, page_count)
            
            # Mostrar detalles de las páginas (solo primeras 5)
            print(f"📄 Número de páginas esperadas: {page_count}")
            for i, claves in enumerate(datoskey["PageKeys"][:5]):  # Solo mostrar primeras 5
                key = str(claves['Key'])
                pags = claves['PageNumber']
                print(f"  Página {pags}: Key={key[:20]}...")
            if page_count > 5:
                print(f"  ... y {page_count - 5} páginas más")
            
            if descargadas:
                print(f"🔄 Reanudando desde página {min(faltantes)} (ya descargadas: {len(descargadas)})")
            else:
                print(f"✅ Iniciando descarga completa de {page_count} páginas...")

            # En modo auto_scale cada página busca su propio tope de resolución:
            # tm ya contiene ESCALAS_CANDIDATAS (alto→bajo) y usamos max_reintentos=1
            # porque no tiene sentido reintentar una escala que el servidor rechazó.
            reintentos = 1 if self.auto_scale else 3

            # Descargar solo las páginas faltantes
            successful_downloads = len(descargadas)  # Contar las ya descargadas
            for claves in datoskey["PageKeys"]:
                key = str(claves['Key'])
                pags = claves['PageNumber']
                pag = f"{pags:02d}"

                # Saltar páginas ya descargadas
                if pags in descargadas:
                    continue

                image_url = cad01 + year + mes + dia + current_cad02 + '&page=' + pag

                print(f"📥 Descargando página {pag}...")
                if not self.calidades(image_url, key, rutafin, tm, cad03, pag, max_reintentos=reintentos):
                    print(f"❌ Fallo en descarga de imagen {pag}")
                    print(f"💡 Para reiniciar desde este punto, ejecuta el script nuevamente")
                    return False
                successful_downloads += 1
            
            # Verificar que se descargaron todas las páginas
            if successful_downloads != page_count:
                print(f"❌ ERROR: Solo se descargaron {successful_downloads} de {page_count} páginas")
                return False
            
            print(f"✅ Todas las {page_count} páginas descargadas exitosamente")
            return self.verificar_cantidad_imagenes(rutafin, page_count)
                
        except KeyboardInterrupt:
            print(f"\n⚠️ Descarga interrumpida por el usuario")
            print(f"💡 Para continuar desde donde se quedó, ejecuta el script nuevamente")
            return False
        except requests.exceptions.RequestException as e:
            print(f"❌ Error de red obteniendo PageKeys: {e}")
            return False
        except json.JSONDecodeError as e:
            print(f"❌ Error decodificando JSON de PageKeys: {e}")
            print(f"Respuesta del servidor: {lista[:500]}...")
            return False
        except Exception as e:
            print(f"❌ Error con token {token}: {e}")
            return False
    
    def descargar_con_tokens(self, url_base, libro, year, mes, dia, posibles_url_ids, 
                           cad01, cad03, rutafin, tm=None):
        """Descargar imágenes probando múltiples tokens y URL IDs"""
        if tm is None:
            tm = self.tm
        
        for url_id in posibles_url_ids:
            print(f"Intentando con URL ID: {url_id}")
            for token in self.tokens:
                if self.intentar_descargar_imagenes(token, url_base, libro, year, mes, dia, 
                                                  url_id, cad01, url_id, cad03, rutafin, tm):
                    print(f"Descarga exitosa con URL ID: {url_id} y Token: {token}")
                    return True
                else:
                    print(f"Token {token} falló para URL ID {url_id}")
        return False
    
    def cargar_correcciones(self, archivo_json):
        """Cargar correcciones para OCR desde archivo JSON"""
        try:
            with open(archivo_json, 'r', encoding='utf-8') as file:
                correcciones = json.load(file)
                correcciones_compiled = {
                    re.compile(r'\b' + k + r'\b'): v for k, v in correcciones.items()
                    if not any(c in k for c in ('\\', '[', '.', '+', '*', '?', '^', '$', '(', ')', '|', '{', '}'))
                }
                return correcciones_compiled
        except Exception as e:
            print(f"Error cargando correcciones: {e}")
            return {}
    
    def corregir_texto(self, texto, correcciones):
        """Aplicar correcciones al texto OCR"""
        for pattern, correcto in correcciones.items():
            texto = pattern.sub(correcto, texto)
        # Corregir espacios entre minúsculas y mayúsculas
        texto = re.sub(r'(?<=[a-záéíóúñ])(?=[A-ZÁÉÍÓÚÑ])', ' ', texto)
        return texto
    
    def get_scale_factor(self, height):
        """Factor de escala para OCR basado en altura del texto"""
        if height < 20:
            return 1.12
        elif height < 30:
            return 1.05
        else:
            return 1.02
    
    def adjust_font_size(self, font_size):
        """Ajustar tamaño de fuente para OCR"""
        if font_size < 10:
            return 15
        elif font_size > 40:
            return 50
        else:
            return font_size
    
    def crear_pdf_con_texto_superpuesto(self, imagen_path, salida_pdf_path,
                                       y_adjustment=10, font_scale=0.45):
        """Crear PDF con texto superpuesto invisible usando OCR (método Nación)"""
        # Rastrear si se creó archivo temporal para limpieza al final
        temp_file_created = None
        
        try:
            # Cargar imagen original con PIL
            img_pil = Image.open(imagen_path)
            
            # Verificar formato de imagen y convertir si es necesario
            try:
                if img_pil.format not in ['JPEG', 'PNG', 'TIFF']:
                    # Convertir formato no soportado
                    temp_path = imagen_path + "_temp.jpg"
                    img_pil.convert('RGB').save(temp_path, format='JPEG')
                    imagen_path = temp_path
                    temp_file_created = temp_path  # Guardar referencia para eliminar al final
                    img_pil = Image.open(imagen_path)
            except Exception as e:
                print(f"⚠️ Error validando formato de imagen {imagen_path}: {e}")
                return
            
            print(f"  🖼️ Procesando imagen: {os.path.basename(imagen_path)}")
            print(f"  📐 Dimensiones: {img_pil.size}")
            
            # ✅ MÉTODO NACIÓN: Procesar imagen con PIL (sin reducción)
            # Convertir a escala de grises
            if img_pil.mode != 'L':
                img_ocr = img_pil.convert('L')
            else:
                img_ocr = img_pil
            
            # Aplicar autocontrast (como en Nación) - menos agresivo que threshold
            from PIL import ImageOps
            img_ocr = ImageOps.autocontrast(img_ocr, cutoff=5)
            
            # Convertir a array numpy para OCR
            import numpy as np
            gray = np.array(img_ocr)
            
            # ✅ MÉTODO NACIÓN: Aplicar OCR sin parámetros extra (usa defaults optimizados)
            print(f"  🔍 Iniciando OCR (método Nación - sin reducción)...")
            try:
                ocr_data = pytesseract.image_to_data(
                    gray, lang='spa', output_type=pytesseract.Output.DICT)
            except Exception as ocr_error:
                print(f"⚠️ Error en OCR para {imagen_path}: {ocr_error}")
                # Crear PDF básico sin OCR como fallback
                doc: Any = fitz.open()
                page = doc.new_page(width=img_pil.width, height=img_pil.height)
                page.insert_image(fitz.Rect(0, 0, img_pil.width, img_pil.height), filename=imagen_path)
                doc.save(salida_pdf_path)
                doc.close()
                print(f'  ⚠️ PDF básico creado (sin OCR): {salida_pdf_path}')
                return
            
            print(f"  ✅ OCR completado - {len([t for t in ocr_data['text'] if t.strip()])} bloques de texto detectados")
            
            # Crear PDF con imagen de fondo (usa la imagen ORIGINAL)
            doc: Any = fitz.open()
            page = doc.new_page(width=img_pil.width, height=img_pil.height)
            page.insert_image(fitz.Rect(0, 0, img_pil.width, img_pil.height), filename=imagen_path)
            
            # Añadir texto superpuesto
            for i in range(len(ocr_data['text'])):
                if int(ocr_data['conf'][i]) > 0:
                    left = ocr_data['left'][i]
                    top = ocr_data['top'][i]
                    width = ocr_data['width'][i]
                    height = ocr_data['height'][i]
                    text = ocr_data['text'][i].strip()
                    
                    if text:
                        # Cargar correcciones si existe el archivo
                        correcciones = {}
                        if 'correcciones_json' in self.medio_config:
                            correcciones = self.cargar_correcciones(
                                os.path.join(self.default_config['rutabase'], getuser(), 
                                           self.medio_config['correcciones_json'])
                            )
                        
                        text = self.corregir_texto(text, correcciones)
                        
                        # Calcular tamaño de fuente
                        scale_factor = self.get_scale_factor(height)
                        font_size = height * font_scale * scale_factor
                        fontsize = self.adjust_font_size(font_size)
                        
                        # ✅ MÉTODO NACIÓN: Sin reescalado de coordenadas (no se reduce la imagen)
                        x1 = int(left)
                        y1 = int(top)
                        w = int(width)
                        h = int(height)
                        
                        y1_corrected = y1 + y_adjustment
                        bbox = fitz.Rect(x1, y1_corrected, x1 + w, y1_corrected + h)
                        try:
                            page.insert_text(bbox.tl, text, fontsize=fontsize, 
                                           color=(0, 0, 0), fill_opacity=0, stroke_opacity=0)
                        except Exception:
                            try:
                                page.insert_text((x1, y1_corrected), text, fontsize=fontsize, 
                                               color=(0, 0, 0), fill_opacity=0, stroke_opacity=0)
                            except Exception:
                                continue
            
            doc.save(salida_pdf_path)
            doc.close()
            print(f'  ✅ PDF con OCR creado: {salida_pdf_path}')
            
        except Exception as e:
            print(f'  ❌ Error creando PDF con OCR: {e}')
        
        finally:
            # 🧹 LIMPIEZA: Eliminar archivo temporal si se creó
            if temp_file_created and os.path.exists(temp_file_created):
                try:
                    os.remove(temp_file_created)
                    print(f'  🗑️ Archivo temporal eliminado: {os.path.basename(temp_file_created)}')
                except Exception:
                    pass
    
    def crear_pdf_texto_desde_imagenes(self, folder_path, output_pdf_path,
                                     y_adjustment=10, font_scale=0.45):
        """
        Crea un PDF completo SOLO con el texto extraído de las imágenes (sin las imágenes)
        Todas las páginas se unen en un solo PDF con fondo blanco y texto visible.
        Útil para validar fácilmente la calidad del OCR
        
        Basado en el método de Nación para validación de OCR
        
        Args:
            folder_path: Ruta de la carpeta con imágenes de entrada
            output_pdf_path: Ruta del PDF de texto completo a crear (todas las páginas juntas)
            y_adjustment: Ajuste vertical del texto (por defecto 10)
            font_scale: Escala del tamaño de fuente (por defecto 0.45)
        """
        # Obtener la lista de archivos jpg en la carpeta
        jpg_files = [f for f in os.listdir(folder_path) if f.endswith('.jpg')]
        
        # Ordenar los archivos en orden alfanumérico natural
        jpg_files = natsorted(jpg_files)
        
        # Cargar correcciones si están configuradas
        correcciones = {}
        if 'correcciones_json' in self.medio_config:
            correcciones = self.cargar_correcciones(
                os.path.join(self.default_config['rutabase'], getuser(),
                           self.medio_config['correcciones_json'])
            )
        
        print(f"📝 CREANDO PDF COMPLETO DE TEXTO (sin imagen) para validación de OCR...")
        print(f"📂 Entrada: {folder_path}")
        print(f"📄 Salida: {output_pdf_path}")
        print(f"📄 Total imágenes: {len(jpg_files)}\n")
        print(f"{'─'*70}")
        
        # Crear UN SOLO documento PDF para todas las páginas
        doc: Any = fitz.open()
        
        # Procesar cada archivo jpg y agregar como página con texto
        total = len(jpg_files)
        texto_total_agregado = 0
        for i, file in enumerate(jpg_files, 1):
            input_image_path = os.path.join(folder_path, file)
            
            print(f"  [{i}/{total}] Procesando: {file}")
            
            # Rastrear archivo temporal creado para limpieza
            temp_file_created = None
            
            try:
                # Cargar imagen original
                img_pil = Image.open(input_image_path)
                
                # Convertir a escala de grises y aplicar autocontrast
                if img_pil.mode != 'L':
                    img_ocr = img_pil.convert('L')
                else:
                    img_ocr = img_pil
                
                img_ocr = ImageOps.autocontrast(img_ocr, cutoff=5)
                
                # Convertir a array numpy para OCR
                import numpy as np
                gray = np.array(img_ocr)
                
                # Aplicar OCR
                ocr_data = pytesseract.image_to_data(
                    gray, lang='spa', output_type=pytesseract.Output.DICT)
                
                # Crear una nueva página en blanco en el documento PDF
                page = doc.new_page(width=img_pil.width, height=img_pil.height)
                # No insertar imagen - página en blanco con texto visible
                
                # Añadir texto reconocido (VISIBLE - fill_opacity=1)
                texto_pagina = 0
                for j in range(len(ocr_data['text'])):
                    if int(ocr_data['conf'][j]) > 0:
                        left = ocr_data['left'][j]
                        top = ocr_data['top'][j]
                        width = ocr_data['width'][j]
                        height = ocr_data['height'][j]
                        text = ocr_data['text'][j].strip()
                        
                        if text:
                            # Aplicar correcciones
                            if correcciones:
                                text = self.corregir_texto(text, correcciones)
                            
                            # Calcular tamaño de fuente
                            scale_factor = self.get_scale_factor(height)
                            font_size = height * font_scale * scale_factor
                            fontsize = self.adjust_font_size(font_size)
                            
                            y1_corrected = top + y_adjustment
                            bbox = fitz.Rect(left, y1_corrected, left + width, y1_corrected + height)
                            
                            try:
                                # Texto VISIBLE (fill_opacity=1, stroke_opacity=0)
                                page.insert_text(bbox.tl, text, fontsize=fontsize, 
                                               color=(0, 0, 0), fill_opacity=1, stroke_opacity=0)
                                texto_pagina += 1
                            except Exception:
                                try:
                                    page.insert_text((left, y1_corrected), text, fontsize=fontsize,
                                                   color=(0, 0, 0), fill_opacity=1, stroke_opacity=0)
                                    texto_pagina += 1
                                except Exception:
                                    continue
                
                texto_total_agregado += texto_pagina
                print(f"      ✅ Página {i}: {texto_pagina} bloques de texto agregados")
                
            except Exception as e:
                print(f"      ❌ Error procesando {file}: {e}")
            
            finally:
                # 🧹 LIMPIEZA: Eliminar archivo temporal si se creó
                if temp_file_created and os.path.exists(temp_file_created):
                    try:
                        os.remove(temp_file_created)
                        print(f"      🗑️ Archivo temporal eliminado: {os.path.basename(temp_file_created)}")
                    except Exception:
                        pass
        
        # Guardar el PDF de texto COMPLETO con todas las páginas
        doc.save(output_pdf_path)
        doc.close()
        
        print(f"{'─'*70}")
        print(f"✅ PDF completo de texto creado: {output_pdf_path}")
        print(f"📊 Total bloques de texto agregados: {texto_total_agregado}")
        print(f"💡 Abre este archivo para validar fácilmente la calidad del OCR\n")
    
    def crear_pdf_con_texto_superpuesto_para_carpeta(self, folder_path, output_folder,
                                              y_adjustment=10, font_scale=0.45):
        """
        Crea PDFs con OCR para todas las imágenes de una carpeta.
        
        Basado en la lógica de Calameo para procesar masivamente imágenes.
        Cada imagen se procesa individualmente para crear un PDF con texto OCR superpuesto.
        
        Args:
            folder_path: Ruta de la carpeta con imágenes de entrada
            output_folder: Ruta de la carpeta para guardar los PDFs con OCR
            y_adjustment: Ajuste vertical del texto (por defecto 10)
            font_scale: Escala del tamaño de fuente (por defecto 0.45)
        """
        # Crear carpeta de salida si no existe
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)
        
        # Obtener la lista de archivos jpg en la carpeta
        jpg_files = [f for f in os.listdir(folder_path) if f.endswith('.jpg')]
        
        # Ordenar los archivos en orden alfanumérico natural
        jpg_files = natsorted(jpg_files)
        
        # Cargar correcciones si están configuradas
        correcciones = {}
        if 'correcciones_json' in self.medio_config:
            correcciones = self.cargar_correcciones(
                os.path.join(self.default_config['rutabase'], getuser(),
                           self.medio_config['correcciones_json'])
            )
        
        # Procesar cada archivo jpg y crear el PDF correspondiente
        total = len(jpg_files)
        for i, file in enumerate(jpg_files, 1):
            input_image_path = os.path.join(folder_path, file)
            output_pdf_path = os.path.join(output_folder, f"{i:02d}.pdf")
            print(f"[PDF_CON_OCR] Procesando imagen {i}/{total}: {input_image_path}")
            
            self.crear_pdf_con_texto_superpuesto(
                input_image_path, output_pdf_path, y_adjustment, font_scale)
            
            # Verificar si el PDF se creó exitosamente
            if os.path.exists(output_pdf_path) and os.path.getsize(output_pdf_path) > 0:
                print(f"[PDF_CON_OCR]  ✅ PDF creado ({i}/{total}): {output_pdf_path}")
            else:
                print(f"[PDF_CON_OCR]  ❌ Fallo al crear PDF para {input_image_path}")
        
    def get_available_media(self):
        """Obtener lista de todos los medios disponibles en la configuración"""
        media_sections = []
        for section in self.config.sections():
            if section not in ['DEFAULT', 'FTP']:
                media_sections.append(section)
        return sorted(media_sections)
    
    def process_single_medium(self, medio_name, args):
        """Procesar un solo medio específico"""
        print(f"\n{'='*70}")
        print(f"{'='*70}")
        print(f"🚀 INICIANDO PROCESAMIENTO DEL MEDIO: {medio_name}")
        print(f"{'='*70}")
        print(f"{'='*70}\n")
        
        # Configurar título de consola
        self._setup_console_title(f"PressReader Downloader - {medio_name}")
        
        # Cargar configuración del medio
        print(f"📋 PASO 1/10: Cargando configuración del medio {medio_name}...")
        self.load_medio_config(medio_name)
        print(f"✅ Configuración cargada exitosamente\n")
        
        # Configurar archivo de lock
        print(f"🔒 PASO 2/10: Configurando archivo de lock para evitar ejecuciones concurrentes...")
        self.setup_lock_file()
        print(f"✅ Lock file configurado\n")
        
        try:
            # Procesar fecha
            print(f"📅 PASO 3/10: Procesando fecha...")
            now = self.parse_date_args(args)
            year = format(now.year)
            mes = f"{now.month:02d}"
            dia = f"{now.day:02d}"
            
            print(f"✅ Fecha seleccionada: {year}-{mes}-{dia}\n")
            
            # Configurar rutas
            print(f"💾 PASO 4/10: Configurando rutas de trabajo...")
            username = getuser()
            rutabase = self.default_config['rutabase']
            base02 = self.medio_config.get('base02', f'/Downloads/Diarios_local/{medio_name}/')
            
            # Ajustar base02 para usuario actual
            if base02.startswith('/'):
                base02 = base02[1:]  # Quitar slash inicial
            base02 = base02.format(username=username)
            
            rutafin = os.path.join(rutabase, username, base02, f"{year}{mes}{dia}")
            output = os.path.join(rutabase, username, base02,
                                f"{self.medio_config['impreso']}_{year}{mes}{dia}.pdf")
            
            print(f"📁 Ruta de trabajo: {rutafin}")
            print(f"📄 Archivo de salida: {output}")
            
            # Crear directorios principales
            os.makedirs(rutafin, exist_ok=True)
            os.makedirs(os.path.dirname(output), exist_ok=True)
            print(f"✅ Directorios creados\n")
            
            # 📁 CREAR SUBCARPETAS DESDE EL INICIO (antes de descargar)
            # Esto evita duplicación y organiza desde el principio
            ruta_imagenes = os.path.join(rutafin, "imagenes")
            ruta_paginas_ocr = os.path.join(rutafin, "pdf_paginas_ocr")
            os.makedirs(ruta_imagenes, exist_ok=True)
            os.makedirs(ruta_paginas_ocr, exist_ok=True)
            
            print(f"📁 PASO 5/10: Creando subcarpetas de trabajo...")
            print(f"✅ Carpetas creadas: imagenes/, pdf_paginas_ocr/\n")
            
            # Verificar si el PDF ya existe
            print(f"🔍 PASO 6/10: Verificando si el PDF ya existe...")
            if os.path.exists(output):
                file_size = os.path.getsize(output) / 1024 / 1024
                print(f"✅ PDF final ya existe: {output} ({file_size:.1f} MB)")
                print("⏭️  Saltando proceso de descarga\n")
                return True  # Consideramos éxito si ya existe
            
            print(f"❌ PDF no encontrado, procediendo con descarga\n")
            
            # Configurar URLs y parámetros
            print(f"🌐 PASO 7/10: Configurando URLs y parámetros de descarga...")
            url_base = 'https://ingress.pressreader.com/services/IssueInfo/GetPageKeys?accessToken='
            libro = self.medio_config['libro']
            url_ids = [id.strip() for id in self.medio_config.get('url_ids', '').split(',') if id.strip()]
            cad01 = f'https://i.prcdn.co/img?file={libro}'
            cad03 = '&ticket='
            
            print(f"📚 Libro: {libro}")
            print(f"🆔 URL IDs: {url_ids}")
            print(f"✅ Configuración de URLs completada\n")
            
            # 🎯 PROCESO ESTÁNDAR UNIFICADO PARA TODOS LOS MEDIOS (basado en tiempo.py exitoso)
            print(f"📋 Iniciando proceso estándar unificado para {medio_name}\n")

            use_browser_auth = self.medio_config.get('use_browser_auth', 'False').lower() == 'true'

            # Validar token ANTES de descargar; si falla → refrescar proactivamente
            print("🔑 Validando token contra API...")
            if self._validar_token_actual():
                print("  ✅ Token válido")
            else:
                print("  ❌ Token inválido o caducado")
                if not self._try_refresh_token():
                    print("\n❌ Error: No se pudo obtener token válido")
                    return False

            # Descargar imágenes usando la lógica robusta
            print(f"📥 PASO 8/10: Descargando imágenes desde PressReader...")
            print(f"{'─'*70}")
            if self.medio_config.get('use_tokens', 'False').lower() == 'true':
                print(f"🔑 Modo: Múltiples tokens ({len(self.tokens)} tokens disponibles)")
                if not self.descargar_con_tokens(url_base, libro, year, mes, dia,
                                               url_ids, cad01, cad03, rutafin):
                    if use_browser_auth and self._try_refresh_token():
                        print("🔄 Reintentando descarga con token fresco...")
                        if not self.descargar_con_tokens(url_base, libro, year, mes, dia,
                                                       url_ids, cad01, cad03, rutafin):
                            print("\n❌ Error: No se pudo completar la descarga ni con token fresco")
                            return False
                    else:
                        print("\n❌ Error: No se pudo completar la descarga")
                        return False
            else:
                print(f"🔑 Modo: Token único ({len(self.tokens)} token disponible)")
                # Proceso con un solo token pero probando múltiples url_ids
                descarga_exitosa = False
                for url_id in url_ids:
                    print(f"\n🔄 Intentando con URL ID: {url_id}")
                    if self.intentar_descargar_imagenes(self.tokens[0], url_base, libro, year, mes, dia,
                                                       url_id, cad01, url_id, cad03, rutafin):
                        print(f"\n✅ Descarga exitosa con URL ID: {url_id}")
                        descarga_exitosa = True
                        break
                    else:
                        print(f"❌ URL ID {url_id} falló, probando siguiente...")

                if not descarga_exitosa:
                    # Intentar refresco vía browser auth si está habilitado
                    if use_browser_auth and self._try_refresh_token():
                        print("🔄 Reintentando descarga con token fresco (browser auth)...")
                        for url_id in url_ids:
                            print(f"\n🔄 Intentando con URL ID: {url_id}")
                            if self.intentar_descargar_imagenes(self.tokens[0], url_base, libro, year, mes, dia,
                                                               url_id, cad01, url_id, cad03, rutafin):
                                print(f"\n✅ Descarga exitosa con token fresco, URL ID: {url_id}")
                                descarga_exitosa = True
                                break
                            else:
                                print(f"❌ URL ID {url_id} falló con token fresco también")

                if not descarga_exitosa:
                    print("\n❌ Error: No se pudo completar la descarga con ningún URL ID")
                    return False

            print(f"\n{'─'*70}")
            print(f"✅ PASO 8/10 completado: Descarga de imágenes finalizada exitosamente\n")
            
            # 📄 CREAR PDFs INDIVIDUALES CON OCR DESDE LAS IMÁGENES
            print(f"📄 PASO 9/10: Creando PDFs individuales con OCR desde imágenes...")
            print(f"🔍 Leyendo imágenes desde: {ruta_imagenes}")
            
            # Contar imágenes disponibles
            jpg_count = len([f for f in os.listdir(ruta_imagenes) if f.endswith('.jpg')])
            print(f"📊 Se procesarán {jpg_count} imágenes con OCR\n")
            print(f"{'─'*70}")
            
            ruta_paginas_ocr = os.path.join(rutafin, "pdf_paginas_ocr")
            self.crear_pdf_con_texto_superpuesto_para_carpeta(
                ruta_imagenes, ruta_paginas_ocr, y_adjustment=10, font_scale=0.45)
            
            print(f"{'─'*70}")
            print(f"✅ PASO 9/10 completado: PDFs individuales con OCR creados\n")
            
            # 📄 UNIR LOS PDFs CON OCR EN EL PDF FINAL
            print(f"📄 UNIR PDFs: Uniendo {len([f for f in os.listdir(ruta_paginas_ocr) if f.endswith('.pdf')])} PDFs en archivo final...")
            pdf_files = [os.path.join(ruta_paginas_ocr, f) for f in 
                        natsorted(os.listdir(ruta_paginas_ocr)) if f.endswith('.pdf')]
            
            if not pdf_files:
                print("❌ Error: No se encontraron PDFs individuales en pdf_paginas_ocr/")
                return False
            
            print(f"🔗 Procesando {len(pdf_files)} páginas...")
            merged_pdf: Any = fitz.open()
            for pdf_file in pdf_files:
                pdf_document = fitz.open(pdf_file)
                merged_pdf.insert_pdf(pdf_document)
                pdf_document.close()
            
            merged_pdf.save(output)
            merged_pdf.close()
            
            if os.path.exists(output) and os.path.getsize(output) > 0:
                file_size = os.path.getsize(output) / 1024 / 1024
                print(f"✅ PDF final con OCR creado: {output}")
                print(f"📏 Tamaño del PDF final: {file_size:.1f} MB\n")
            else:
                print(f"❌ Error creando PDF final: {output}")
                return False
            
            # Eliminar imágenes sueltas de la raíz (ya están en imagenes/)
            print(f"🧹 LIMPIEZA PASO A: Eliminando imágenes residuales de la raíz...")
            archivos_eliminados = 0
            for archivo in os.listdir(rutafin):
                if archivo.endswith('.jpg'):
                    try:
                        os.remove(os.path.join(rutafin, archivo))
                        archivos_eliminados += 1
                    except Exception:
                        pass
            
            print(f"✅ {archivos_eliminados} imágenes residuales eliminadas")
            
            # 🧹 LIMPIEZA PASO B: Eliminar archivos temporales (si existen)
            print(f"🧹 LIMPIEZA PASO B: Eliminando archivos temporales...")
            temp_patterns = ['_temp.jpg', '_ocr_temp.jpg', '_temp', '.creating']
            temp_eliminados = 0
            for archivo in os.listdir(rutafin):
                archivo_path = os.path.join(rutafin, archivo)
                if any(pattern in archivo for pattern in temp_patterns):
                    if os.path.isfile(archivo_path):
                        try:
                            os.remove(archivo_path)
                            temp_eliminados += 1
                            print(f"  🗑️ Temporal eliminado: {archivo}")
                        except Exception:
                            pass
                    elif os.path.isdir(archivo_path):
                        try:
                            shutil.rmtree(archivo_path)
                            temp_eliminados += 1
                            print(f"  🗑️ Directorio temporal eliminado: {archivo}")
                        except Exception:
                            pass
            
            if temp_eliminados > 0:
                print(f"✅ {temp_eliminados} archivos temporales eliminados")
            else:
                print(f"✅ No hay archivos temporales para eliminar")
            
            # 📝 CREAR PDF DE TEXTO COMPLETO para validación de OCR
            # Guardar en la subcarpeta de fecha (rutafin) junto con el PDF final y las imágenes
            output_texto_pdf = os.path.join(
                rutafin,
                f"{self.medio_config['impreso']}_{year}{mes}{dia}_texto_validacion.pdf"
            )
            print(f"\n📝 PASO EXTRA: Creando PDF completo de texto (sin imagen) para validar calidad OCR...")
            print(f"  📁 Ruta PDF texto: {output_texto_pdf}")
            self.crear_pdf_texto_desde_imagenes(
                ruta_imagenes, output_texto_pdf, y_adjustment=10, font_scale=0.45)
            
            print(f"📁 Estructura final: imagenes/, pdf_paginas_ocr/")
            print(f"📝 PDF de validación: {os.path.basename(output_texto_pdf)}\n")
            
            # Subir a FTP DESDE LAS SUBCARPETAS
            if os.path.exists(output):
                print(f"📤 PASO 10/10: Subiendo archivos al FTP desde subcarpetas...")
                print(f"🌐 Host FTP: {self.ftp_config['host']}")
                print(f"📂 Ruta FTP: {self.medio_config.get('ftp_path', '/')}")
                ftp_path = self.medio_config.get('ftp_path', '/')
                
                # Subir imágenes DESDE LA CARPETA IMAGENES
                jpg_count = len([f for f in os.listdir(ruta_imagenes) if f.endswith('.jpg')])
                if os.path.isdir(ruta_imagenes) and jpg_count > 0:
                    print(f"  📷 Subiendo {jpg_count} imágenes desde: imagenes/")
                    self.upload_images_to_ftp(ruta_imagenes, ftp_path)
                else:
                    print(f"  ⚠️ No hay imágenes para subir")
                
                # Subir PDFs de páginas individuales DESDE pdf_paginas_ocr
                pdf_count = len([f for f in os.listdir(ruta_paginas_ocr) if f.endswith('.pdf')])
                if os.path.isdir(ruta_paginas_ocr) and pdf_count > 0:
                    print(f"  📄 Subiendo {pdf_count} PDFs de páginas desde: pdf_paginas_ocr/")
                    self.upload_pdfs_to_ftp(ruta_paginas_ocr, ftp_path)
                else:
                    print(f"  ⚠️ No hay PDFs de páginas para subir")
                
                # Subir PDF de texto de validación
                if os.path.exists(output_texto_pdf) and os.path.getsize(output_texto_pdf) > 0:
                    print(f"  📝 Subiendo PDF de validación de OCR al FTP...")
                    print(f"     📄 Archivo: {os.path.basename(output_texto_pdf)}")
                    try:
                        self.upload_pdf_to_ftp(output_texto_pdf, ftp_path)
                        print(f"     ✅ PDF de validación subido al FTP exitosamente")
                    except Exception as e:
                        print(f"     ⚠️ Error subiendo PDF de validación: {e}")
                else:
                    print(f"  ⚠️ PDF de validación no existe o está vacío")
                
                # Subir PDF final
                print(f"  📄 Subiendo PDF final al FTP...")
                uploaded = False
                intento = 1
                while not uploaded:
                    try:
                        self.upload_pdf_to_ftp(output, ftp_path)
                        print(f"✅ Archivo PDF subido al FTP exitosamente")
                        uploaded = True
                    except Exception as e:
                        print(f"❌ Error subiendo PDF (intento {intento}): {e}")
                        print(f"⏳ Reintentando en 1 minuto...")
                        time.sleep(60)
                        intento += 1
                
                print(f"✅ PASO 10/10 completado: Subida al FTP finalizada\n")
            
            print(f"{'='*70}")
            print(f"🎉 PROCESO COMPLETADO EXITOSAMENTE PARA: {medio_name}")
            print(f"{'='*70}\n")
            return True
            
        except Exception as e:
            print(f"\n{'='*70}")
            print(f"❌ ERROR PROCESANDO {medio_name}")
            print(f"{'='*70}")
            print(f"💥 Error: {e}")
            print(f"📍 Tipo de error: {type(e).__name__}")
            import traceback
            print(f"\n📋 Traceback completo:")
            traceback.print_exc()
            print(f"{'='*70}\n")
            return False
        
        finally:
            print(f"🔓 Liberando archivo de lock...")
            self.cleanup_lock_file()
    
    def process_all_media(self, args):
        """Procesar todos los medios disponibles en secuencia"""
        available_media = self.get_available_media()
        
        print(f"\n{'='*60}")
        print(f"INICIANDO PROCESAMIENTO MASIVO DE TODOS LOS MEDIOS")
        print(f"Medios a procesar: {', '.join(available_media)}")
        print(f"Fecha: {self.parse_date_args(args).strftime('%Y-%m-%d')}")
        print(f"{'='*60}\n")
        
        success_count = 0
        failed_media = []
        
        for i, medio in enumerate(available_media, 1):
            print(f"\n{'─'*50}")
            print(f"PROCESANDO MEDIO {i}/{len(available_media)}: {medio}")
            print(f"{'─'*50}")
            
            try:
                # Limpiar configuración anterior
                self.medio_config = {}
                self.tokens = []
                self.tm = []

                # Procesar medio individual
                if self.process_single_medium(medio, args):
                    success_count += 1
                    print(f"✅ {medio} completado exitosamente")
                else:
                    failed_media.append(medio)
                    print(f"❌ {medio} falló")
                
                # Pausa entre medios para evitar sobrecarga
                if i < len(available_media):
                    print(f"Pausa de 5 segundos antes del siguiente medio...")
                    time.sleep(5)
                    
            except Exception as e:
                failed_media.append(medio)
                print(f"❌ Error crítico procesando {medio}: {e}")
                continue
        
        # Resumen final
        print(f"\n{'='*60}")
        print(f"RESUMEN FINAL DEL PROCESAMIENTO MASIVO")
        print(f"{'='*60}")
        print(f"Total de medios procesados: {len(available_media)}")
        print(f"Exitosos: {success_count}")
        print(f"Fallidos: {len(failed_media)}")
        
        if failed_media:
            print(f"Medios que fallaron: {', '.join(failed_media)}")
        
        if success_count == len(available_media):
            print(f"🎉 TODOS LOS MEDIOS PROCESADOS EXITOSAMENTE")
        elif success_count > 0:
            print(f"⚠️  Algunos medios fallaron, pero {success_count} fueron exitosos")
        else:
            print(f"💥 NINGÚN MEDIO FUE PROCESADO EXITOSAMENTE")
        
        print(f"{'='*60}\n")
    
    def main(self, args):
        """Función principal del orquestador"""
        if hasattr(args, 'medio') and args.medio:
            # Procesar un medio específico
            self.process_single_medium(args.medio, args)
        else:
            # Procesar todos los medios
            self.process_all_media(args)


def main():
    """Función principal para ejecución desde línea de comandos"""
    parser = argparse.ArgumentParser(
        description='PressReader Downloader Orchestrator',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Ejemplos de uso:
  python pressreader_downloader.py                    # Procesar TODOS los medios
  python pressreader_downloader.py Tiempo             # Procesar medio específico
  python pressreader_downloader.py Espectador --fecha 2024-06-01
  python pressreader_downloader.py Motor
  python pressreader_downloader.py Opinion --custom-fecha 2024-12-25

Medios disponibles:
  Tiempo, Espectador, Opinion, Publimetro, Motor

Si no se especifica un medio, se procesarán TODOS los medios disponibles en secuencia.
        '''
    )
    
    parser.add_argument('medio', 
                       nargs='?',  # Hacer opcional
                       choices=['Tiempo', 'Espectador', 'Opinion', 'Publimetro', 'Motor'],
                       help='Medio a procesar (opcional: si se omite, procesa todos los medios)')
    parser.add_argument('--fecha', 
                       help='Fecha personalizada en formato YYYY-MM-DD (ej: 2024-06-01)')
    parser.add_argument('--custom-fecha', 
                       help='Alias para --fecha')
    parser.add_argument('--config', 
                       default='press.ini',
                       help='Archivo de configuración (default: press.ini)')
    
    args = parser.parse_args()
    
    try:
        downloader = PressReaderDownloader(args.config)
        
        # Verificar si se especificó un medio o procesar todos
        if args.medio:
            print(f"🔄 Procesando medio específico: {args.medio}")
        else:
            print("🚀 Iniciando procesamiento masivo de TODOS los medios disponibles")
            available_media = downloader.get_available_media()
            print(f"📋 Medios encontrados: {', '.join(available_media)}")
        
        downloader.main(args)
        
    except KeyboardInterrupt:
        print("\n⚠️ Proceso interrumpido por el usuario")
        sys.exit(1)
    except Exception as e:
        print(f"💥 Error fatal: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()