"""
Módulo común para Laboratorio IA 01B: Embodied AI con Gemini Robotics-ER 2 y Kinova Gen3.
Implementa representación estructurada, conversión de coordenadas, registro de profundidad,
desproyección 3D, validación de espacio de trabajo y poses de pre-agarre.
"""

import json
import re
from typing import Dict, List, Optional, Tuple, Union
import numpy as np


# -----------------------------------------------------------------------------
# DICCIONARIO DE PROMPTS DEL SISTEMA Y TAREAS (TODO 1)
# -----------------------------------------------------------------------------
PROMPTS = {
    "propio": (
        "Analiza la escena física capturada por la cámara RGB-D del robot. "
        "Razona explícitamente sobre las relaciones espaciales entre los objetos "
        "(contención, estabilidad, cercanía, apoyo y ausencia de obstáculos). "
        "Identifica el objeto solicitado: '{objeto}'. "
        "Devuelve ÚNICAMENTE un arreglo JSON con el punto central óptimo de agarre en coordenadas "
        "normalizadas [y, x] de 0 a 1000, respetando el orden fila primero y columna segundo: "
        '[{"point": [y, x], "label": "{objeto}"}]'
    ),
    "ingenuo": (
        "Indica dónde está {objeto} en la imagen. "
        'Responde en JSON: [{"point": [y, x], "label": "{objeto}"}]'
    ),
    "cajas": (
        "Detecta todos los objetos manipulables en la escena y sus recipientes. "
        "Devuelve ÚNICAMENTE un arreglo JSON con las cajas delimitadoras normalizadas [0-1000]: "
        '[{"box_2d": [ymin, xmin, ymax, xmax], "label": "nombre_objeto"}]'
    ),
    "agarre": (
        "Evalúa si el objeto '{objeto}' puede ser agarrado de manera segura desde arriba por el manipulador. "
        "Un agarre es INSEGURO si: hay otro objeto encima, hay una mano humana cerca, o el objeto está inestable/volcado. "
        "Si es INSEGURO, abstente devolviendo una lista vacía: []. "
        "Si es SEGURO, devuelve el punto de agarre normalizado [0-1000]: "
        '[{"point": [y, x], "label": "{objeto}"}]'
    ),
    "plan": (
        "Genera un plan de manipulación estructurado para cumplir la tarea: '{objeto}'. "
        "Usa exclusivamente el vocabulario permitido: "
        "['mover_a', 'aproximar_preagarre', 'bajar', 'cerrar_pinza', 'subir', 'trasladar', 'soltar_abrir_pinza']. "
        "Devuelve ÚNICAMENTE un arreglo JSON con los pasos y objetivos."
    ),
    "exito": (
        "Compara la imagen antes y después de la acción de manipulación para verificar: '{objeto}'. "
        'Devuelve ÚNICAMENTE un objeto JSON: {"exito": true/false, "razon": "explicación breve"}'
    ),
}


# -----------------------------------------------------------------------------
# TODO 2: CONVERSIÓN DE COORDENADAS NORMALIZADAS A PÍXEL
# -----------------------------------------------------------------------------
def normalizado_a_pixel(punto_yx: Union[List[int], Tuple[int, int]], ancho: int, alto: int) -> Tuple[int, int]:
    """
    Convierte un punto normalizado [y, x] en el rango 0-1000 a coordenadas de píxel (u, v).
    
    Parámetros:
        punto_yx: Lista o tupla [y, x] con valores normalizados entre 0 y 1000.
                  ATENCIÓN: punto_yx[0] es la fila (y), punto_yx[1] es la columna (x).
        ancho: Ancho de la imagen (W) en píxeles.
        alto: Alto de la imagen (H) en píxeles.
        
    Retorna:
        (u, v): Coordenadas de píxel enteras (u = columna, v = fila).
        
    Lanza:
        ValueError: Si algún valor está fuera del rango [0, 1000] o la entrada es inválida.
    """
    if len(punto_yx) != 2:
        raise ValueError(f"punto_yx debe contener exactamente 2 elementos [y, x], recibido: {punto_yx}")
    
    y_n, x_n = float(punto_yx[0]), float(punto_yx[1])
    
    if not (0.0 <= y_n <= 1000.0 and 0.0 <= x_n <= 1000.0):
        raise ValueError(f"Coordenadas normalizadas fuera de rango [0, 1000]: y={y_n}, x={x_n}")
    
    if ancho <= 0 or alto <= 0:
        raise ValueError(f"Dimensiones de imagen inválidas: ancho={ancho}, alto={alto}")
    
    u = int(round((x_n / 1000.0) * (ancho - 1)))
    v = int(round((y_n / 1000.0) * (alto - 1)))
    
    # Asegurar límites dentro de la imagen
    u = max(0, min(ancho - 1, u))
    v = max(0, min(alto - 1, v))
    
    return u, v


