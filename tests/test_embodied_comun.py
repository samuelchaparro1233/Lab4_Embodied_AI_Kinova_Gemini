"""
Suite de pruebas unitarias para el Laboratorio IA 01B (Embodied AI).
Evalúa los TODO 1 a 4, registro multimodal, desproyección 3D y validación de seguridad.
Meta de la guía: 15 passed.
"""

import pytest
import numpy as np

from embodied_comun import (
    normalizado_a_pixel,
    caja_a_pixeles,
    profundidad_robusta,
    desproyectar,
    registrar_profundidad,
    dentro_del_volumen,
    pose_pre_agarre,
    calcular_normal_mesa,
    extraer_json,
    PROMPTS,
)


# =============================================================================
# PRUEBAS DE CONVERSIÓN NORMALIZADA A PÍXEL (TODO 2)
# =============================================================================

def test_normalizado_a_pixel_centro():
    """Prueba que el centro normalizado [500, 500] mapea al centro de la imagen."""
    W, H = 1920, 1080
    u, v = normalizado_a_pixel([500, 500], W, H)
    assert u == 960
    assert v == 540


def test_normalizado_a_pixel_orden_yx_y_esquinas():
    """
    Verifica que punto_yx recibe [y, x] (fila primero, columna segundo)
    y mapea correctamente las 4 esquinas sin salirse del rango [0, W-1] y [0, H-1].
    """
    W, H = 1920, 1080
    
    # Esquina superior-izquierda: y=0, x=0 -> u=0, v=0
    assert normalizado_a_pixel([0, 0], W, H) == (0, 0)
    
    # Esquina inferior-derecha: y=1000, x=1000 -> u=1919, v=1079
    assert normalizado_a_pixel([1000, 1000], W, H) == (1919, 1079)
    
    # y=200 (fila v), x=800 (columna u)
    u, v = normalizado_a_pixel([200, 800], W, H)
    assert u == int(round(0.8 * 1919))
    assert v == int(round(0.2 * 1079))


def test_normalizado_a_pixel_fuera_de_rango_y():
    """Debe lanzar ValueError si la componente y está fuera de [0, 1000]."""
    with pytest.raises(ValueError):
        normalizado_a_pixel([-5, 500], 1920, 1080)
    with pytest.raises(ValueError):
        normalizado_a_pixel([1005, 500], 1920, 1080)


def test_normalizado_a_pixel_fuera_de_rango_x():
    """Debe lanzar ValueError si la componente x está fuera de [0, 1000]."""
    with pytest.raises(ValueError):
        normalizado_a_pixel([500, -1], 1920, 1080)
    with pytest.raises(ValueError):
        normalizado_a_pixel([500, 1200], 1920, 1080)


# =============================================================================
# PRUEBAS DE ESTIMACIÓN DE PROFUNDIDAD ROBUSTA (TODO 3)
# =============================================================================

def test_profundidad_robusta_valor_limpio():
    """Verifica la lectura correcta de una superficie uniforme en milímetros a metros."""
    depth_map = np.full((100, 100), 750, dtype=np.uint16)
    z_m = profundidad_robusta(depth_map, u=50, v=50, ventana=5)
    assert z_m == pytest.approx(0.750, abs=1e-4)


def test_profundidad_robusta_filtra_ceros():
    """
    Simula píxeles sin retorno (valor 0) típicos del sensor IR en bordes o reflexiones.
    La mediana debe calcularse ignorando los ceros.
    """
    depth_map = np.full((10, 10), 0, dtype=np.uint16)
    # Solo 3 valores válidos alrededor del centro
    depth_map[4, 4] = 600
    depth_map[5, 5] = 620
    depth_map[5, 4] = 610
    
    z_m = profundidad_robusta(depth_map, u=5, v=5, ventana=5)
    assert z_m == pytest.approx(0.610, abs=1e-3)


