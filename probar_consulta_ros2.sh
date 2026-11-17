#!/bin/bash
source /opt/ros/jazzy/setup.bash
echo "[*] Enviando orden de detección por tópico ROS 2 /embodied/consulta..."
ros2 topic pub --once /embodied/consulta std_msgs/msg/String "{data: '{\"tarea\": \"propio\", \"escena\": \"semantica1\", \"objeto\": \"la caja de hamburguesa libre y segura para tomar desde arriba\"}'}"