def caja_a_pixeles(box_2d: List[float], ancho: int, alto: int) -> Tuple[int, int, int, int]:
    """
    Convierte una caja delimitadora normalizada [ymin, xmin, ymax, xmax] (0-1000)
    a coordenadas de píxel (u_min, v_min, u_max, v_max).
    """
    ymin, xmin, ymax, xmax = box_2d
    u_min, v_min = normalizado_a_pixel([ymin, xmin], ancho, alto)
    u_max, v_max = normalizado_a_pixel([ymax, xmax], ancho, alto)
    return u_min, v_min, u_max, v_max


# -----------------------------------------------------------------------------
# TODO 3: ESTIMACIÓN DE PROFUNDIDAD ROBUSTA (MEDIANA LOCAL)
# -----------------------------------------------------------------------------
def profundidad_robusta(
    depth_mm: np.ndarray,
    u: int,
    v: int,
    ventana: int = 5
) -> Optional[float]:
    """
    Estima la distancia métrica en una ventana centrada en (u, v) en la imagen de profundidad.
    Filtra los ceros (píxeles sin dato del sensor de profundidad) y toma la mediana.
    
    Parámetros:
        depth_mm: Matriz 2D de profundidad (numpy array) con valores en milímetros.
        u: Coordenada horizontal (columna).
        v: Coordenada vertical (fila).
        ventana: Dimensión de la ventana cuadrada (debe ser impar, ej. 5 o 7).
        
    Retorna:
        Profundidad en metros (float) o None si no hay píxeles válidos (> 0).
    """
    if depth_mm is None or depth_mm.size == 0:
        return None
    
    h_img, w_img = depth_mm.shape[:2]
    if u < 0 or u >= w_img or v < 0 or v >= h_img:
        return None
    
    radio = ventana // 2
    v_min = max(0, v - radio)
    v_max = min(h_img, v + radio + 1)
    u_min = max(0, u - radio)
    u_max = min(w_img, u + radio + 1)
    
    parche = depth_mm[v_min:v_max, u_min:u_max]
    validos = parche[parche > 0]
    
    # Si la ventana es muy pequeña y cae en un hueco de submuestreo, expandir ligeramente
    if validos.size == 0 and ventana < 9:
        radio_exp = 4
        v_min_e = max(0, v - radio_exp)
        v_max_e = min(h_img, v + radio_exp + 1)
        u_min_e = max(0, u - radio_exp)
        u_max_e = min(w_img, u + radio_exp + 1)
        parche_exp = depth_mm[v_min_e:v_max_e, u_min_e:u_max_e]
        validos = parche_exp[parche_exp > 0]
        
    if validos.size == 0:
        return None
    
    mediana_mm = float(np.median(validos))
    return mediana_mm / 1000.0  # Conversión a metros


# -----------------------------------------------------------------------------
# TODO 4: DESPROYECCIÓN PINHOLE INVERSA (2D + Z -> 3D)
# -----------------------------------------------------------------------------
def desproyectar(
    u: float,
    v: float,
    z_m: float,
    k: Union[Dict[str, float], np.ndarray]
) -> np.ndarray:
    """
    Proyecta un píxel (u, v) con profundidad métrica z_m al espacio 3D en el marco óptico.
    Modelo pinhole inverso:
        X = (u - cx) * Z / fx
        Y = (v - cy) * Z / fy
        Z = z_m
        
    Marco óptico de la cámara:
        +X hacia la derecha
        +Y hacia abajo
        +Z hacia adelante (profundidad)
        
    Parámetros:
        u: Columna del píxel.
        v: Fila del píxel.
        z_m: Profundidad en metros.
        k: Diccionario {'fx', 'fy', 'cx', 'cy'} o matriz 3x3 de intrínsecos.
        
    Retorna:
        np.ndarray de forma (3,) con coordenadas [X, Y, Z] en metros.
    """
    if isinstance(k, dict):
        fx = float(k["fx"])
        fy = float(k["fy"])
        cx = float(k["cx"])
        cy = float(k["cy"])
    else:
        # Matriz 3x3
        fx = float(k[0, 0])
        fy = float(k[1, 1])
        cx = float(k[0, 2])
        cy = float(k[1, 2])
        
    if fx == 0.0 or fy == 0.0:
        raise ValueError("Longitudes focales fx y fy no pueden ser cero.")
        
    x_m = ((u - cx) * z_m) / fx
    y_m = ((v - cy) * z_m) / fy
    
    return np.array([x_m, y_m, float(z_m)], dtype=float)


