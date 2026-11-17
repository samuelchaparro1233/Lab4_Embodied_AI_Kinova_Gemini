#!/usr/bin/env python3
"""
Captura de Color desde ROS 2 en WSL2
"""
import os
import sys
import time
import numpy as np
import cv2

try:
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
    from sensor_msgs.msg import Image
    from cv_bridge import CvBridge
except ImportError as e:
    print(f"[ERROR] Dependencias de ROS 2 no disponibles: {e}")
    sys.exit(1)


class CapturadorColorRos(Node):
    def __init__(self):
        super().__init__('capturador_color_ros')
        self.bridge = CvBridge()
        self.img_color = None

        qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )

        self.sub_color = self.create_subscription(
            Image,
            '/camera/color/image_raw',
            self.cb_color,
            qos
        )
        self.get_logger().info('Esperando fotograma de /camera/color/image_raw...')

    def cb_color(self, msg):
        if self.img_color is None:
            try:
                self.img_color = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
                self.get_logger().info(f'Color recibido con exito: shape={self.img_color.shape}')
            except Exception as e:
                self.get_logger().error(f'Error decodificando color: {e}')


def main():
    rclpy.init()
    node = CapturadorColorRos()
    start = time.time()
    timeout = 10.0

    while rclpy.ok() and node.img_color is None:
        rclpy.spin_once(node, timeout_sec=0.2)
        if time.time() - start > timeout:
            node.get_logger().warn('Timeout esperando /camera/color/image_raw.')
            break

    os.makedirs('capturas', exist_ok=True)

    if node.img_color is not None:
        cv2.imwrite('capturas/ros_color_test.png', node.img_color)
        print(f"[EXITO] Imagen de color guardada: capturas/ros_color_test.png (shape={node.img_color.shape})")
    else:
        print("[FALLO] No se recibio fotograma en el topico de color")

    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
