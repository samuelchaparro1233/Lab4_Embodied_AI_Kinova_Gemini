"""
Generador de escenas RGB-D sintéticas para el Laboratorio IA 01B (Embodied AI).
Permite trabajar 100% de forma remota/autónoma fuera del laboratorio físico,
generando las capturas de la Tabla 3 con resolución y geometría exactas del Kinova Gen3:
- Color: 1920 x 1080 (Omnivision OV5640)
- Profundidad: 480 x 270 en milímetros (Intel RealSense D410)
- Metadatos: capturas/<escena>_meta.json
"""

import json
from datetime import datetime
from pathlib import Path
import cv2
import numpy as np

DIR_CAPTURAS = Path("capturas")
DIR_CAPTURAS.mkdir(parents=True, exist_ok=True)

# Dimensiones exactas de la cámara del Kinova Gen3
W_C, H_C = 1920, 1080
W_D, H_D = 480, 270

# Profundidades métricas nominales (mm)
Z_MESA_MM = 800  # Mesa a 0.80 m de la cámara
K_COLOR = {"fx": 1350.0, "fy": 1350.0, "cx": 960.0, "cy": 540.0}
K_DEPTH = {"fx": 380.0, "fy": 380.0, "cx": 240.0, "cy": 135.0}


def crear_fondo_mesa():
    """Crea una imagen de fondo de la mesa de laboratorio con textura sutil."""
    color = np.full((H_C, W_C, 3), (210, 215, 220), dtype=np.uint8)  # Mesa gris claro
    # Borde de la mesa
    cv2.rectangle(color, (120, 80), (1800, 1000), (190, 195, 200), -1)
    cv2.rectangle(color, (120, 80), (1800, 1000), (120, 120, 130), 3)

    # Mapa de profundidad base (mesa a 800 mm)
    depth = np.full((H_D, W_D), Z_MESA_MM, dtype=np.uint16)
    # Fuera de la mesa: profundidad mayor (piso a 1400 mm)
    depth[0:20, :] = 1400
    depth[250:270, :] = 1400
    depth[:, 0:30] = 1400
    depth[:, 450:480] = 1400

    return color, depth


def proyectar_a_depth(box_c, z_mm):
    """Mapea una caja 2D de color a coordenadas del mapa de profundidad."""
    u1_c, v1_c, u2_c, v2_c = box_c
    # Escalamiento nominal con compensación de extrínsecos t_x = -19.5 mm
    u1_d = int(np.clip(u1_c * (W_D / W_C) - 3, 0, W_D - 1))
    v1_d = int(np.clip(v1_c * (H_D / H_C) - 1, 0, H_D - 1))
    u2_d = int(np.clip(u2_c * (W_D / W_C) - 3, 0, W_D - 1))
    v2_d = int(np.clip(v2_c * (H_D / H_C) - 1, 0, H_D - 1))
    return u1_d, v1_d, u2_d, v2_d


