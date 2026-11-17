#!/usr/bin/env python3
"""
Nodo de ROS 2 (Ruta B) para el Laboratorio IA 01B (Embodied AI).
Razonamiento Espacial, Localización 3D y Publicación TF2 con Gemini Robotics y Kinova Gen3.

Suscripciones:
  - /camera/color/image_raw (sensor_msgs/Image)
  - /camera/depth/image_raw (sensor_msgs/Image)
  - /embodied/consulta (std_msgs/String - JSON)

Publicaciones:
  - /embodied/punto_3d (geometry_msgs/PointStamped)
  - /embodied/resultado (std_msgs/String - JSON)
  - /tf (tf2_ros.TransformBroadcaster: objetivo_gemini_<i>)

Uso en ROS 2 Jazzy:
  python3 embodied_ros2_nodo.py --ros-args -p grupo:=G03 -p thinking:=low
"""

import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from embodied_comun import (
    PROMPTS,
    caja_a_pixeles,
    calcular_normal_mesa,
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

# -----------------------------------------------------------------------------
# DETECCIÓN Y COMPATIBILIDAD CON ROS 2 (rclpy)
# -----------------------------------------------------------------------------
try:
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
    from sensor_msgs.msg import Image as RosImage
    from std_msgs.msg import String as RosString
    from geometry_msgs.msg import PointStamped as RosPointStamped
    from geometry_msgs.msg import TransformStamped as RosTransformStamped
    from tf2_ros import TransformBroadcaster as RosTransformBroadcaster
    try:
        from cv_bridge import CvBridge
        HAVE_CV_BRIDGE = True
    except ImportError:
        HAVE_CV_BRIDGE = False
    ROS2_DISPONIBLE = True
except ImportError:
    ROS2_DISPONIBLE = False
    HAVE_CV_BRIDGE = False

    # Clases Mock para permitir instanciación, pruebas y desarrollo en entornos sin ROS 2 (ej. Windows)
    class Node:
        def __init__(self, node_name: str, **kwargs):
            self.node_name = node_name
            self._params = {}
            self._logger = self

        def declare_parameter(self, name: str, default_value: Any):
            self._params[name] = default_value

        def get_parameter(self, name: str):
            class _Param:
                def __init__(self, val):
                    self.value = val
            return _Param(self._params.get(name))

        def create_subscription(self, msg_type, topic, callback, qos_profile):
            return {"topic": topic, "callback": callback}

        def create_publisher(self, msg_type, topic, qos_profile):
            class _Pub:
                def __init__(self, topic):
                    self.topic = topic
                    self.published = []
                def publish(self, msg):
                    self.published.append(msg)
            return _Pub(topic)

        def get_clock(self):
            class _Clock:
                def now(self):
                    class _Time:
                        def to_msg(self):
                            class _Stamp:
                                sec = int(time.time())
                                nanosec = int((time.time() % 1) * 1e9)
                            return _Stamp()
                    return _Time()
            return _Clock()

        def info(self, msg: str):
            print(f"[ROS2 INFO] {msg}")

        def warn(self, msg: str):
            print(f"[ROS2 WARN] {msg}")

        def error(self, msg: str):
            print(f"[ROS2 ERROR] {msg}")

        def get_logger(self):
            return self

    class RosPointStamped:
        def __init__(self):
            class _Header:
                frame_id = ""
                stamp = None
            class _Point:
                x = 0.0
                y = 0.0
                z = 0.0
            self.header = _Header()
            self.point = _Point()

    class RosTransformStamped:
        def __init__(self):
            class _Header:
                frame_id = ""
                stamp = None
            class _Transform:
                class _Trans:
                    x = 0.0
                    y = 0.0
                    z = 0.0
                class _Rot:
                    x = 0.0
                    y = 0.0
                    z = 0.0
                    w = 1.0
                translation = _Trans()
                rotation = _Rot()
            self.header = _Header()
            self.child_frame_id = ""
            self.transform = _Transform()

    class RosString:
        def __init__(self):
            self.data = ""

    class RosImage:
        def __init__(self):
            self.header = None
            self.height = 0
            self.width = 0
            self.encoding = ""
            self.data = b""

    class RosTransformBroadcaster:
        def __init__(self, node):
            self.node = node
            self.transforms = []
        def sendTransform(self, transform):
            self.transforms.append(transform)

    class CvBridge:
        pass


def cargar_config_camara() -> dict:
    if ARCHIVO_CONFIG.exists():
        with open(ARCHIVO_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "color": {"width": 1920, "height": 1080, "intrinsics": {"fx": 1346.8, "fy": 1346.8, "cx": 960.0, "cy": 540.0}},
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
# CLASE PRINCIPAL: NodoEmbodied
# -----------------------------------------------------------------------------
class NodoEmbodied(Node):
    """
    Nodo de ROS 2 para Embodied AI con Gemini Robotics-ER 2 y Kinova Gen3.
    """

    def __init__(self):
        super().__init__("nodo_embodied")
        asegurar_directorios()
        self.config = cargar_config_camara()

        # Parámetros del nodo
        self.declare_parameter("grupo", "G03")
        self.declare_parameter("thinking", "low")
        self.declare_parameter("frame_id", "camera_color_optical_frame")
        self.declare_parameter("simulado", False)
        self.declare_parameter("sin_registro", False)
        self.declare_parameter("topic_color", "/camera/color/image_raw")
        self.declare_parameter("topic_depth", "/camera/depth/image_raw")
        self.declare_parameter("topic_consulta", "/embodied/consulta")

        self.grupo = self.get_parameter("grupo").value
        self.thinking = self.get_parameter("thinking").value
        self.frame_id = self.get_parameter("frame_id").value
        self.simulado = bool(self.get_parameter("simulado").value)
        self.sin_registro = bool(self.get_parameter("sin_registro").value)

        topic_color = self.get_parameter("topic_color").value
        topic_depth = self.get_parameter("topic_depth").value
        topic_consulta = self.get_parameter("topic_consulta").value

        # Bridge y TF Broadcaster
        if HAVE_CV_BRIDGE:
            self.bridge = CvBridge()
        else:
            self.bridge = None

        self.tf_broadcaster = RosTransformBroadcaster(self)

        # Publicadores
        self.pub_punto_3d = self.create_publisher(RosPointStamped, "/embodied/punto_3d", 10)
        self.pub_resultado = self.create_publisher(RosString, "/embodied/resultado", 10)

        # Estado interno de imágenes (protegido por cerrojo)
        self._lock = threading.Lock()
        self.ultima_img_color: Optional[np.ndarray] = None
        self.ultima_img_depth: Optional[np.ndarray] = None
        self.ultimo_stamp_color = None
        self.ultimo_stamp_depth = None

        # Suscripciones
        # En ROS 2 usamos QoS SensorData para imágenes en tiempo real
        if ROS2_DISPONIBLE:
            qos_sensor = QoSProfile(
                reliability=ReliabilityPolicy.BEST_EFFORT,
                history=HistoryPolicy.KEEP_LAST,
                depth=1,
            )
        else:
            qos_sensor = 10

        self.sub_color = self.create_subscription(
            RosImage, topic_color, self._cb_color, qos_sensor
        )
        self.sub_depth = self.create_subscription(
            RosImage, topic_depth, self._cb_depth, qos_sensor
        )
        self.sub_consulta = self.create_subscription(
            RosString, topic_consulta, self._cb_consulta, 10
        )

        self.get_logger().info(
            f"NodoEmbodied inicializado [Grupo={self.grupo}, Thinking={self.thinking}, Frame={self.frame_id}]"
        )
        self.get_logger().info(f"Suscrito a: {topic_color}, {topic_depth}, {topic_consulta}")

    # -------------------------------------------------------------------------
    # CALLBACKS DE SUSCRIPCIÓN
    # -------------------------------------------------------------------------
    def _cb_color(self, msg: RosImage):
        """Almacena el último cuadro de color sin bloquear el hilo principal."""
        try:
            if self.bridge and HAVE_CV_BRIDGE:
                img = self.bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
            else:
                img = self._decodificar_imagen_raw(msg, is_depth=False)
            with self._lock:
                self.ultima_img_color = img
                self.ultimo_stamp_color = getattr(msg.header, "stamp", None) if hasattr(msg, "header") else None
        except Exception as e:
            self.get_logger().warn(f"Error procesando imagen de color: {e}")

    def _cb_depth(self, msg: RosImage):
        """Almacena el último mapa de profundidad (16UC1 milímetros) sin bloquear."""
        try:
            if self.bridge and HAVE_CV_BRIDGE:
                img = self.bridge.imgmsg_to_cv2(msg, desired_encoding="passthrough")
            else:
                img = self._decodificar_imagen_raw(msg, is_depth=True)

            # Asegurar formato uint16 en milímetros
            if img is not None:
                if img.dtype == np.float32:
                    # Si viene en metros (32FC1), convertir a mm uint16
                    img = (img * 1000.0).astype(np.uint16)
                elif img.dtype != np.uint16:
                    img = img.astype(np.uint16)

            with self._lock:
                self.ultima_img_depth = img
                self.ultimo_stamp_depth = getattr(msg.header, "stamp", None) if hasattr(msg, "header") else None
        except Exception as e:
            self.get_logger().warn(f"Error procesando imagen de profundidad: {e}")

    def _cb_consulta(self, msg: RosString):
        """
        Callback para /embodied/consulta.
        CRÍTICO PARA LA ARQUITECTURA:
        La consulta a la API de Gemini (que puede tardar entre 0.5s y 3s)
        se despacha en un hilo de trabajo separado (_trabajar).
        Si se ejecutara de forma síncrona dentro del callback, bloquearía
        el executor de ROS 2, causando acumulación en las colas y pérdida
        de cuadros de /camera/color y /camera/depth.
        """
        raw_str = msg.data if hasattr(msg, "data") else str(msg)
        self.get_logger().info(f"Consulta recibida en /embodied/consulta: {raw_str}")
        hilo = threading.Thread(target=self._trabajar, args=(raw_str,), daemon=True)
        hilo.start()

    # -------------------------------------------------------------------------
    # TODO 5: PUBLICACIÓN DE PUNTO 3D Y TRANSFORMADAS TF2
    # -------------------------------------------------------------------------
    def _publicar(
        self,
        puntos_3d: List[np.ndarray],
        labels: Optional[List[str]] = None,
        frame_id: Optional[str] = None,
        stamp: Optional[Any] = None,
    ) -> Tuple[Optional[RosPointStamped], List[RosTransformStamped]]:
        """
        TODO 5 (Ruta B con ROS 2):
        Crea y publica un PointStamped en /embodied/punto_3d con las coordenadas
        del objeto principal (primer objeto).
        Emite un TransformStamped por cada objeto detectado hacia TF2 usando
        TransformBroadcaster, con child_frame_id 'objetivo_gemini_<i>' y rotación identidad.

        Parámetros:
            puntos_3d: Lista de vectores numpy [X, Y, Z] métricos en el marco óptico.
            labels: Lista opcional de etiquetas para los objetos.
            frame_id: Marco de referencia padre (por defecto self.frame_id).
            stamp: Timestamp ROS 2 (por defecto clock actual del nodo).

        Retorna:
            Tupla (msg_punto, lista_transforms)
        """
        if not puntos_3d or len(puntos_3d) == 0:
            self.get_logger().warn("[TODO 5] Lista de puntos 3D vacía; nada que publicar.")
            return None, []

        target_frame = frame_id if frame_id is not None else self.frame_id
        target_stamp = stamp
        if hasattr(target_stamp, "stamp"):
            target_stamp = target_stamp.stamp
        if target_stamp is None:
            clock = self.get_clock()
            target_stamp = clock.now().to_msg() if hasattr(clock, "now") else None

        # 1. PointStamped del primer objeto
        primer_pt = puntos_3d[0]
        msg_punto = RosPointStamped()
        if hasattr(msg_punto, "header"):
            msg_punto.header.frame_id = target_frame
            msg_punto.header.stamp = target_stamp
        if hasattr(msg_punto, "point"):
            msg_punto.point.x = float(primer_pt[0])
            msg_punto.point.y = float(primer_pt[1])
            msg_punto.point.z = float(primer_pt[2])

        self.pub_punto_3d.publish(msg_punto)
        self.get_logger().info(
            f"[TODO 5] Publicado PointStamped en /embodied/punto_3d: "
            f"[{primer_pt[0]:.3f}, {primer_pt[1]:.3f}, {primer_pt[2]:.3f}] en {target_frame}"
        )

        # 2. TransformStamped para cada objeto detectado (objetivo_gemini_<i>)
        transforms_enviados = []
        for i, pt in enumerate(puntos_3d):
            tf_msg = RosTransformStamped()
            if hasattr(tf_msg, "header"):
                tf_msg.header.frame_id = target_frame
                tf_msg.header.stamp = target_stamp
            tf_msg.child_frame_id = f"objetivo_gemini_{i}"

            # Traslación métrica (X, Y, Z)
            if hasattr(tf_msg, "transform"):
                tf_msg.transform.translation.x = float(pt[0])
                tf_msg.transform.translation.y = float(pt[1])
                tf_msg.transform.translation.z = float(pt[2])

                # Rotación Identidad (sin rotación respecto al marco óptico de la cámara)
                tf_msg.transform.rotation.x = 0.0
                tf_msg.transform.rotation.y = 0.0
                tf_msg.transform.rotation.z = 0.0
                tf_msg.transform.rotation.w = 1.0

            self.tf_broadcaster.sendTransform(tf_msg)
            transforms_enviados.append(tf_msg)
            self.get_logger().info(
                f"[TODO 5] TF broadcast: {target_frame} -> {tf_msg.child_frame_id} = "
                f"[{pt[0]:.3f}, {pt[1]:.3f}, {pt[2]:.3f}] m"
            )

        return msg_punto, transforms_enviados

    # -------------------------------------------------------------------------
    # HILO DE TRABAJO (Despacho asíncrono de consultas y tareas)
    # -------------------------------------------------------------------------
    def _trabajar(self, raw_data: str):
        """
        Procesa la consulta en segundo plano sin congelar las suscripciones de cámara.
        Maneja:
          - Tarea 'guardar': Guarda imágenes actuales en disco (formato idéntico a Ruta A).
          - Tareas VLM ('propio', 'ingenuo', 'cajas', 'agarre', etc.): Llama a Gemini / simula,
            realiza registro, desproyecta a 3D, publica en ROS 2 y guarda en resultados.csv.
        """
        t_inicio = time.time()
        try:
            solicitud = json.loads(raw_data)
        except Exception:
            solicitud = {"tarea": "propio", "escena": "desconocida", "objeto": raw_data}

        tarea = solicitud.get("tarea", "propio")
        escena = solicitud.get("escena", "escena_ros2")
        objeto = solicitud.get("objeto", "objeto")
        nota = solicitud.get("nota", "")

        # 1. Obtener los cuadros actuales
        with self._lock:
            img_color = self.ultima_img_color.copy() if self.ultima_img_color is not None else None
            img_depth = self.ultima_img_depth.copy() if self.ultima_img_depth is not None else None
            stamp_actual = self.ultimo_stamp_color

        # Si aún no han llegado mensajes por tópicos, intentar cargar de capturas/ o generar
        if img_color is None:
            color_file = DIR_CAPTURAS / f"{escena}_color.png"
            if color_file.exists():
                img_color = cv2.imread(str(color_file))
                self.get_logger().info(f"Uso de archivo local: {color_file}")
            else:
                img_color = np.zeros((1080, 1920, 3), dtype=np.uint8)
                cv2.rectangle(img_color, (150, 100), (1770, 980), (70, 70, 80), -1)
                cv2.putText(img_color, f"ROS2 Escena: {escena}", (200, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 2)

        if img_depth is None:
            depth_file = DIR_CAPTURAS / f"{escena}_depth.png"
            if depth_file.exists():
                img_depth = cv2.imread(str(depth_file), cv2.IMREAD_UNCHANGED)
                self.get_logger().info(f"Uso de archivo de profundidad local: {depth_file}")
            else:
                img_depth = np.full((270, 480), 750, dtype=np.uint16)

        # ---------------------------------------------------------------------
        # CASO 1: Tarea 'guardar' (Fase 2, Paso 3 de la Guía)
        # ---------------------------------------------------------------------
        if tarea == "guardar":
            color_path = DIR_CAPTURAS / f"{escena}_color.png"
            depth_path = DIR_CAPTURAS / f"{escena}_depth.png"
            meta_path = DIR_CAPTURAS / f"{escena}_meta.json"

            cv2.imwrite(str(color_path), img_color)
            cv2.imwrite(str(depth_path), img_depth)

            pixeles_validos = int(np.count_nonzero(img_depth > 0))
            pct_validos = round((pixeles_validos / img_depth.size) * 100.0, 1)

            meta = {
                "escena": escena,
                "timestamp": datetime.now().isoformat(),
                "ruta": "B_con_ros",
                "nota": nota,
                "color": {"width": img_color.shape[1], "height": img_color.shape[0]},
                "depth": {"width": img_depth.shape[1], "height": img_depth.shape[0]},
                "profundidad_valida_pct": pct_validos,
                "advertencia": "Profundidad < 60%" if pct_validos < 60.0 else "OK",
            }
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)

            self.get_logger().info(f"[+] Escena '{escena}' guardada exitosamente (Profundidad válida: {pct_validos}%)")
            res_str = RosString()
            res_str.data = json.dumps({"status": "guardado", "escena": escena, "pct_validos": pct_validos})
            self.pub_resultado.publish(res_str)
            return

        # ---------------------------------------------------------------------
        # CASO 2: Tarea de Razonamiento VLM / Consulta
        # ---------------------------------------------------------------------
        latencia_s = 0.0
        tokens_totales = 0
        tokens_pensamiento = 0
        modelo_nombre = "gemini-robotics-er-2-preview"
        respuesta_raw = ""

        # Verificar si usamos API o simulación
        api_key = obtener_api_key() or ""
        usar_sim = self.simulado or (not api_key)

        if usar_sim:
            self.get_logger().info("[*] Modo simulación o sin API key: generando respuesta sintética.")
            latencia_s = 0.52
            modelo_nombre = "sintetico_ros2"
            if tarea == "cajas":
                respuesta_raw = '[{"box_2d": [400, 400, 650, 600], "label": "caja 1"}]'
            elif tarea == "agarre":
                if "inseguro" in escena or "agarre_i" in escena:
                    respuesta_raw = "[]"
                else:
                    respuesta_raw = '[{"point": [524, 503], "label": "agarre seguro"}]'
            else:
                respuesta_raw = f'[{{"point": [524, 503], "label": "{objeto}"}}]'
        else:
            self.get_logger().info(f"[*] Consultando Gemini ({modelo_nombre}, thinking={self.thinking})...")
            try:
                from google import genai
                client = genai.Client(api_key=api_key)
                prompt_plantilla = PROMPTS.get(tarea, PROMPTS["propio"])
                prompt_texto = prompt_plantilla.replace("{objeto}", objeto)

                _, buffer = cv2.imencode(".png", img_color)
                imagen_bytes = buffer.tobytes()

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
                                thinking_config=genai.types.ThinkingConfig(
                                    thinking_budget=1024 if self.thinking == "high" else 0
                                ),
                            ),
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
                latencia_s = round(time.time() - t_inicio, 3)
            except Exception as e:
                self.get_logger().warn(f"Error consultando Gemini: {e}. Conmutando a sintético.")
                respuesta_raw = f'[{{"point": [524, 503], "label": "{objeto}"}}]'
                modelo_nombre = "error_fallback"
                latencia_s = round(time.time() - t_inicio, 3)

        # Registro de profundidad y desproyección
        H_c, W_c = img_color.shape[:2]
        K_c = self.config["color"]["intrinsics"]
        K_d = self.config["depth"]["intrinsics"]
        R = np.array(self.config["extrinsics_depth_to_color"]["rotation_matrix"])
        t_m = np.array(self.config["extrinsics_depth_to_color"]["translation_m"])

        if self.sin_registro:
            depth_registrada = cv2.resize(img_depth, (W_c, H_c), interpolation=cv2.INTER_NEAREST)
        else:
            depth_registrada = registrar_profundidad(img_depth, K_d, K_c, R, t_m, (H_c, W_c))

        # Parseo de puntos y desproyección 3D
        puntos_3d = []
        etiquetas = []
        u_px, v_px = None, None
        X_m, Y_m, Z_m = None, None, None
        es_valido_volumen = None
        distancia_3d_m = None

        try:
            data = extraer_json(respuesta_raw)
            if isinstance(data, list) and len(data) > 0 and "point" in data[0]:
                u_px, v_px = normalizado_a_pixel(data[0]["point"], W_c, H_c)
                for elem in data:
                    pt_norm = elem.get("point")
                    if pt_norm is not None:
                        u, v = normalizado_a_pixel(pt_norm, W_c, H_c)
                        z = profundidad_robusta(depth_registrada, u, v, ventana=5)
                        if z is not None:
                            p3d = desproyectar(u, v, z, K_c)
                            puntos_3d.append(p3d)
                            etiquetas.append(elem.get("label", objeto))

                if puntos_3d:
                    X_m, Y_m, Z_m = puntos_3d[0]
                    limites = self.config["workspace_limits_optical_frame"]
                    es_valido_volumen = dentro_del_volumen(puntos_3d[0], limites)
                    if len(puntos_3d) >= 2:
                        distancia_3d_m = float(np.linalg.norm(puntos_3d[0] - puntos_3d[1]))
        except Exception as e:
            self.get_logger().error(f"Error procesando geometría: {e}")

        # Ejecutar TODO 5: Publicar PointStamped y TF
        if puntos_3d:
            self._publicar(puntos_3d, etiquetas, frame_id=self.frame_id, stamp=stamp_actual)

        # Publicar resultado JSON en /embodied/resultado
        res_payload = {
            "tarea": tarea,
            "escena": escena,
            "objeto": objeto,
            "u": u_px,
            "v": v_px,
            "punto_3d": [X_m, Y_m, Z_m] if X_m is not None else None,
            "dentro_volumen": es_valido_volumen,
            "distancia_3d_m": distancia_3d_m,
            "latencia_s": latencia_s,
            "modelo": modelo_nombre,
        }
        res_str = RosString()
        res_str.data = json.dumps(res_payload)
        self.pub_resultado.publish(res_str)

        # Registrar en CSV (idéntico a Ruta A)
        fila = [
            datetime.now().isoformat(),
            self.grupo,
            escena,
            tarea,
            f'"{objeto}"',
            self.thinking,
            str(self.sin_registro),
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
            f'"ROS2: {nota}"' if nota else '"ROS2"',
        ]
        with open(ARCHIVO_RESULTADOS, "a", encoding="utf-8") as f:
            f.write(",".join(fila) + "\n")

        self.get_logger().info(f"[+] Proceso completado en {latencia_s}s. Publicado a /embodied/resultado y guardado en CSV.")

    # -------------------------------------------------------------------------
    # UTILIDAD: Decodificación alternativa sin cv_bridge si fuera necesario
    # -------------------------------------------------------------------------
    def _decodificar_imagen_raw(self, msg: RosImage, is_depth: bool = False) -> Optional[np.ndarray]:
        """Convierte mensaje sensor_msgs/Image a numpy sin depender de cv_bridge."""
        if not hasattr(msg, "data") or len(msg.data) == 0:
            return None
        h, w = msg.height, msg.width
        if is_depth:
            # 16UC1 (2 bytes por píxel)
            arr = np.frombuffer(msg.data, dtype=np.uint16)
            if arr.size == h * w:
                return arr.reshape((h, w))
            # Si viene en 32FC1
            arr_f = np.frombuffer(msg.data, dtype=np.float32)
            if arr_f.size == h * w:
                return (arr_f.reshape((h, w)) * 1000.0).astype(np.uint16)
        else:
            arr = np.frombuffer(msg.data, dtype=np.uint8)
            if arr.size == h * w * 3:
                return arr.reshape((h, w, 3))
        return None


# -----------------------------------------------------------------------------
# PUNTO DE ENTRADA PRINCIPAL
# -----------------------------------------------------------------------------
def main(args=None):
    if not ROS2_DISPONIBLE:
        print("=" * 70)
        print("[!] AVISO: 'rclpy' no está instalado en este entorno de Windows.")
        print("    Este script implementa el nodo oficial de ROS 2 Jazzy (Ruta B).")
        print("    Para ejecutarlo en el laboratorio (Ubuntu 24.04 / Jazzy):")
        print("      python3 embodied_ros2_nodo.py --ros-args -p grupo:=G03 -p thinking:=low")
        print("=" * 70)
        print("[*] Ejecutando autoverificación offline de TODO 5 (Mock ROS 2)...")
        nodo = NodoEmbodied()
        puntos_prueba = [np.array([0.05, -0.02, 0.65]), np.array([-0.10, 0.08, 0.70])]
        pt_msg, tfs = nodo._publicar(puntos_prueba)
        print(f"[OK] PointStamped generado: X={pt_msg.point.x:.3f}, Y={pt_msg.point.y:.3f}, Z={pt_msg.point.z:.3f}")
        print(f"[OK] TransformStamped generados ({len(tfs)}): {[t.child_frame_id for t in tfs]}")
        print("[OK] Verificación completada con éxito.")
        return

    rclpy.init(args=args)
    nodo = NodoEmbodied()
    try:
        rclpy.spin(nodo)
    except Exception:
        nodo.get_logger().info("NodoEmbodied detenido.")
    finally:
        nodo.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