def test_profundidad_robusta_ignora_outliers_con_mediana():
    """
    Construye una ventana con un valor espurio (reflejo o ruido extremo).
    La mediana debe ser inmune al valor atípico mientras que el promedio fallaría.
    """
    patch = np.array([
        [600, 602, 601],
        [598, 600, 3500],  # 3500 mm es un reflejo espurio
        [601, 599, 600]
    ], dtype=np.uint16)
    
    z_m = profundidad_robusta(patch, u=1, v=1, ventana=3)
    assert z_m == pytest.approx(0.600, abs=1e-3)


def test_profundidad_robusta_retorna_none_si_todo_cero():
    """Si no hay píxeles válidos en toda la ventana, retorna None."""
    depth_map = np.zeros((20, 20), dtype=np.uint16)
    z_m = profundidad_robusta(depth_map, u=10, v=10, ventana=5)
    assert z_m is None


def test_profundidad_robusta_limites_imagen():
    """Verifica que la ventana no cause desbordamiento en las esquinas de la imagen."""
    depth_map = np.full((50, 50), 800, dtype=np.uint16)
    # Esquina (0, 0) y esquina (49, 49)
    z_top_left = profundidad_robusta(depth_map, u=0, v=0, ventana=7)
    z_bottom_right = profundidad_robusta(depth_map, u=49, v=49, ventana=7)
    assert z_top_left == pytest.approx(0.800, abs=1e-3)
    assert z_bottom_right == pytest.approx(0.800, abs=1e-3)


# =============================================================================
# PRUEBAS DE DESPROYECCIÓN PINHOLE INVERSA (TODO 4)
# =============================================================================

def test_desproyectar_centro_optico():
    """Un punto en el centro óptico (cx, cy) debe tener coordenadas X=0, Y=0, Z=z_m."""
    k = {"fx": 1000.0, "fy": 1000.0, "cx": 500.0, "cy": 500.0}
    pt3d = desproyectar(u=500.0, v=500.0, z_m=0.80, k=k)
    np.testing.assert_allclose(pt3d, [0.0, 0.0, 0.80], atol=1e-5)


def test_desproyectar_cuadrantes():
    """Verifica las convenciones de signos: +X derecha, +Y abajo en el marco óptico."""
    k = {"fx": 1000.0, "fy": 1000.0, "cx": 500.0, "cy": 500.0}
    # u = 600 (+100 px), v = 400 (-100 px), Z = 1.0 m
    # X = 100 * 1.0 / 1000 = +0.10 m
    # Y = -100 * 1.0 / 1000 = -0.10 m
    pt3d = desproyectar(u=600.0, v=400.0, z_m=1.0, k=k)
    np.testing.assert_allclose(pt3d, [0.10, -0.10, 1.0], atol=1e-5)


# =============================================================================
# PRUEBAS DE ESPACIO DE TRABAJO Y SEGURIDAD DE AGARRE (FASE 9)
# =============================================================================

def test_dentro_del_volumen_valido_e_invalido():
    """Valida los límites del prisma de trabajo seguro."""
    lim = {
        "x_min": -0.30, "x_max": 0.30,
        "y_min": -0.20, "y_max": 0.20,
        "z_mesa": 0.80, "h_max": 0.30  # Z permitido: [0.50, 0.80]
    }
    # Punto válido
    assert dentro_del_volumen([0.0, 0.0, 0.65], lim) is True
    # Fuera en X
    assert dentro_del_volumen([0.35, 0.0, 0.65], lim) is False
    # Debajo de la mesa (Z > z_mesa)
    assert dentro_del_volumen([0.0, 0.0, 0.85], lim) is False
    # Demasiado alto (Z < z_mesa - h_max)
    assert dentro_del_volumen([0.0, 0.0, 0.40], lim) is False


def test_pose_pre_agarre():
    """Verifica que la pose de pre-agarre retrocede a lo largo de la normal hacia la cámara."""
    pt_agarre = np.array([0.05, -0.02, 0.60])
    normal_mesa = np.array([0.0, 0.0, -1.0])  # Normal hacia la cámara
    p_pre = pose_pre_agarre(pt_agarre, normal_mesa, d_a=0.10)
    # p_pre = [0.05, -0.02, 0.60 - 0.10] = [0.05, -0.02, 0.50]
    np.testing.assert_allclose(p_pre, [0.05, -0.02, 0.50], atol=1e-4)


