#!/usr/bin/env python3
"""
Bridge/Streamer RTSP a ROS 2 para la Cámara del Kinova Gen3.
Lee el flujo de video en vivo de la cámara del Kinova (192.168.1.10)
y lo publica en los tópicos estándar de ROS 2:
  - /camera/color/image_raw (sensor_msgs/Image)
  - /camera/color/camera_info (sensor_msgs/CameraInfo)
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from sensor_msgs.msg import CameraInfo, Image

ARCHIVO_CONFIG = Path("config_camara_kinova.json")


class KinovaCameraStreamer(Node):
    def __init__(self, ip: str = "192.168.1.10", fps: float = 15.0):
        super().__init__("kinova_camera_streamer")
        self.ip = ip
        self.fps = fps
        self.bridge = CvBridge()

        # Publicadores
        self.pub_color = self.create_publisher(Image, "/camera/color/image_raw", 10)
        self.pub_info = self.create_publisher(CameraInfo, "/camera/color/camera_info", 10)

        # Cargar parámetros intrínsecos de la cámara
        self.camera_info_msg = self._crear_camera_info()

        # Configurar conexión FFMPEG de baja latencia para RTSP
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
            "rtsp_transport;tcp|"
            "fflags;nobuffer|"
            "flags;low_delay|"
            "max_delay;0|"
            "stimeout;5000000|"
            "analyzeduration;100000|"
            "probesize;100000"
        )

        rtsp_url = f"rtsp://{self.ip}/color"
        self.get_logger().info(f"Conectando a stream RTSP de Kinova: {rtsp_url}...")
        self.cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not self.cap.isOpened():
            self.get_logger().warn(f"No se pudo abrir FFMPEG para {rtsp_url}. Intentando backend por defecto...")
            self.cap = cv2.VideoCapture(rtsp_url)

        if self.cap.isOpened():
            self.get_logger().info("[OK] Conectado exitosamente al stream de la cámara del Kinova.")
        else:
            self.get_logger().error(f"[ERROR] No se pudo conectar a {rtsp_url}. Verifica IP y cable de red.")

        # Timer de publicación periódica
        periodo_s = 1.0 / self.fps
        self.timer = self.create_timer(periodo_s, self._timer_callback)
        self.get_logger().info(f"Publicando en /camera/color/image_raw a {self.fps} FPS.")

    def _crear_camera_info(self) -> CameraInfo:
        msg = CameraInfo()
        msg.header.frame_id = "camera_color_optical_frame"
        msg.width = 1920
        msg.height = 1080
        msg.distortion_model = "plumb_bob"
        msg.d = [0.0, 0.0, 0.0, 0.0, 0.0]

        fx = 1346.8
        fy = 1346.8
        cx = 960.0
        cy = 540.0
        msg.k = [fx, 0.0, cx, 0.0, fy, cy, 0.0, 0.0, 1.0]
        msg.p = [fx, 0.0, cx, 0.0, 0.0, fy, cy, 0.0, 0.0, 0.0, 1.0, 0.0]
        return msg

    def _timer_callback(self):
        if not self.cap.isOpened():
            return

        ret, frame = self.cap.read()
        if not ret or frame is None:
            return

        stamp = self.get_clock().now().to_msg()

        # Publicar imagen
        try:
            msg_img = self.bridge.cv2_to_imgmsg(frame, encoding="bgr8")
            msg_img.header.stamp = stamp
            msg_img.header.frame_id = "camera_color_optical_frame"
            self.pub_color.publish(msg_img)

            self.camera_info_msg.header.stamp = stamp
            self.pub_info.publish(self.camera_info_msg)
        except Exception as e:
            self.get_logger().warn(f"Error convirtiendo imagen a ROS: {e}")

    def destroy_node(self):
        if hasattr(self, "cap") and self.cap.isOpened():
            self.cap.release()
        super().destroy_node()


def main():
    parser = argparse.ArgumentParser(description="Streamer RTSP Kinova a ROS 2")
    parser.add_argument("--ip", default="192.168.1.10", help="IP del robot Kinova")
    parser.add_argument("--fps", type=float, default=15.0, help="Tasa de publicación en FPS")
    args, unknown = parser.parse_known_args()

    rclpy.init()
    nodo = KinovaCameraStreamer(ip=args.ip, fps=args.fps)
    try:
        rclpy.spin(nodo)
    except Exception:
        pass
    finally:
        nodo.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