# -----------------------------------------------------------------------------
# REGISTRO DE PROFUNDIDAD (PROFUNDIDAD -> MARCO DE COLOR)
# -----------------------------------------------------------------------------
def registrar_profundidad(
    depth_mm: np.ndarray,
    k_d: Union[Dict[str, float], np.ndarray],
    k_c: Union[Dict[str, float], np.ndarray],
    r: np.ndarray,
    t: np.ndarray,
    forma_color: Tuple[int, int] = (1080, 1920)
) -> np.ndarray:
    """
    Registra la imagen de profundidad al marco del sensor de color usando intrínsecos y extrínsecos.
    
    P_d = z_d * K_d^(-1) * [u_d, v_d, 1]^T
    P_c = R * P_d + t
    p_c ~ K_c * P_c
    """
    alto_c, ancho_c = forma_color
    alto_d, ancho_d = depth_mm.shape[:2]
    
    depth_registrada = np.zeros((alto_c, ancho_c), dtype=np.uint16)
    
    if isinstance(k_d, dict):
        fx_d, fy_d, cx_d, cy_d = k_d["fx"], k_d["fy"], k_d["cx"], k_d["cy"]
    else:
        fx_d, fy_d, cx_d, cy_d = k_d[0, 0], k_d[1, 1], k_d[0, 2], k_d[1, 2]
        
    if isinstance(k_c, dict):
        fx_c, fy_c, cx_c, cy_c = k_c["fx"], k_c["fy"], k_c["cx"], k_c["cy"]
    else:
        fx_c, fy_c, cx_c, cy_c = k_c[0, 0], k_c[1, 1], k_c[0, 2], k_c[1, 2]

    # Malla de coordenadas en profundidad
    u_d, v_d = np.meshgrid(np.arange(ancho_d), np.arange(alto_d))
    z_d_mm = depth_mm.astype(float)
    mascara_valida = z_d_mm > 0
    
    if not np.any(mascara_valida):
        return depth_registrada

    u_val = u_d[mascara_valida]
    v_val = v_d[mascara_valida]
    z_val_m = z_d_mm[mascara_valida] / 1000.0  # metros

    # Desproyección en marco de profundidad
    x_d = (u_val - cx_d) * z_val_m / fx_d
    y_d = (v_val - cy_d) * z_val_m / fy_d
    p_d = np.vstack((x_d, y_d, z_val_m))  # 3 x N

    # Transformación extrínseca al marco de color
    t_m = np.asarray(t).reshape(3, 1)
    if np.max(np.abs(t_m)) > 5.0:  # Si viene en mm
        t_m = t_m / 1000.0
    r_mat = np.asarray(r)
    p_c = r_mat @ p_d + t_m

    x_c = p_c[0, :]
    y_c = p_c[1, :]
    z_c = p_c[2, :]

    # Reproyección a píxeles de color
    z_c_seguro = np.where(z_c <= 0, 1e-6, z_c)
    u_c = np.round((x_c * fx_c / z_c_seguro) + cx_c).astype(int)
    v_c = np.round((y_c * fy_c / z_c_seguro) + cy_c).astype(int)

    # Filtrar píxeles que caen dentro de la resolución de color
    en_rango = (u_c >= 0) & (u_c < ancho_c) & (v_c >= 0) & (v_c < alto_c) & (z_c > 0)
    u_c_en = u_c[en_rango]
    v_c_en = v_c[en_rango]
    z_c_mm_en = np.round(z_c[en_rango] * 1000.0).astype(np.uint16)

    depth_registrada[v_c_en, u_c_en] = z_c_mm_en
    return depth_registrada


# -----------------------------------------------------------------------------
# FASE 9: VALIDACIÓN DE ESPACIO DE TRABAJO Y PRE-AGARRE
# -----------------------------------------------------------------------------
def dentro_del_volumen(xyz: Union[List[float], np.ndarray], lim: Dict[str, float]) -> bool:
    """
    Verifica si un punto 3D [X, Y, Z] se encuentra dentro del prisma de trabajo seguro.
    
    limites: dict con 'x_min', 'x_max', 'y_min', 'y_max', 'z_mesa', 'h_max'
    """
    x, y, z = xyz
    return bool(
        lim["x_min"] <= x <= lim["x_max"]
        and lim["y_min"] <= y <= lim["y_max"]
        and (lim["z_mesa"] - lim["h_max"]) <= z <= lim["z_mesa"]
    )


