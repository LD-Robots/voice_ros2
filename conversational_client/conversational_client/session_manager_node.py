#!/usr/bin/env python3
"""
session_manager_node.py - Gestionează starea sesiunii și închiderea la "Goodbye"

Acest nod ascultă transcrierea și decide când să închidă sesiunea.
"""
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String
from conversational_interfaces.msg import Transcription

class SessionManagerNode(Node):
    def __init__(self):
        super().__init__('session_manager_node')
        
        # Subscriber la transcrierea de la server (pentru a detecta intentia de goodbye din text)
        self.transcription_sub = self.create_subscription(
            Transcription,
            '/transcription',
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
        """Verifică dacă textul conține cuvinte de goodbye."""
        text = msg.text.lower().strip()
        
        # Verificăm dacă userul a zis ceva de genul goodbye
        for kw in self.goodbye_keywords:
            if kw in text:
                self.get_logger().info(f'👋 Goodbye detected in text: "{kw}". Closing session.')
                
                # Trimite semnal de închidere
                end_msg = Bool()
                end_msg.data = True
                self.session_pub.publish(end_msg)
                return

def main(args=None):
    rclpy.init(args=args)
    node = SessionManagerNode()
    rclpy.spin(node)
    try:
        rclpy.shutdown()
    except:
        pass

if __name__ == '__main__':
    main()
