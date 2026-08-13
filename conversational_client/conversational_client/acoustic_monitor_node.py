#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio
from std_msgs.msg import Bool, String, Float32
from conversational_interfaces.srv import SetXmosParam
import numpy as np
import struct
import time
import json
import threading

# ReSpeaker USB logic moved to xmos_hardware_node.py

class AcousticMonitorNode(Node):
    def __init__(self):
        super().__init__('acoustic_monitor_node')

        # Parameters
        self.declare_parameter('monitoring_interval_s', 5.0)
        self.declare_parameter('quiet_noise_threshold_dbfs', -48.0)
        self.declare_parameter('noisy_noise_threshold_dbfs', -35.0)
        self.declare_parameter('enable_respeaker_tuning', True)
        self.declare_parameter('state_hysteresis_db', 2.0)
        self.declare_parameter('min_state_dwell_s', 10.0)

        self.interval = self.get_parameter('monitoring_interval_s').value
        self.quiet_thresh = self.get_parameter('quiet_noise_threshold_dbfs').value
        self.noisy_thresh = self.get_parameter('noisy_noise_threshold_dbfs').value
        self.enable_hw_tuning = self.get_parameter('enable_respeaker_tuning').value
        self.state_hysteresis_db = float(self.get_parameter('state_hysteresis_db').value)
        self.min_state_dwell_s = float(self.get_parameter('min_state_dwell_s').value)

        # State tracking
        self.voice_active = False
        self.robot_speaking = False
        self.recent_noise_dbfs = []
        self.lock = threading.Lock()
        self.current_state = 'moderate'
        self.latest_rt60 = 0.0
        self._last_state_change_time = 0.0  # timestamp of last state transition

        # Publishers / Subscribers
        self.env_pub = self.create_publisher(String, 'acoustic_environment', 10)
        self.audio_sub = self.create_subscription(Audio, 'audio_raw', self.audio_callback, 10)
        self.vad_sub = self.create_subscription(Bool, 'voice_activity', self.vad_callback, 10)
        self.speaking_sub = self.create_subscription(Bool, 'is_speaking', self.speaking_callback, 10)
        self.rt60_sub = self.create_subscription(Float32, 'hardware_rt60', self.rt60_callback, 10)

        # Service Client for XMOS Tuning
        self.xmos_client = self.create_client(SetXmosParam, 'set_xmos_param')

        # Environment Evaluation Timer
        self.timer = self.create_timer(self.interval, self.evaluate_environment)
        
        self.get_logger().info('🎙️ Acoustic Monitor Node Initialized.')

    def rt60_callback(self, msg: Float32):
        self.latest_rt60 = msg.data

    def _send_tuning(self, param_name, param_value):
        if not self.xmos_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().warn('XMOS service not available, skipping tuning.')
            return
            
        req = SetXmosParam.Request()
        req.param_name = param_name
        req.param_value = float(param_value)
        # Send asynchronously so we don't block the timer loop
        self.xmos_client.call_async(req)

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

        # Determine raw candidate state from noise floor
        if avg_noise <= self.quiet_thresh:
            candidate = 'quiet'
        elif avg_noise >= self.noisy_thresh:
            candidate = 'noisy'
        else:
            candidate = 'moderate'

        rt60 = self.latest_rt60
        now = time.monotonic()

        # ── Hysteresis guard ────────────────────────────────────────────────────
        # Only allow a state transition if:
        #   1. The candidate differs from the current state
        #   2. The noise measurement is far enough past the threshold (hysteresis)
        #   3. Enough dwell time has elapsed since the last transition
        state = self.current_state  # default: stay in current state
        if candidate != self.current_state:
            dwell_ok = (now - self._last_state_change_time) >= self.min_state_dwell_s
            if dwell_ok:
                # Check hysteresis: noise must exceed the boundary by at least state_hysteresis_db
                if candidate == 'quiet':
                    # Requires noise to be at least hysteresis_db below the quiet threshold
                    hysteresis_ok = avg_noise <= (self.quiet_thresh - self.state_hysteresis_db)
                elif candidate == 'noisy':
                    # Requires noise to be at least hysteresis_db above the noisy threshold
                    hysteresis_ok = avg_noise >= (self.noisy_thresh + self.state_hysteresis_db)
                else:  # candidate == 'moderate'
                    # Coming from quiet: must be above quiet_thresh + hysteresis
                    # Coming from noisy: must be below noisy_thresh - hysteresis
                    if self.current_state == 'quiet':
                        hysteresis_ok = avg_noise > (self.quiet_thresh + self.state_hysteresis_db)
                    else:
                        hysteresis_ok = avg_noise < (self.noisy_thresh - self.state_hysteresis_db)

                if hysteresis_ok:
                    state = candidate
                else:
                    self.get_logger().debug(
                        f'🛡️ Hysteresis blocked transition {self.current_state}→{candidate} '
                        f'(noise={avg_noise:.1f} dBFS, margin={self.state_hysteresis_db:.1f} dB)'
                    )
            else:
                remaining = self.min_state_dwell_s - (now - self._last_state_change_time)
                self.get_logger().debug(
                    f'⏳ Dwell guard blocked transition {self.current_state}→{candidate} '
                    f'({remaining:.1f}s remaining)'
                )
        # ────────────────────────────────────────────────────────────────────────

        # Apply dynamic hardware noise suppression tuning via service
        if self.enable_hw_tuning and state != self.current_state:
            self.get_logger().info(f'Applying new tuning for {state} environment...')
            if state == 'quiet':
                # Gentle suppression
                self._send_tuning('STATNOISEONOFF_SR', 1.0)
                self._send_tuning('NONSTATNOISEONOFF_SR', 0.0)
                self._send_tuning('GAMMA_NS_SR', 0.5)
            elif state == 'noisy':
                # Aggressive suppression
                self._send_tuning('STATNOISEONOFF_SR', 1.0)
                self._send_tuning('NONSTATNOISEONOFF_SR', 1.0)
                self._send_tuning('GAMMA_NS_SR', 1.5)
            else:
                # Standard moderate settings
                self._send_tuning('STATNOISEONOFF_SR', 1.0)
                self._send_tuning('NONSTATNOISEONOFF_SR', 1.0)
                self._send_tuning('GAMMA_NS_SR', 1.0)

        # Log state transitions
        if state != self.current_state:
            self.get_logger().info(
                f'🔊 Acoustic state changed: {self.current_state} ➡️ {state} '
                f'(Noise: {avg_noise:.1f} dBFS, RT60: {rt60:.2f}s)'
            )
            self.current_state = state
            self._last_state_change_time = now

        # Publish environment info
        env_msg = String()
        env_msg.data = json.dumps({
            'state': state,
            'noise_dbfs': float(avg_noise),
            'rt60': rt60,
            'hardware_active': self.xmos_client.wait_for_service(timeout_sec=0.1)
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