def pose_pre_agarre(xyz: Union[List[float], np.ndarray], n: Union[List[float], np.ndarray], d_a: float = 0.10) -> np.ndarray:
    """
    Calcula la pose de pre-agarre retrocediendo una distancia d_a a lo largo de la normal de la mesa:
    p_pre = p + d_a * n
    
    Parámetros:
        xyz: Punto 3D de agarre en el objeto [X, Y, Z].
        n: Vector normal unitario de la mesa orientado hacia la cámara.
        d_a: Distancia de aproximación en metros (típicamente 0.10 m = 10 cm).
    """
    n_unit = np.asarray(n, dtype=float)
    norm = np.linalg.norm(n_unit)
    if norm > 1e-6:
        n_unit = n_unit / norm
    return np.asarray(xyz, dtype=float) + d_a * n_unit


def calcular_normal_mesa(p1: np.ndarray, p2: np.ndarray, p3: np.ndarray) -> np.ndarray:
    """
    Calcula la normal unitaria del plano de la mesa a partir de tres puntos no colineales.
    Se orienta hacia la cámara (componente Z negativa en el marco óptico si mira hacia abajo).
    """
    v1 = np.asarray(p2) - np.asarray(p1)
    v2 = np.asarray(p3) - np.asarray(p1)
    normal = np.cross(v1, v2)
    norm = np.linalg.norm(normal)
    if norm < 1e-6:
        raise ValueError("Puntos colineales o degenerados; no se puede estimar la normal.")
    normal = normal / norm
    # En el marco óptico, si la cámara mira hacia abajo a la mesa, la normal de la mesa
    # que apunta hacia la cámara tiene componente Z negativa (hacia el sensor).
    if normal[2] > 0:
        normal = -normal
    return normal


# -----------------------------------------------------------------------------
# UTILIDADES DE PARSEO Y MANEJO DE JSON
# -----------------------------------------------------------------------------
def extraer_json(texto: str) -> Union[dict, list]:
    """
    Extrae y parsea de forma robusta estructuras JSON desde respuestas en texto de Gemini,
    removiendo bloques markdown ```json ... ``` si están presentes.
    """
    if not texto or not texto.strip():
        raise ValueError("Texto de respuesta vacío")
        
    texto_limpio = texto.strip()
    
    # Extraer contenido dentro de bloques ```json ... ```
    match_bloque = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", texto_limpio)
    if match_bloque:
        texto_limpio = match_bloque.group(1).strip()
        
    # Intentar parseo directo
    try:
        return json.loads(texto_limpio)
    except json.JSONDecodeError:
        # Intentar encontrar el primer '[' o '{' y el último ']' o '}'
        start_bracket = texto_limpio.find("[")
        start_brace = texto_limpio.find("{")
        
        if start_bracket != -1 and (start_brace == -1 or start_bracket < start_brace):
            end_bracket = texto_limpio.rfind("]")
            if end_bracket != -1:
                return json.loads(texto_limpio[start_bracket : end_bracket + 1])
        elif start_brace != -1:
            end_brace = texto_limpio.rfind("}")
            if end_brace != -1:
                return json.loads(texto_limpio[start_brace : end_brace + 1])
                
        raise ValueError(f"No se pudo extraer JSON válido del texto: {texto[:100]}...")


# -----------------------------------------------------------------------------
# UTILIDAD: OBTENCIÓN AUTOMÁTICA DE LA API KEY (Sin escribirla en la terminal)
# -----------------------------------------------------------------------------
def obtener_api_key() -> Optional[str]:
    """
    Recupera la clave GEMINI_API_KEY en cascada para no tener que cargarla en la terminal:
    1. Variable de entorno activa en la sesión (os.environ['GEMINI_API_KEY']).
    2. Archivo local .env en la raíz del proyecto (GEMINI_API_KEY="...").
    3. Archivo local api_key.txt en la raíz del proyecto.
    4. Registro de Variables de Usuario de Windows (HKEY_CURRENT_USER\\Environment).
    """
    import os
    from pathlib import Path

    # 1. Variable de entorno de la sesión
    key = os.environ.get("GEMINI_API_KEY")
    if key and key.strip():
        return key.strip()

    # 2. Archivos locales (.env o api_key.txt)
    for nombre in [".env", "api_key.txt"]:
        archivo = Path(nombre)
        if archivo.exists():
            try:
                for linea in archivo.read_text(encoding="utf-8").splitlines():
                    linea = linea.strip()
                    if linea.startswith("GEMINI_API_KEY="):
                        val = linea.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            os.environ["GEMINI_API_KEY"] = val
                            return val
                    elif linea and not linea.startswith("#") and linea.startswith("AIza"):
                        os.environ["GEMINI_API_KEY"] = linea
                        return linea
            except Exception:
                pass

    # 3. Variables de Entorno persistentes del usuario en Windows
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as rkey:
            val, _ = winreg.QueryValueEx(rkey, "GEMINI_API_KEY")
            if val and str(val).strip():
                val_str = str(val).strip()
                os.environ["GEMINI_API_KEY"] = val_str
                return val_str
    except Exception:
        pass

    return None

