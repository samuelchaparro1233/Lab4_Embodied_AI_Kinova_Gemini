#!/bin/bash
source /opt/ros/jazzy/setup.bash
echo "=== ROS 2 DISTRO ==="
printenv ROS_DISTRO
echo "=== PYTHON CHECK ==="
python3 -c "
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import PointStamped, TransformStamped
from sensor_msgs.msg import Image
from std_msgs.msg import String
from tf2_ros import TransformBroadcaster
print('ROS 2 y rclpy: OK')
"
python3 -c "
import cv2
print('OpenCV:', cv2.__version__)
" 2>/dev/null || echo "OpenCV: NO instalado en Ubuntu (ejecutar: sudo apt install -y python3-opencv)"

python3 -c "
from cv_bridge import CvBridge
print('cv_bridge: OK')
" 2>/dev/null || echo "cv_bridge: NO instalado en Ubuntu (ejecutar: sudo apt install -y ros-jazzy-cv-bridge)"

python3 -c "
from google import genai
print('google-genai: OK')
" 2>/dev/null || echo "google-genai: NO instalado en Ubuntu (ejecutar: pip install --break-system-packages google-genai)"
