#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool
from conversational_interfaces.msg import WakeWord
import time
import sys

# Text colors for clean formatting
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

class IntegrationTestNode(Node):
    def __init__(self):
        super().__init__('integration_test_node')
        
        # State variables
        self.session_active = None
        self.conversation_paused = None
        self.session_active_updates = []
        self.conversation_pause_updates = []

        # Subscriptions
        self.session_sub = self.create_subscription(
            Bool,
            '/voice/session_active',
            self.session_callback,
            10
        )
        self.pause_sub = self.create_subscription(
            Bool,
            '/voice/conversation_pause',
            self.pause_callback,
            10
        )

        # Publishers
        self.wake_word_pub = self.create_publisher(
            WakeWord,
            '/voice/wake_word',
            10
        )
        self.pause_pub = self.create_publisher(
            Bool,
            '/voice/conversation_pause',
            10
        )

        self.get_logger().info("Integration Test Node Initialized.")

    def session_callback(self, msg: Bool):
        self.session_active = msg.data
        self.session_active_updates.append(msg.data)
        self.get_logger().info(f"Received /voice/session_active: {msg.data}")

    def pause_callback(self, msg: Bool):
        self.conversation_paused = msg.data
        self.conversation_pause_updates.append(msg.data)
        self.get_logger().info(f"Received /voice/conversation_pause: {msg.data}")

    def run_tests(self):
        print(f"\n{BOLD}{CYAN}=================================================={RESET}")
        print(f"{BOLD}{CYAN}      ROS 2 Voice Pipeline Integration Test       {RESET}")
        print(f"{BOLD}{CYAN}=================================================={RESET}\n")

        # Step 0: Ensure the system is in a clean starting state
        print(f"{BOLD}[*] Step 0: Initial State Check...{RESET}")
        self.spin_once(1.0)
        print(f"    - Session Active: {self.session_active}")
        print(f"    - Conversation Paused: {self.conversation_paused}")

        # Step 1: Test Wake Word Detection
        print(f"\n{BOLD}[*] Step 1: Triggering 'hello_robot' Wake Word...{RESET}")
        ww_msg = WakeWord()
        ww_msg.word = "hello_robot"
        ww_msg.score = 0.85
        self.wake_word_pub.publish(ww_msg)
        
        # Wait up to 5 seconds for session to activate
        success = self.wait_for_condition(lambda: self.session_active is True, timeout=5.0)
        if success:
            print(f"    {GREEN}✓ SUCCESS: Session activated correctly via wake word.{RESET}")
        else:
            print(f"    {RED}✗ FAILED: Session did not activate within 5 seconds.{RESET}")
            self.fail_exit()

        # Step 2: Test Pause State Activation
        print(f"\n{BOLD}[*] Step 2: Activating Conversation Pause...{RESET}")
        pause_msg = Bool()
        pause_msg.data = True
        self.pause_pub.publish(pause_msg)

        success = self.wait_for_condition(lambda: self.conversation_paused is True, timeout=3.0)
        if success:
            print(f"    {GREEN}✓ SUCCESS: Conversation pause state set to True.{RESET}")
        else:
            print(f"    {RED}✗ FAILED: Conversation did not transition to paused.{RESET}")
            self.fail_exit()

        # Step 3: Test Timeout Bypass during Pause
        wait_time = 15.0
        print(f"\n{BOLD}[*] Step 3: Testing Timeout Bypass during Pause...{RESET}")
        print(f"    - Waiting for {wait_time} seconds (VAD timeout should be ignored)...")
        
        # Spin for wait_time seconds and check if session stays active
        for i in range(int(wait_time)):
            self.spin_once(1.0)
            if self.session_active is False:
                print(f"    {RED}✗ FAILED: Session closed prematurely during pause at second {i+1}!{RESET}")
                self.fail_exit()
        
        print(f"    {GREEN}✓ SUCCESS: Session remained active during pause (timeout bypassed).{RESET}")

        # Step 4: Test Resume from Pause
        print(f"\n{BOLD}[*] Step 4: Resuming Conversation...{RESET}")
        resume_msg = Bool()
        resume_msg.data = False
        self.pause_pub.publish(resume_msg)

        success = self.wait_for_condition(lambda: self.conversation_paused is False, timeout=3.0)
        if success:
            print(f"    {GREEN}✓ SUCCESS: Conversation pause state reset to False.{RESET}")
        else:
            print(f"    {RED}✗ FAILED: Conversation did not resume.{RESET}")
            self.fail_exit()

        # Step 5: Test Standard Inactivity Timeout (After Resume)
        print(f"\n{BOLD}[*] Step 5: Testing Standard Inactivity Timeout...{RESET}")
        print(f"    - Waiting for session inactivity timeout (up to 35 seconds of silence)...")
        
        success = self.wait_for_condition(lambda: self.session_active is False, timeout=35.0)
        if success:
            print(f"    {GREEN}✓ SUCCESS: Session timed out and closed automatically.{RESET}")
            print(f"    {GREEN}✓ SUCCESS: Conversation pause state was reset: {self.conversation_paused}{RESET}")
        else:
            print(f"    {RED}✗ FAILED: Session did not timeout after 35 seconds of silence.{RESET}")
            self.fail_exit()

        print(f"\n{BOLD}{GREEN}=================================================={RESET}")
        print(f"{BOLD}{GREEN}     ALL TESTS COMPLETED SUCCESSFULLY!            {RESET}")
        print(f"{BOLD}{GREEN}=================================================={RESET}\n")

    def spin_once(self, duration=0.1):
        start = time.time()
        while time.time() - start < duration:
            rclpy.spin_once(self, timeout_sec=0.05)

    def wait_for_condition(self, condition_func, timeout=5.0):
        start = time.time()
        while time.time() - start < timeout:
            rclpy.spin_once(self, timeout_sec=0.05)
            if condition_func():
                return True
        return False

    def fail_exit(self):
        print(f"\n{BOLD}{RED}=================================================={RESET}")
        print(f"{BOLD}{RED}             TEST SUITE FAILED                    {RESET}")
        print(f"{BOLD}{RED}=================================================={RESET}\n")
        sys.exit(1)

def main(args=None):
    rclpy.init(args=args)
    node = IntegrationTestNode()
    try:
        # Wait for system nodes to discover subscribers/publishers
        print("Waiting 2.0s for ROS 2 network discovery...")
        time.sleep(2.0)
        node.run_tests()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