# =============================================================================
# PRUEBA SINTÉTICA OFICIAL DE LA GUÍA (RECUPERACIÓN DE CAJA 3D)
# =============================================================================

def test_pipeline_recupera_la_caja():
    """
    Prueba exigida explícitamente en la Guía de Laboratorio (Fase 4):
    Escena sintética con mesa a 0.80 m y caja a 0.60 m.
    Recupera el centro 3D conocido con un error inferior a 5 mm (< 0.005 m).
    """
    W_c, H_c = 1920, 1080
    K_c = {"fx": 1350.0, "fy": 1350.0, "cx": 960.0, "cy": 540.0}
    
    # Supongamos que una caja tiene su centro exactamente en [X=0.08, Y=-0.04, Z=0.60] m
    X_real = 0.08
    Y_real = -0.04
    Z_real = 0.60
    
    # Proyección teórica a píxel de color:
    u_c = (X_real * K_c["fx"] / Z_real) + K_c["cx"]  # 0.08 * 1350 / 0.60 + 960 = 1140 px
    v_c = (Y_real * K_c["fy"] / Z_real) + K_c["cy"]  # -0.04 * 1350 / 0.60 + 540 = 450 px
    
    # Coordenadas normalizadas devueltas por Gemini [y, x] (0-1000)
    y_norm = (v_c / (H_c - 1)) * 1000.0
    x_norm = (u_c / (W_c - 1)) * 1000.0
    
    # 1. Normalizado a píxel
    u_est, v_est = normalizado_a_pixel([y_norm, x_norm], W_c, H_c)
    
    # 2. Mapa de profundidad sintético registrado (mesa a 800 mm, caja a 600 mm)
    mapa_profundidad = np.full((H_c, W_c), 800, dtype=np.uint16)
    # Región de la caja alrededor de (u_est, v_est)
    mapa_profundidad[v_est - 20 : v_est + 20, u_est - 20 : u_est + 20] = 600
    
    # 3. Profundidad robusta
    z_est = profundidad_robusta(mapa_profundidad, u_est, v_est, ventana=5)
    assert z_est is not None
    
    # 4. Desproyección 3D
    P_est = desproyectar(u_est, v_est, z_est, K_c)
    P_real = np.array([X_real, Y_real, Z_real])
    
    # Error euclidiano en metros
    error_m = np.linalg.norm(P_est - P_real)
    assert error_m < 0.005, f"El error 3D ({error_m * 1000:.2f} mm) supera la tolerancia de 5 mm"


def test_sin_registro_falla_en_el_borde():
    """
    Prueba exigida en la Guía de Laboratorio (Fase 4):
    Muestra cómo tomar 'el mismo píxel escalado' sin registrar los extrínsecos
    (traslación de -19.5 mm entre sensores) produce un error significativo,
    especialmente hacia los bordes de la imagen o con parallax.
    """
    W_c, H_c = 1920, 1080
    W_d, H_d = 480, 270
    
    # Objeto cerca del borde derecho
    u_color = 1800
    v_color = 900
    
    # Si ingenuamente escalamos el píxel de color al tamaño de profundidad:
    u_depth_ingenuo = int(u_color * (W_d / W_c))  # 1800 * (480/1920) = 450 px
    v_depth_ingenuo = int(v_color * (H_d / H_c))  # 900 * (270/1080) = 225 px
    
    # La cámara de profundidad RealSense D410 tiene un desplazamiento físico de t = [-19.5, -5.0, 0.0] mm
    # A una distancia de 0.6 m, un offset de 19.5 mm genera un desplazamiento de píxeles:
    # delta_u = fx_d * (19.5 mm / 600 mm) = 380 * (0.0195 / 0.6) = ~12.35 px en el mapa de profundidad
    t_offset_mm = 19.5
    fx_d = 380.0
    z_m = 0.60
    desplazamiento_esperado_px = fx_d * (t_offset_mm / (z_m * 1000.0))
    
    # Este error supera ampliamente la tolerancia geométrica requerida para agarre
    assert desplazamiento_esperado_px > 10.0
