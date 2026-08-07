#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio
from std_msgs.msg import Bool, String
import numpy as np
import struct
import usb.core
import usb.util
import time
import json
import threading

PARAMETERS = {
    'RT60': (18, 26, 'float', 0.9, 0.25, 'ro', 'Current RT60 estimate in seconds'),
    'STATNOISEONOFF_SR': (19, 33, 'int', 1, 0, 'rw', 'Stationary noise suppression for ASR.'),
    'NONSTATNOISEONOFF_SR': (19, 34, 'int', 1, 0, 'rw', 'Non-stationary noise suppression for ASR.'),
    'GAMMA_NS_SR': (19, 35, 'float', 3, 0, 'rw', 'Over-subtraction factor of stationary noise for ASR.'),
    'MIN_NS_SR': (19, 37, 'float', 1, 0, 'rw', 'Gain-floor for stationary noise suppression for ASR.')
}

class ReSpeakerTuner:
    def __init__(self, vid=0x2886, pid=0x0018):
        self.vid = vid
        self.pid = pid
        self.dev = None
        self.TIMEOUT = 1000
        self.connect()

    def connect(self):
        try:
            self.dev = usb.core.find(idVendor=self.vid, idProduct=self.pid)
            if self.dev:
                try:
                    self.dev.set_configuration()
                except usb.core.USBError:
                    pass
        except Exception:
            self.dev = None

    def is_connected(self):
        if self.dev is None:
            self.connect()
        return self.dev is not None

    def read(self, name):
        if not self.is_connected():
            return None
        try:
            data = PARAMETERS[name]
            id = data[0]
            cmd = 0x80 | data[1]
            if data[2] == 'int':
                cmd |= 0x40
            length = 8
            response = self.dev.ctrl_transfer(
                usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
                0, cmd, id, length, self.TIMEOUT)
            response = struct.unpack(b'ii', response.tobytes())
            if data[2] == 'int':
                result = response[0]
            else:
                result = response[0] * (2.**response[1])
            return result
        except Exception:
            self.dev = None # Force reconnect next time
            return None

    def write(self, name, value):
        if not self.is_connected():
            return False
        try:
            data = PARAMETERS[name]
            id = data[0]
            if data[2] == 'int':
                payload = struct.pack(b'iii', data[1], int(value), 1)
            else:
                payload = struct.pack(b'ifi', data[1], float(value), 0)
            self.dev.ctrl_transfer(
                usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
                0, 0, id, payload, self.TIMEOUT)
            return True
        except Exception:
            self.dev = None # Force reconnect next time
            return False

