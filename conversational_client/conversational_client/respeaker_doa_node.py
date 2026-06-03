#!/usr/bin/env python3
"""
ReSpeaker DOA Node - Publishes Direction of Arrival (DOA) angle from ReSpeaker Mic Array.
Uses pyusb via the existing tuning.py script.
Publishes to: /doa_angle (std_msgs/Int32)
"""
import rclpy
from rclpy.node import Node
from std_msgs.msg import Int32
import sys
import os
import time

# Add tools directory to path to import tuning.py
TOOLS_DIR = os.path.join(os.path.dirname(__file__), '../../../../tools/usb_4_mic_array')
# For install directory vs src directory:
TOOLS_DIR_ALT = os.path.expanduser('~/voice_ros2/tools/usb_4_mic_array')

if os.path.exists(TOOLS_DIR_ALT):
    sys.path.append(TOOLS_DIR_ALT)
elif os.path.exists(TOOLS_DIR):
    sys.path.append(TOOLS_DIR)

try:
    from tuning import find as find_device
except ImportError as e:
    print(f"⚠️ Could not import tuning.py: {e}")
    find_device = None

class RespeakerDOANode(Node):
    def __init__(self):
        super().__init__('respeaker_doa_node')
        
        self.pub = self.create_publisher(Int32, '/doa_angle', 10)
        
        if not find_device:
            self.get_logger().error("tuning.py not found. Cannot read DOA.")
            return
            
        self.dev = find_device()
        if not self.dev:
            self.get_logger().error("ReSpeaker device not found on USB! Check permissions or connection.")
            return
            
        self.get_logger().info("✅ Connected to ReSpeaker USB for DOA tracking.")
        self.last_angle = -1
        
        # Timer to read DOA at 10Hz
        self.timer = self.create_timer(0.1, self.read_doa)

    def read_doa(self):
        try:
            angle = self.dev.direction
            msg = Int32()
            msg.data = angle
            self.pub.publish(msg)
            self.last_angle = angle
        except Exception as e:
            self.get_logger().error(f"Error reading DOA: {e}")

    def destroy_node(self):
        if hasattr(self, 'dev') and self.dev:
            try:
                self.dev.close()
            except Exception:
                pass
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)
    node = RespeakerDOANode()
    if getattr(node, 'dev', None):
        try:
            rclpy.spin(node)
        except KeyboardInterrupt:
            pass
        finally:
            node.destroy_node()
            rclpy.shutdown()
    else:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
