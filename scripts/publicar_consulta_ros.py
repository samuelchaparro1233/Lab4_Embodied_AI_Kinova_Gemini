#!/usr/bin/env python3
"""
Publica una consulta de prueba en el tópico /embodied/consulta
"""
import json
import sys
import rclpy
from rclpy.node import Node
from std_msgs.msg import String

def main():
    objeto = sys.argv[1] if len(sys.argv) > 1 else "la hoja de papel blanca"
    tarea = sys.argv[2] if len(sys.argv) > 2 else "puntos"

    rclpy.init()
    node = Node('cliente_consulta_test')
    pub = node.create_publisher(String, '/embodied/consulta', 10)

    msg = String()
    msg.data = json.dumps({"objeto": objeto, "tarea": tarea})

    print(f"[*] Publicando consulta en /embodied/consulta: {msg.data}")
    # Publicar un par de veces para asegurar entrega en QoS
    for _ in range(3):
        pub.publish(msg)
        rclpy.spin_once(node, timeout_sec=0.2)

    print("[OK] Consulta publicada.")
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()