class AcousticMonitorNode(Node):
    def __init__(self):
        super().__init__('acoustic_monitor_node')

        # Parameters
        self.declare_parameter('monitoring_interval_s', 5.0)
        self.declare_parameter('respeaker_vid', 10374)  # 0x2886
        self.declare_parameter('respeaker_pid', 24)     # 0x0018
        self.declare_parameter('quiet_noise_threshold_dbfs', -48.0)
        self.declare_parameter('noisy_noise_threshold_dbfs', -35.0)
        self.declare_parameter('enable_respeaker_tuning', True)

        self.interval = self.get_parameter('monitoring_interval_s').value
        self.vid = self.get_parameter('respeaker_vid').value
        self.pid = self.get_parameter('respeaker_pid').value
        self.quiet_thresh = self.get_parameter('quiet_noise_threshold_dbfs').value
        self.noisy_thresh = self.get_parameter('noisy_noise_threshold_dbfs').value
        self.enable_hw_tuning = self.get_parameter('enable_respeaker_tuning').value

        # Tuner initialization
        self.tuner = ReSpeakerTuner(vid=self.vid, pid=self.pid)

        # State tracking
        self.voice_active = False
        self.robot_speaking = False
        self.recent_noise_dbfs = []
        self.lock = threading.Lock()
        self.current_state = 'moderate'

        # Publishers / Subscribers
        self.env_pub = self.create_publisher(String, 'acoustic_environment', 10)
        self.audio_sub = self.create_subscription(Audio, 'audio_raw', self.audio_callback, 10)
        self.vad_sub = self.create_subscription(Bool, 'voice_activity', self.vad_callback, 10)
        self.speaking_sub = self.create_subscription(Bool, 'is_speaking', self.speaking_callback, 10)

        # Environment Evaluation Timer
        self.timer = self.create_timer(self.interval, self.evaluate_environment)
        
        self.get_logger().info('🎙️ Acoustic Monitor Node Initialized.')
        if self.tuner.is_connected():
            self.get_logger().info('✅ ReSpeaker XVF-3000 detected. Hardware registers accessible.')
        else:
            self.get_logger().warn('⚠️ ReSpeaker XVF-3000 not found over USB. Operating in Software fallback mode.')

    def vad_callback(self, msg: Bool):
        with self.lock:
            self.voice_active = msg.data

    def speaking_callback(self, msg: Bool):
        with self.lock:
            self.robot_speaking = msg.data

    def audio_callback(self, msg: Audio):
        # We only measure noise floor when there is silence and no robot playback
        with self.lock:
            if self.voice_active or self.robot_speaking:
                return

        audio_data = np.array(msg.data, dtype=np.int16)
        if audio_data.size == 0:
            return

        # Calculate RMS energy of current chunk
        rms = np.sqrt(np.mean(audio_data.astype(np.float32) ** 2))
        
        # Convert to dBFS (max amplitude of int16 is 32768)
        if rms > 0.0:
            dbfs = 20 * np.log10(rms / 32768.0)
        else:
            dbfs = -100.0

        with self.lock:
            self.recent_noise_dbfs.append(dbfs)
            # Keep only the last 100 frames to avoid memory bloat
            if len(self.recent_noise_dbfs) > 100:
                self.recent_noise_dbfs.pop(0)

    def evaluate_environment(self):
        with self.lock:
            if not self.recent_noise_dbfs:
                # No data collected (user spoke continuously, or no audio received)
                avg_noise = None
            else:
                avg_noise = np.mean(self.recent_noise_dbfs)
                self.recent_noise_dbfs.clear()

        # Fallback to current if no data
        if avg_noise is None:
            return

        # Determine Environment state
        if avg_noise <= self.quiet_thresh:
            state = 'quiet'
        elif avg_noise >= self.noisy_thresh:
            state = 'noisy'
        else:
            state = 'moderate'

        # Read RT60 from hardware if connected
        rt60 = 0.0
        hw_active = False
        if self.tuner.is_connected():
            hw_active = True
            hw_rt60 = self.tuner.read('RT60')
            if hw_rt60 is not None:
                rt60 = float(hw_rt60)

            # Apply dynamic hardware noise suppression tuning
            if self.enable_hw_tuning:
                if state == 'quiet':
                    # Gentle suppression
                    self.tuner.write('STATNOISEONOFF_SR', 1)
                    self.tuner.write('NONSTATNOISEONOFF_SR', 0)
                    self.tuner.write('GAMMA_NS_SR', 0.5)
                elif state == 'noisy':
                    # Aggressive suppression
                    self.tuner.write('STATNOISEONOFF_SR', 1)
                    self.tuner.write('NONSTATNOISEONOFF_SR', 1)
                    self.tuner.write('GAMMA_NS_SR', 1.5)
                else:
                    # Standard moderate settings
                    self.tuner.write('STATNOISEONOFF_SR', 1)
                    self.tuner.write('NONSTATNOISEONOFF_SR', 1)
                    self.tuner.write('GAMMA_NS_SR', 1.0)

        # Log state transitions
        if state != self.current_state:
            self.get_logger().info(f'🔊 Acoustic state changed: {self.current_state} ➡️ {state} (Noise: {avg_noise:.1f} dBFS, RT60: {rt60:.2f}s)')
            self.current_state = state

        # Publish environment info
        env_msg = String()
        env_msg.data = json.dumps({
            'state': state,
            'noise_dbfs': float(avg_noise),
            'rt60': rt60,
            'hardware_active': hw_active
        })
        self.env_pub.publish(env_msg)

def main(args=None):
    rclpy.init(args=args)
    node = AcousticMonitorNode()
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