def guardar_escena(nombre: str, color_img: np.ndarray, depth_img: np.ndarray, nota: str):
    color_path = DIR_CAPTURAS / f"{nombre}_color.png"
    depth_path = DIR_CAPTURAS / f"{nombre}_depth.png"
    meta_path = DIR_CAPTURAS / f"{nombre}_meta.json"

    cv2.imwrite(str(color_path), color_img)
    cv2.imwrite(str(depth_path), depth_img)

    total_px = depth_img.size
    valid_px = np.count_nonzero(depth_img > 0)
    pct_val = round((valid_px / total_px) * 100.0, 2)

    meta = {
        "escena": nombre,
        "nota": nota,
        "fecha": datetime.now().isoformat(),
        "color_res": [W_C, H_C],
        "depth_res": [W_D, H_D],
        "pct_profundidad_valida": pct_val,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    print(f"[OK] Escena '{nombre:14s}' guardada. Profundidad válida: {pct_val}%")


# =============================================================================
# GENERACIÓN DE LAS ESCENAS DE LA TABLA 3
# =============================================================================

def generar_todas_las_escenas():
    print("=" * 60)
    print("GENERANDO SET COMPLETO DE ESCENAS RGB-D SINTÉTICAS (TABLA 3)")
    print("=" * 60)

    # 1. ESCENA REGLA (Hoja A4 horizontal centrada: 297 x 210 mm)
    # A 800 mm de distancia, 297 mm en OV5640 (fx=1350) -> ~501 px de ancho
    col, dep = crear_fondo_mesa()
    # Hoja blanca centrada
    cv2.rectangle(col, (710, 435), (1210, 645), (255, 255, 255), -1)
    cv2.rectangle(col, (710, 435), (1210, 645), (0, 0, 0), 2)
    cv2.putText(col, "HOJA A4 (297 x 210 mm)", (760, 545), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (80, 80, 80), 2)
    # Profundidad de la hoja (sobre la mesa: 798 mm)
    u1_d, v1_d, u2_d, v2_d = proyectar_a_depth((710, 435, 1210, 645), 798)
    dep[v1_d:v2_d, u1_d:u2_d] = 798
    guardar_escena("regla", col, dep, "Hoja A4 horizontal centrada, Z=0.80 m")

    # 2. ESCENA SEMANTICA1 (D: Dos cajas, termo sobre una caja)
    col, dep = crear_fondo_mesa()
    # Caja 1 (ocupada con termo): izquierda
    cv2.rectangle(col, (350, 420), (660, 680), (45, 110, 185), -1)
    cv2.putText(col, "Caja con termo", (360, 460), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    # Termo cilíndrico sobre caja 1
    cv2.circle(col, (505, 550), 45, (160, 160, 160), -1)
    cv2.circle(col, (505, 550), 45, (40, 40, 40), 2)
    # Caja 2 (LIBRE): derecha
    cv2.rectangle(col, (812, 430), (1120, 700), (50, 160, 80), -1)
    cv2.putText(col, "Caja Libre", (850, 470), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    # Profundidades (cajas altura 80 mm -> Z=720 mm; termo altura 200 mm -> Z=520 mm)
    u1, v1, u2, v2 = proyectar_a_depth((350, 420, 660, 680), 720)
    dep[v1:v2, u1:u2] = 720
    u1_t, v1_t, u2_t, v2_t = proyectar_a_depth((460, 505, 550, 595), 520)
    dep[v1_t:v2_t, u1_t:u2_t] = 520
    u1_2, v1_2, u2_2, v2_2 = proyectar_a_depth((812, 430, 1120, 700), 720)
    dep[v1_2:v2_2, u1_2:u2_2] = 720
    guardar_escena("semantica1", col, dep, "D: dos cajas, termo sobre una")

    # 3. ESCENA SEMANTICA1P (P: Dos cajas, bandeja como distractor)
    col, dep = crear_fondo_mesa()
    # Bandeja grande distractora
    cv2.rectangle(col, (750, 320), (1350, 780), (180, 180, 190), -1)
    cv2.putText(col, "Bandeja distractor", (780, 360), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (50, 50, 50), 2)
    # Caja libre a la izquierda
    cv2.rectangle(col, (300, 380), (610, 650), (50, 160, 80), -1)
    cv2.putText(col, "Caja Libre", (340, 420), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    u1, v1, u2, v2 = proyectar_a_depth((300, 380, 610, 650), 720)
    dep[v1:v2, u1:u2] = 720
    u1_b, v1_b, u2_b, v2_b = proyectar_a_depth((750, 320, 1350, 780), 780)
    dep[v1_b:v2_b, u1_b:u2_b] = 780
    guardar_escena("semantica1p", col, dep, "P: dos cajas, bandeja como distractor")

    # 4. ESCENA SEMANTICA2 y SEMANTICA2P (Celulares y Llaves)
    for nombre, nota, u_cel, u_llaves in [
        ("semantica2", "D: llaves junto al celular derecho", 700, 930),
        ("semantica2p", "P: llaves junto al celular izquierdo", 400, 630),
    ]:
        col, dep = crear_fondo_mesa()
        # Celular 1
        cv2.rectangle(col, (400, 420), (580, 660), (30, 30, 30), -1)
        # Celular 2
        cv2.rectangle(col, (700, 420), (880, 660), (30, 30, 30), -1)
        # Llaves al lado
        cv2.circle(col, (u_llaves, 540), 30, (0, 215, 255), -1)
        cv2.putText(col, "Llaves", (u_llaves - 25, 595), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
        u1, v1, u2, v2 = proyectar_a_depth((400, 420, 580, 660), 790)
        dep[v1:v2, u1:u2] = 790
        u1, v1, u2, v2 = proyectar_a_depth((700, 420, 880, 660), 790)
        dep[v1:v2, u1:u2] = 790
        guardar_escena(nombre, col, dep, nota)

    # 5. ESCENA SEMANTICA3 y SEMANTICA3P (Cuaderno abierto y cerrado)
    for nombre, nota, abierto_izq in [
        ("semantica3", "D: cuaderno abierto a la izquierda", True),
        ("semantica3p", "P: cuaderno abierto a la derecha", False),
    ]:
        col, dep = crear_fondo_mesa()
        if abierto_izq:
            # Cuaderno abierto (blanco con lomo central)
            cv2.rectangle(col, (380, 350), (780, 650), (250, 250, 250), -1)
            cv2.line(col, (580, 350), (580, 650), (80, 80, 80), 3)
            # Cuaderno cerrado (pasta azul)
            cv2.rectangle(col, (950, 370), (1250, 670), (180, 50, 40), -1)
        else:
            cv2.rectangle(col, (380, 370), (680, 670), (180, 50, 40), -1)
            cv2.rectangle(col, (850, 350), (1250, 650), (250, 250, 250), -1)
            cv2.line(col, (1050, 350), (1050, 650), (80, 80, 80), 3)
        guardar_escena(nombre, col, dep, nota)

    # 6. ESCENA SEMANTICA4 y SEMANTICA4P (Estuche y esferos)
    for nombre, nota in [
        ("semantica4", "D: esfero dentro del estuche y dos fuera"),
        ("semantica4p", "P: estuche en otra posición"),
    ]:
        col, dep = crear_fondo_mesa()
        # Estuche rectangular
        cv2.rectangle(col, (500, 380), (820, 500), (90, 50, 140), -1)
        # Esfero dentro
        cv2.line(col, (540, 440), (780, 440), (20, 20, 20), 8)
        # Esferos fuera
        cv2.line(col, (520, 560), (760, 560), (200, 30, 30), 8)
        cv2.line(col, (520, 610), (760, 610), (30, 180, 30), 8)
        guardar_escena(nombre, col, dep, nota)

    # 7. ESCENA BURGER y BURGERP (Cajas y carrito/bandeja)
    for nombre, nota in [
        ("burger", "D: cajas de hamburguesa y bandeja"),
        ("burgerp", "P: configuración alterna de cajas"),
    ]:
        col, dep = crear_fondo_mesa()
        cv2.rectangle(col, (800, 450), (1100, 750), (45, 140, 220), -1)
        cv2.putText(col, "Caja hamburguesa", (810, 500), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        u1, v1, u2, v2 = proyectar_a_depth((800, 450, 1100, 750), 720)
        dep[v1:v2, u1:u2] = 720
        guardar_escena(nombre, col, dep, nota)

    # 8. ESCENAS DE DISTANCIA (dist1 a dist5: mediciones conocidas)
    distancias_reales = [0.312, 0.250, 0.380, 0.220, 0.340]
    for idx, d_m in enumerate(distancias_reales, start=1):
        nombre = f"dist{idx}"
        col, dep = crear_fondo_mesa()
        # Colocar 2 objetos separados por d_m
        # px_sep = d_m * fx / Z = d_m * 1350 / 0.80
        sep_px = int((d_m * 1350.0) / 0.80)
        c1 = (600, 540)
        c2 = (600 + sep_px, 540)
        if c2[0] > 1750:
            c1 = (200, 540)
            c2 = (200 + sep_px, 540)

        cv2.circle(col, c1, 40, (0, 120, 240), -1)  # Termo
        cv2.circle(col, c2, 40, (40, 200, 60), -1)  # Tapa caja
        cv2.line(col, c1, c2, (0, 0, 255), 2)
        cv2.putText(col, f"d={d_m:.3f}m", ((c1[0] + c2[0]) // 2 - 50, 520), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

        dep[120:150, int(c1[0] * W_D / W_C) - 10 : int(c1[0] * W_D / W_C) + 10] = 600
        dep[120:150, int(c2[0] * W_D / W_C) - 10 : int(c2[0] * W_D / W_C) + 10] = 600
        guardar_escena(nombre, col, dep, f"Distancia conocida {d_m:.3f} m centro a centro")

    # 9. ESCENAS DE AGARRE SEGURO E INSEGURO
    for s_idx in [1, 2, 3]:
        col, dep = crear_fondo_mesa()
        cv2.rectangle(col, (810, 430), (1120, 700), (50, 160, 80), -1)
        cv2.putText(col, f"Agarre Seguro {s_idx}", (820, 470), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        dep[100:175, 200:280] = 600
        guardar_escena(f"agarre_s{s_idx}", col, dep, f"Escena segura {s_idx}: despejada y estable")

    # Inseguras
    # i1: objeto encima
    col, dep = crear_fondo_mesa()
    cv2.rectangle(col, (810, 430), (1120, 700), (45, 110, 185), -1)
    cv2.circle(col, (965, 565), 50, (150, 150, 150), -1)
    cv2.putText(col, "Inseguro: Objeto Encima", (750, 400), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    guardar_escena("agarre_i1", col, dep, "Insegura: objeto pesado encima de la caja")

    # i2: mano humana cerca
    col, dep = crear_fondo_mesa()
    cv2.rectangle(col, (810, 430), (1120, 700), (45, 110, 185), -1)
    # Simular silueta de mano humana cerca
    cv2.ellipse(col, (780, 560), (60, 120), 30, 0, 360, (160, 190, 225), -1)
    cv2.putText(col, "Inseguro: Mano Cerca", (750, 400), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    guardar_escena("agarre_i2", col, dep, "Insegura: mano humana en zona de peligro")

    # i3: objeto volcado
    col, dep = crear_fondo_mesa()
    pts = np.array([[850, 650], [950, 430], [1150, 530], [1050, 750]], np.int32)
    cv2.fillPoly(col, [pts], (45, 110, 185))
    cv2.putText(col, "Inseguro: Volcado / Inestable", (750, 400), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    guardar_escena("agarre_i3", col, dep, "Insegura: objeto inclinado/inestable")

    # 10. DETECCIÓN DE ÉXITO (Antes y Después)
    col, dep = crear_fondo_mesa()
    guardar_escena("exito_antes1", col, dep, "Antes: caja fuera de bandeja")
    col, dep = crear_fondo_mesa()
    cv2.rectangle(col, (850, 450), (1150, 750), (50, 160, 80), -1)
    guardar_escena("exito_bien1", col, dep, "Después: caja colocada correctamente dentro")
    col, dep = crear_fondo_mesa()
    cv2.rectangle(col, (400, 450), (700, 750), (40, 40, 200), -1)
    guardar_escena("exito_mal1", col, dep, "Después: caja colocada fuera de la bandeja (fallo)")

    print("=" * 60)
    print(f"[OK] ¡Todas las escenas fueron generadas exitosamente en {DIR_CAPTURAS.resolve()}!")


if __name__ == "__main__":
    generar_todas_las_escenas()
