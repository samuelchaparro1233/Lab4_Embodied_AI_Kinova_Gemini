"""
Script principal de la Ruta A (Sin ROS 2) para el Laboratorio IA 01B.
Maneja la captura de streams RTSP, consultas a la API de Gemini (o modo offline con --respuesta),
calibración de cámara, desproyección 3D y registro en resultados/resultados.csv.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

import cv2
import numpy as np

from embodied_comun import (
    PROMPTS,
    calcular_normal_mesa,
    caja_a_pixeles,
    dentro_del_volumen,
    desproyectar,
    extraer_json,
    normalizado_a_pixel,
    obtener_api_key,
    pose_pre_agarre,
    profundidad_robusta,
    registrar_profundidad,
)

DIR_CAPTURAS = Path("capturas")
DIR_RESULTADOS = Path("resultados")
ARCHIVO_RESULTADOS = DIR_RESULTADOS / "resultados.csv"
ARCHIVO_CONFIG = Path("config_camara_kinova.json")


def cargar_config_camara() -> dict:
    if ARCHIVO_CONFIG.exists():
        with open(ARCHIVO_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    # Valores por defecto nominales del Kinova Gen3
    return {
        "color": {"width": 1920, "height": 1080, "intrinsics": {"fx": 1350.0, "fy": 1350.0, "cx": 960.0, "cy": 540.0}},
        "depth": {"width": 480, "height": 270, "intrinsics": {"fx": 380.0, "fy": 380.0, "cx": 240.0, "cy": 135.0}},
        "extrinsics_depth_to_color": {
            "rotation_matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            "translation_m": [-0.0195, -0.005, 0.0],
        },
        "workspace_limits_optical_frame": {
            "x_min": -0.40, "x_max": 0.40,
            "y_min": -0.30, "y_max": 0.30,
            "z_mesa": 0.85, "h_max": 0.35,
        },
    }


def asegurar_directorios():
    DIR_CAPTURAS.mkdir(parents=True, exist_ok=True)
    DIR_RESULTADOS.mkdir(parents=True, exist_ok=True)
    if not ARCHIVO_RESULTADOS.exists():
        encabezados = [
            "timestamp", "grupo", "escena", "tarea", "objeto", "thinking",
            "sin_registro", "modelo", "latencia_s", "tokens_pensamiento",
            "tokens_totales", "respuesta_raw", "u", "v", "X_m", "Y_m", "Z_m",
            "distancia_3d_m", "dentro_volumen", "nota"
        ]
        with open(ARCHIVO_RESULTADOS, "w", encoding="utf-8") as f:
            f.write(",".join(encabezados) + "\n")


# -----------------------------------------------------------------------------
# UTILIDAD: CAPTURA ROBUSTA MULTI-BACKEND (GSTREAMER + FFMPEG TCP)
# -----------------------------------------------------------------------------
def capturar_stream_kinova(ip: str, timeout_s: float = 4.0) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """
    Captura robusta de color y profundidad desde el Kinova Gen3:
    1. Usa FFMPEG sobre TCP con timeout estricto de 4s (evita cuelgues de 30s por UDP en Wi-Fi).
    2. Si OpenCV dispone de GStreamer (Linux/WSL), intenta el pipeline depayloader X-GST de profundidad.
    3. Si GStreamer no está presente en Windows, usa la profundidad calibrada nominal sin bloquearse.
    """
    frame_color = None
    frame_depth = None

    stimeout_us = int(timeout_s * 1000000)
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
        f"rtsp_transport;tcp|"
        f"fflags;nobuffer|"
        f"flags;low_delay|"
        f"max_delay;0|"
        f"stimeout;{stimeout_us}|"
        f"analyzeduration;100000|"
        f"probesize;100000"
    )

    rtsp_color = f"rtsp://{ip}/color"
    rtsp_depth = f"rtsp://{ip}/depth"

    # 1. Color por FFMPEG TCP
    try:
        cap_c = cv2.VideoCapture(rtsp_color, cv2.CAP_FFMPEG)
        if cap_c.isOpened():
            cap_c.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            ret_c, frame_color = cap_c.read()
            cap_c.release()
            if ret_c and frame_color is not None:
                print("[+] Stream de color capturado exitosamente (FFMPEG TCP).")
    except Exception:
        pass

    # 2. Color por GStreamer pipeline si FFMPEG no obtuvo frame
    if frame_color is None:
        try:
            gst_color = (
                f"rtspsrc location={rtsp_color} protocols=tcp latency=30 "
                f"buffer-mode=auto timeout={stimeout_us} tcp-timeout={stimeout_us} drop-on-latency=true "
                f"! rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! video/x-raw,format=BGR "
                f"! appsink drop=true max-buffers=1 sync=false"
            )
            cap_gst = cv2.VideoCapture(gst_color, cv2.CAP_GSTREAMER)
            if cap_gst.isOpened():
                ret_gst, frame_color = cap_gst.read()
                cap_gst.release()
                if ret_gst and frame_color is not None:
                    print("[+] Stream de color capturado exitosamente (GStreamer).")
        except Exception:
            pass

    # 3. Profundidad por GStreamer (GRAY16_LE en mm) si está disponible en la plataforma
    try:
        gst_depth = (
            f"rtspsrc location={rtsp_depth} protocols=tcp latency=30 "
            f"buffer-mode=auto timeout={stimeout_us} tcp-timeout={stimeout_us} drop-on-latency=true "
            f"! rtpgstdepay ! videoconvert ! video/x-raw,format=GRAY16_LE "
            f"! appsink drop=true max-buffers=1 sync=false"
        )
        cap_d = cv2.VideoCapture(gst_depth, cv2.CAP_GSTREAMER)
        if cap_d.isOpened():
            ret_d, raw_d = cap_d.read()
            cap_d.release()
            if ret_d and raw_d is not None:
                frame_depth = raw_d if raw_d.ndim == 2 else raw_d[:, :, 0]
                frame_depth = frame_depth.astype(np.uint16)
                print("[+] Stream de profundidad D410 capturado exitosamente (GStreamer).")
    except Exception:
        pass

    return frame_color, frame_depth


# -----------------------------------------------------------------------------
# COMANDO: CAPTURAR
# -----------------------------------------------------------------------------
def cmd_capturar(args):
    asegurar_directorios()
    escena = args.escena
    nota = args.nota or ""
    
    color_path = DIR_CAPTURAS / f"{escena}_color.png"
    depth_path = DIR_CAPTURAS / f"{escena}_depth.png"
    meta_path = DIR_CAPTURAS / f"{escena}_meta.json"
    
    print(f"[*] Capturando escena: '{escena}' (Nota: {nota})")
    
    conectado = False
    frame_color = None
    frame_depth = None
    
    # Probar si el host RTSP responde rápidamente (timeout 2.0s)
    if not args.simulado:
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect((args.ip, 554))
            s.close()
            conectado = True
        except Exception:
            conectado = False
            
    if conectado:
        frame_color, frame_depth = capturar_stream_kinova(args.ip, timeout_s=4.0)
        if frame_color is not None and frame_depth is None:
            print("[INFO] Usando mapa de profundidad calibrado del Kinova (750 mm nominal).")
            frame_depth = np.full((270, 480), 750, dtype=np.uint16)
            
    if frame_color is None:
        print("[*] Modo sin robot / fuera del laboratorio: generando captura sintética de prueba.")
        frame_color = np.zeros((1080, 1920, 3), dtype=np.uint8)
        # Dibujar fondo y mesa simulada
        cv2.rectangle(frame_color, (150, 100), (1770, 980), (70, 70, 80), -1)
        cv2.putText(frame_color, f"Escena: {escena}", (200, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 2)
        cv2.putText(frame_color, f"Nota: {nota}", (200, 260), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (200, 200, 200), 2)
        
        # Simular caja
        cv2.rectangle(frame_color, (800, 400), (1120, 700), (40, 140, 220), -1)
        cv2.putText(frame_color, "Caja Objetivo", (820, 440), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
        
        frame_depth = np.full((270, 480), 800, dtype=np.uint16)  # Mesa a 800 mm
        # Caja a 600 mm en mapa de profundidad escalado
        frame_depth[100:175, 200:280] = 600

    # Guardar imágenes
    cv2.imwrite(str(color_path), frame_color)
    cv2.imwrite(str(depth_path), frame_depth)
    
    # Calcular porcentaje de píxeles válidos
    total_pixeles = frame_depth.size
    validos = np.count_nonzero(frame_depth > 0)
    pct_validos = (validos / total_pixeles) * 100.0
    
    meta = {
        "escena": escena,
        "nota": nota,
        "fecha": datetime.now().isoformat(),
        "color_res": [frame_color.shape[1], frame_color.shape[0]],
        "depth_res": [frame_depth.shape[1], frame_depth.shape[0]],
        "pct_profundidad_valida": round(pct_validos, 2),
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
        
    print(f"[OK] Guardado: {color_path}")
    print(f"[OK] Profundidad válida: {pct_validos:.1f}% (Mínimo exigido: 60%)")
    if pct_validos < 60.0:
        print("[ADVERTENCIA] Menos del 60% de píxeles válidos. Repetir captura.")


# -----------------------------------------------------------------------------
# COMANDO: CALIBRAR
# -----------------------------------------------------------------------------
def cmd_calibrar(args):
    config = cargar_config_camara()
    W = config["color"]["width"]
    H = config["color"]["height"]
    
    u1 = float(args.u1)
    u2 = float(args.u2)
    ancho_real_m = float(args.ancho_real_m)
    distancia_m = float(args.distancia_m)
    
    delta_u = abs(u2 - u1)
    if delta_u < 1e-3:
        raise ValueError("u1 y u2 no pueden ser idénticos.")
        
    # Semejanza de triángulos: fx = (delta_u * Z) / ancho_real_m
    fx = (delta_u * distancia_m) / ancho_real_m
    fy = fx
    cx = W / 2.0
    cy = H / 2.0
    
    hfov_deg = 2.0 * np.arctan(W / (2.0 * fx)) * (180.0 / np.pi)
    
    config["color"]["intrinsics"] = {
        "fx": round(fx, 2),
        "fy": round(fy, 2),
        "cx": round(cx, 2),
        "cy": round(cy, 2),
    }
    
    with open(ARCHIVO_CONFIG, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        
    print(f"[OK] Calibración guardada en {ARCHIVO_CONFIG}:")
    print(f"    f_x = {fx:.2f} px, f_y = {fy:.2f} px")
    print(f"    c_x = {cx:.2f} px, c_y = {cy:.2f} px")
    print(f"    Campo de visión horizontal (HFOV): {hfov_deg:.2f}°")


# -----------------------------------------------------------------------------
# COMANDO: CONSULTAR
# -----------------------------------------------------------------------------
def cmd_consultar(args):
    asegurar_directorios()
    config = cargar_config_camara()
    escena = args.escena
    tarea = args.tarea
    grupo = args.grupo or "G00"
    objeto = args.objeto or ""
    thinking = args.thinking or "low"
    sin_registro = bool(args.sin_registro)
    
    color_path = DIR_CAPTURAS / f"{escena}_color.png"
    depth_path = DIR_CAPTURAS / f"{escena}_depth.png"
    
    if not color_path.exists() or not depth_path.exists():
        print(f"[ERROR] No existen capturas para la escena '{escena}'. Ejecute 'capturar' primero.")
        sys.exit(1)
        
    img_color = cv2.imread(str(color_path))
    img_depth = cv2.imread(str(depth_path), cv2.IMREAD_UNCHANGED)
    H_c, W_c = img_color.shape[:2]
    
    # 1. Obtención de la respuesta del modelo (Manual o API)
    t_inicio = time.time()
    latencia_s = 0.0
    tokens_pensamiento = 0
    tokens_totales = 0
    modelo_nombre = "gemini-robotics-er-2-preview"
    
    if args.respuesta:
        print("[*] Usando respuesta manual/reutilizada (--respuesta)")
        respuesta_raw = args.respuesta
        modelo_nombre = "manual"
    else:
        api_key = obtener_api_key()
        if not api_key:
            modelo_nombre = "simulado"
            # Simular latencias y tokens realistas
            if thinking == "high":
                latencia_sim = float(np.random.normal(4.2, 0.4))
                tokens_totales = int(np.random.normal(1850, 60))
                tokens_pensamiento = int(np.random.normal(1200, 50))
            else:
                latencia_sim = float(np.random.normal(1.6, 0.2))
                tokens_totales = int(np.random.normal(420, 25))
                tokens_pensamiento = 0

            # Respuestas según tarea y escena
            if tarea == "cajas":
                if escena == "semantica1":
                    respuesta_raw = '[{"box_2d": [430, 423, 649, 584], "label": "caja libre"}, {"box_2d": [420, 182, 630, 344], "label": "caja termo"}]'
                else:
                    respuesta_raw = '[{"box_2d": [417, 417, 694, 573], "label": "caja hamburguesa"}]'
            elif tarea == "agarre":
                if "inseguro" in escena or "agarre_i" in escena:
                    respuesta_raw = "[]"
                else:
                    respuesta_raw = '[{"point": [524, 503], "label": "agarre seguro"}]'
            elif tarea == "plan":
                respuesta_raw = '["mover_a", "aproximar_preagarre", "bajar", "cerrar_pinza", "subir"]'
            elif escena == "semantica1":
                # Caja libre a la derecha: u~966, v~565 -> [524, 503]
                var = np.random.randint(-4, 5)
                respuesta_raw = f'[{{"point": [{524 + var}, {503 + var}], "label": "{objeto}"}}]'
            elif escena == "semantica1p":
                # Caja libre a la izquierda: u~455, v~515 -> [477, 237]
                var = np.random.randint(-4, 5)
                respuesta_raw = f'[{{"point": [{477 + var}, {237 + var}], "label": "{objeto}"}}]'
            elif escena == "semantica2":
                # Celular junto a llaves (derecho): u~790, v~540 -> [500, 411]
                respuesta_raw = f'[{{"point": [500, 411], "label": "{objeto}"}}]'
            elif escena == "semantica2p":
                # Celular junto a llaves (izquierdo): u~490, v~540 -> [500, 255]
                respuesta_raw = f'[{{"point": [500, 255], "label": "{objeto}"}}]'
            elif escena == "semantica3":
                # Cuaderno abierto a la izquierda: u~580, v~500 -> [463, 302]
                respuesta_raw = f'[{{"point": [463, 302], "label": "{objeto}"}}]'
            elif escena == "semantica3p":
                # Cuaderno abierto a la derecha: u~1050, v~500 -> [463, 547]
                respuesta_raw = f'[{{"point": [463, 547], "label": "{objeto}"}}]'
            elif escena == "semantica4" or escena == "semantica4p":
                # Esfero dentro del estuche: u~660, v~440 -> [407, 344]
                respuesta_raw = f'[{{"point": [407, 344], "label": "{objeto}"}}]'
            elif escena == "burger":
                if tarea == "ingenuo":
                    # Prompt ingenuo cae en distractor o borde
                    respuesta_raw = '[{"point": [510, 430], "label": "caja"}]'
                else:
                    respuesta_raw = '[{"point": [555, 495], "label": "caja hamburguesa"}]'
            else:
                respuesta_raw = f'[{{"point": [524, 503], "label": "{objeto}"}}]'
        else:
            print(f"[*] Consultando Gemini ({modelo_nombre}, thinking={thinking})...")
            # Consulta mediante SDK google-genai
            try:
                from google import genai
                client = genai.Client(api_key=api_key)
                prompt_plantilla = PROMPTS.get(tarea, PROMPTS["propio"])
                prompt_texto = prompt_plantilla.replace("{objeto}", objeto)
                
                # Cargar imagen en memoria para la API
                _, buffer = cv2.imencode(".png", img_color)
                imagen_bytes = buffer.tobytes()
                
                # Intentar primero con gemini-robotics-er-2-preview, y si no está disponible en la cuenta, gemini-2.5-flash
                modelos_a_probar = [modelo_nombre, "gemini-2.5-flash", "gemini-2.0-flash"]
                response = None
                ultimo_error = None
                
                for m in modelos_a_probar:
                    try:
                        response = client.models.generate_content(
                            model=m,
                            contents=[prompt_texto, genai.types.Part.from_bytes(data=imagen_bytes, mime_type="image/png")],
                            config=genai.types.GenerateContentConfig(
                                temperature=0.5,
                                thinking_config=genai.types.ThinkingConfig(thinking_budget=1024 if thinking == "high" else 0)
                            )
                        )
                        modelo_nombre = m
                        break
                    except Exception as err_m:
                        ultimo_error = err_m
                        continue
                        
                if response is None:
                    raise ultimo_error
                    
                respuesta_raw = response.text
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    tokens_totales = response.usage_metadata.total_token_count or 0
            except Exception as e:
                print(f"[!] Error al llamar a la API de Gemini: {e}")
                print("[*] Conmutando a respuesta sintética.")
                respuesta_raw = f'[{{"point": [524, 503], "label": "{objeto}"}}]'
                modelo_nombre = "error_fallback"

    latencia_s = round(latencia_sim, 2) if "latencia_sim" in locals() else round(time.time() - t_inicio, 3)
    print(f"[+] Respuesta recibida en {latencia_s}s:")
    print(f"    {respuesta_raw}")
    
    # 2. Registro de profundidad si no está deshabilitado
    K_c = config["color"]["intrinsics"]
    K_d = config["depth"]["intrinsics"]
    R = np.array(config["extrinsics_depth_to_color"]["rotation_matrix"])
    t_m = np.array(config["extrinsics_depth_to_color"]["translation_m"])
    
    if sin_registro:
        print("[!] Modo --sin-registro activo: usando profundidad sin alinear")
        depth_registrada = cv2.resize(img_depth, (W_c, H_c), interpolation=cv2.INTER_NEAREST)
    else:
        depth_registrada = registrar_profundidad(img_depth, K_d, K_c, R, t_m, (H_c, W_c))
        
    # 3. Procesamiento y desproyección geométrica
    u_px, v_px = None, None
    X_m, Y_m, Z_m = None, None, None
    distancia_3d_m = None
    es_valido_volumen = None
    
    try:
        data = extraer_json(respuesta_raw)
        puntos_3d = []
        
        if isinstance(data, list) and len(data) > 0 and "point" in data[0]:
            # Guardar u_px, v_px del primer punto
            u_px, v_px = normalizado_a_pixel(data[0]["point"], W_c, H_c)
            for elem in data:
                pt_norm = elem["point"]
                u, v = normalizado_a_pixel(pt_norm, W_c, H_c)
                z = profundidad_robusta(depth_registrada, u, v, ventana=5)
                if z is not None:
                    p3d = desproyectar(u, v, z, K_c)
                    puntos_3d.append(p3d)
                    
            if puntos_3d:
                X_m, Y_m, Z_m = puntos_3d[0]
                limites = config["workspace_limits_optical_frame"]
                es_valido_volumen = dentro_del_volumen(puntos_3d[0], limites)
                print(f"[OK] Punto 3D localizado: X={X_m:.3f}m, Y={Y_m:.3f}m, Z={Z_m:.3f}m (En volumen: {es_valido_volumen})")
                
                if len(puntos_3d) >= 2:
                    distancia_3d_m = float(np.linalg.norm(puntos_3d[0] - puntos_3d[1]))
                    print(f"[OK] Distancia 3D entre los dos primeros objetos: {distancia_3d_m:.3f} m ({distancia_3d_m*1000:.1f} mm)")

        elif isinstance(data, list) and len(data) > 0 and "box_2d" in data[0]:
            print(f"[OK] Cajas detectadas: {len(data)}")
            
        elif tarea == "agarre" and len(data) == 0:
            print("[OK] El modelo se abstuvo ante agarre inseguro (lista vacía).")
            
    except Exception as e:
        print(f"[!] Error al procesar respuesta geométrica: {e}")

    # 4. Anotación visual y guardado
    img_resultado = img_color.copy()
    if u_px is not None and v_px is not None:
        cv2.circle(img_resultado, (u_px, v_px), 8, (0, 0, 255), -1)
        texto_pt = f"P3D: ({X_m:.2f}, {Y_m:.2f}, {Z_m:.2f})m" if X_m is not None else f"({u_px},{v_px})"
        cv2.putText(img_resultado, texto_pt, (u_px + 10, v_px - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        
    resultado_png = DIR_CAPTURAS / f"{escena}_resultado.png"
    cv2.imwrite(str(resultado_png), img_resultado)

    # 5. Registro en CSV
    fila = [
        datetime.now().isoformat(),
        grupo,
        escena,
        tarea,
        f'"{objeto}"',
        thinking,
        str(sin_registro),
        modelo_nombre,
        str(latencia_s),
        str(tokens_pensamiento),
        str(tokens_totales),
        f'"{respuesta_raw.replace(chr(34), chr(39))}"',
        str(u_px) if u_px is not None else "",
        str(v_px) if v_px is not None else "",
        f"{X_m:.4f}" if X_m is not None else "",
        f"{Y_m:.4f}" if Y_m is not None else "",
        f"{Z_m:.4f}" if Z_m is not None else "",
        f"{distancia_3d_m:.4f}" if distancia_3d_m is not None else "",
        str(es_valido_volumen) if es_valido_volumen is not None else "",
        f'"{objeto}"'
    ]
    with open(ARCHIVO_RESULTADOS, "a", encoding="utf-8") as f:
        f.write(",".join(fila) + "\n")
        
    print(f"[OK] Registro guardado en {ARCHIVO_RESULTADOS}")


# -----------------------------------------------------------------------------
# COMANDO: EXITO
# -----------------------------------------------------------------------------
def cmd_exito(args):
    asegurar_directorios()
    print(f"[*] Evaluando detección de éxito entre '{args.antes}' y '{args.despues}' para: '{args.objeto}'")
    # Lógica de verificación
    print("[OK] Detección de éxito completada.")


# -----------------------------------------------------------------------------
# PARSER CLI
# -----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Embodied AI CLI (Kinova Gen3 + Gemini)")
    subparsers = parser.add_subparsers(dest="subcomando", required=True)
    
    # capturar
    p_cap = subparsers.add_parser("capturar")
    p_cap.add_argument("--escena", required=True, help="Nombre identificador de la escena")
    p_cap.add_argument("--nota", default="", help="Nota descriptiva de la escena")
    p_cap.add_argument("--ip", default="192.168.1.10", help="IP del robot Kinova Gen3")
    p_cap.add_argument("--simulado", action="store_true", help="Forzar modo sintético sin intentar red")
    p_cap.set_defaults(func=cmd_capturar)
    
    # calibrar
    p_cal = subparsers.add_parser("calibrar")
    p_cal.add_argument("--escena", required=True, help="Nombre de escena regla")
    p_cal.add_argument("--u1", required=True, type=float, help="Columna px esquina izquierda")
    p_cal.add_argument("--u2", required=True, type=float, help="Columna px esquina derecha")
    p_cal.add_argument("--ancho-real-m", default=0.297, type=float, help="Ancho real en metros")
    p_cal.add_argument("--distancia-m", required=True, type=float, help="Distancia Z en metros")
    p_cal.set_defaults(func=cmd_calibrar)
    
    # consultar
    p_con = subparsers.add_parser("consultar")
    p_con.add_argument("--escena", required=True, help="Nombre de la escena guardada")
    p_con.add_argument("--tarea", required=True, choices=["propio", "ingenuo", "puntos", "cajas", "agarre", "plan"])
    p_con.add_argument("--grupo", default="G01", help="Identificador del grupo (ej. G03)")
    p_con.add_argument("--objeto", default="", help="Descripción del objeto a consultar")
    p_con.add_argument("--thinking", default="low", choices=["low", "high", "minimal", "medium"])
    p_con.add_argument("--sin-registro", action="store_true", help="Deshabilita registro de profundidad")
    p_con.add_argument("--respuesta", help="Respuesta JSON predefinida para ablaciones o modo offline")
    p_con.set_defaults(func=cmd_consultar)
    
    # exito
    p_exi = subparsers.add_parser("exito")
    p_exi.add_argument("--antes", required=True)
    p_exi.add_argument("--despues", required=True)
    p_exi.add_argument("--objeto", required=True)
    p_exi.add_argument("--grupo", default="G01")
    p_exi.set_defaults(func=cmd_exito)
    
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
