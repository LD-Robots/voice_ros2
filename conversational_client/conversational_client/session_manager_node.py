#!/usr/bin/env python3
"""
session_manager_node.py - Manages session state and closing on "Goodbye"

Acest nod ascultă transcrierea și decide când să închidă sesiunea.
"""
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String
from conversational_interfaces.msg import Transcription

class SessionManagerNode(Node):
    def __init__(self):
        super().__init__('session_manager_node')
        self.declare_parameter('transcription_topic', '/attended_transcription')
        transcription_topic = str(self.get_parameter('transcription_topic').value)
        
        # Subscriber la transcrierea de la server (pentru a detecta intentia de goodbye din text)
        self.transcription_sub = self.create_subscription(
            Transcription,
            transcription_topic,
            self.transcription_callback,
            10
        )
        
        # Publisher pentru controlul sesiunii
        self.session_pub = self.create_publisher(Bool, '/end_session_external', 10)
        
        # Cuvinte cheie pentru închidere
        self.goodbye_keywords = [
            "goodbye", "bye bye", "see you", "later", "shut down",
            "la revedere", "pa pa", "ne vedem", "opreste-te", "închide"
        ]
        
        self.get_logger().info('✅ Session Manager started. Listening for Goodbye...')

    def transcription_callback(self, msg: Transcription):
        """Check if text contains goodbye words."""
        text = msg.text.lower().strip()
        
        # Checkm dacă userul a zis ceva de genul goodbye
        for kw in self.goodbye_keywords:
            if kw in text:
                self.get_logger().info(f'👋 Goodbye detected in text: "{kw}". Closing session.')
                
                # Send semnal de închidere
                end_msg = Bool()
                end_msg.data = True
                self.session_pub.publish(end_msg)
                return

def main(args=None):
    rclpy.init(args=args)
    node = SessionManagerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:
            pass

if __name__ == '__main__':
    main()
