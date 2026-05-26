#!/usr/bin/env python3
"""
respeaker_doa_node.py - Extracts Direction of Arrival (DOA) from the ReSpeaker USB Mic Array
"""
import rclpy
from rclpy.node import Node
from std_msgs.msg import Int32
import usb.core
import usb.util
import struct

class RespeakerDoaNode(Node):
    def __init__(self):
        super().__init__('respeaker_doa_node')
        self.pub_doa = self.create_publisher(Int32, '/doa_angle', 10)
        
        # Connect to ReSpeaker
        self.dev = usb.core.find(idVendor=0x2886, idProduct=0x0018)
        if not self.dev:
            self.get_logger().error("ReSpeaker USB device not found! Is it plugged in?")
            return
            
        try:
            self.dev.set_configuration()
        except usb.core.USBError:
            pass
            
        self.get_logger().info("✅ Connected to ReSpeaker Mic Array for DOA tracking.")
        
        self.last_doa = -1
        # Poll 10 times per second
        self.polling_timer = self.create_timer(0.1, self.poll_doa)

    def poll_doa(self):
        if not self.dev:
            return
            
        try:
            # DOAANGLE has id=21, offset=0, type='int'
            cmd = 0x80 | 0 # offset
            cmd |= 0x40    # int type indicator
            
            response = self.dev.ctrl_transfer(
                usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
                0, cmd, 21, 8, 5000)
                
            response = struct.unpack(b'ii', response.tobytes())
            doa = response[0]
            
            # Publish only if it has changed to avoid spamming the topic
            if doa != self.last_doa:
                msg = Int32()
                msg.data = doa
                self.pub_doa.publish(msg)
                self.last_doa = doa
                
        except Exception as e:
            self.get_logger().debug(f"Error reading DOA from ReSpeaker: {e}")

def main(args=None):
    rclpy.init(args=args)
    node = RespeakerDoaNode()
    if not node.dev:
        node.destroy_node()
        rclpy.shutdown()
        return
        
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        # Clean up USB resources
        if node.dev:
            usb.util.dispose_resources(node.dev)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == '__main__':
    main()
