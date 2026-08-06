#!/usr/bin/env python3
"""
XMOS Hardware Control Node for ReSpeaker XVF-3000

This node maintains exclusive USB access to the ReSpeaker hardware registers.
It continuously polls fast-changing hardware states (DOA, VAD) and publishes them to ROS topics.
It also exposes a service to dynamically adjust any tuning parameter (AEC, AGC, NS, etc.).
"""
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, Int32, Float32
from conversational_interfaces.srv import SetXmosParam

import struct
import threading
import usb.core
import usb.util
import time

# Full parameter list from tuning.py
PARAMETERS = {
    'AECFREEZEONOFF': (18, 7, 'int', 1, 0, 'rw', 'Adaptive Echo Canceler updates inhibit.', '0 = Adaptation enabled', '1 = Freeze adaptation, filter only'),
    'AECNORM': (18, 19, 'float', 16, 0.25, 'rw', 'Limit on norm of AEC filter coefficients'),
    'AECPATHCHANGE': (18, 25, 'int', 1, 0, 'ro', 'AEC Path Change Detection.', '0 = false (no path change detected)', '1 = true (path change detected)'),
    'RT60': (18, 26, 'float', 0.9, 0.25, 'ro', 'Current RT60 estimate in seconds'),
    'HPFONOFF': (18, 27, 'int', 3, 0, 'rw', 'High-pass Filter on microphone signals.', '0 = OFF', '1 = ON - 70 Hz cut-off', '2 = ON - 125 Hz cut-off', '3 = ON - 180 Hz cut-off'),
    'RT60ONOFF': (18, 28, 'int', 1, 0, 'rw', 'RT60 Estimation for AES. 0 = OFF 1 = ON'),
    'AECSILENCELEVEL': (18, 30, 'float', 1, 1e-09, 'rw', 'Threshold for signal detection in AEC [-inf .. 0] dBov (Default: -80dBov = 10log10(1x10-8))'),
    'AECSILENCEMODE': (18, 31, 'int', 1, 0, 'ro', 'AEC far-end silence detection status. ', '0 = false (signal detected) ', '1 = true (silence detected)'),
    'AGCONOFF': (19, 0, 'int', 1, 0, 'rw', 'Automatic Gain Control. ', '0 = OFF ', '1 = ON'),
    'AGCMAXGAIN': (19, 1, 'float', 1000, 1, 'rw', 'Maximum AGC gain factor. ', '[0 .. 60] dB (default 30dB = 20log10(31.6))'),
    'AGCDESIREDLEVEL': (19, 2, 'float', 0.99, 1e-08, 'rw', 'Target power level of the output signal. ', '[-inf .. 0] dBov (default: -23dBov = 10log10(0.005))'),
    'AGCGAIN': (19, 3, 'float', 1000, 1, 'rw', 'Current AGC gain factor. ', '[0 .. 60] dB (default: 0.0dB = 20log10(1.0))'),
    'AGCTIME': (19, 4, 'float', 1, 0.1, 'rw', 'Ramps-up / down time-constant in seconds.'),
    'CNIONOFF': (19, 5, 'int', 1, 0, 'rw', 'Comfort Noise Insertion.', '0 = OFF', '1 = ON'),
    'FREEZEONOFF': (19, 6, 'int', 1, 0, 'rw', 'Adaptive beamformer updates.', '0 = Adaptation enabled', '1 = Freeze adaptation, filter only'),
    'STATNOISEONOFF': (19, 8, 'int', 1, 0, 'rw', 'Stationary noise suppression.', '0 = OFF', '1 = ON'),
    'GAMMA_NS': (19, 9, 'float', 3, 0, 'rw', 'Over-subtraction factor of stationary noise. min .. max attenuation'),
    'MIN_NS': (19, 10, 'float', 1, 0, 'rw', 'Gain-floor for stationary noise suppression.', '[-inf .. 0] dB (default: -16dB = 20log10(0.15))'),
    'NONSTATNOISEONOFF': (19, 11, 'int', 1, 0, 'rw', 'Non-stationary noise suppression.', '0 = OFF', '1 = ON'),
    'GAMMA_NN': (19, 12, 'float', 3, 0, 'rw', 'Over-subtraction factor of non- stationary noise. min .. max attenuation'),
    'MIN_NN': (19, 13, 'float', 1, 0, 'rw', 'Gain-floor for non-stationary noise suppression.', '[-inf .. 0] dB (default: -10dB = 20log10(0.3))'),
    'ECHOONOFF': (19, 14, 'int', 1, 0, 'rw', 'Echo suppression.', '0 = OFF', '1 = ON'),
    'GAMMA_E': (19, 15, 'float', 3, 0, 'rw', 'Over-subtraction factor of echo (direct and early components). min .. max attenuation'),
    'GAMMA_ETAIL': (19, 16, 'float', 3, 0, 'rw', 'Over-subtraction factor of echo (tail components). min .. max attenuation'),
    'GAMMA_ENL': (19, 17, 'float', 5, 0, 'rw', 'Over-subtraction factor of non-linear echo. min .. max attenuation'),
    'NLATTENONOFF': (19, 18, 'int', 1, 0, 'rw', 'Non-Linear echo attenuation.', '0 = OFF', '1 = ON'),
    'NLAEC_MODE': (19, 20, 'int', 2, 0, 'rw', 'Non-Linear AEC training mode.', '0 = OFF', '1 = ON - phase 1', '2 = ON - phase 2'),
    'SPEECHDETECTED': (19, 22, 'int', 1, 0, 'ro', 'Speech detection status.', '0 = false (no speech detected)', '1 = true (speech detected)'),
    'FSBUPDATED': (19, 23, 'int', 1, 0, 'ro', 'FSB Update Decision.', '0 = false (FSB was not updated)', '1 = true (FSB was updated)'),
    'FSBPATHCHANGE': (19, 24, 'int', 1, 0, 'ro', 'FSB Path Change Detection.', '0 = false (no path change detected)', '1 = true (path change detected)'),
    'TRANSIENTONOFF': (19, 29, 'int', 1, 0, 'rw', 'Transient echo suppression.', '0 = OFF', '1 = ON'),
    'VOICEACTIVITY': (19, 32, 'int', 1, 0, 'ro', 'VAD voice activity status.', '0 = false (no voice activity)', '1 = true (voice activity)'),
    'STATNOISEONOFF_SR': (19, 33, 'int', 1, 0, 'rw', 'Stationary noise suppression for ASR.', '0 = OFF', '1 = ON'),
    'NONSTATNOISEONOFF_SR': (19, 34, 'int', 1, 0, 'rw', 'Non-stationary noise suppression for ASR.', '0 = OFF', '1 = ON'),
    'GAMMA_NS_SR': (19, 35, 'float', 3, 0, 'rw', 'Over-subtraction factor of stationary noise for ASR. ', '[0.0 .. 3.0] (default: 1.0)'),
    'GAMMA_NN_SR': (19, 36, 'float', 3, 0, 'rw', 'Over-subtraction factor of non-stationary noise for ASR. ', '[0.0 .. 3.0] (default: 1.1)'),
    'MIN_NS_SR': (19, 37, 'float', 1, 0, 'rw', 'Gain-floor for stationary noise suppression for ASR.', '[-inf .. 0] dB (default: -16dB = 20log10(0.15))'),
    'MIN_NN_SR': (19, 38, 'float', 1, 0, 'rw', 'Gain-floor for non-stationary noise suppression for ASR.', '[-inf .. 0] dB (default: -10dB = 20log10(0.3))'),
    'GAMMAVAD_SR': (19, 39, 'float', 1000, 0, 'rw', 'Set the threshold for voice activity detection.', '[-inf .. 60] dB (default: 3.5dB 20log10(1.5))'),
    'DOAANGLE': (21, 0, 'int', 359, 0, 'ro', 'DOA angle. Current value. Orientation depends on build configuration.')
}

class ReSpeakerUSBInterface:
    def __init__(self, vid=0x2886, pid=0x0018):
        self.vid = vid
        self.pid = pid
        self.dev = None
        self.TIMEOUT = 1000
        self._lock = threading.Lock()
        self.connect()

    def connect(self):
        # Must be called while holding self._lock
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
        with self._lock:
            if self.dev is None:
                self.connect()
            return self.dev is not None

    def read(self, name):
        if name not in PARAMETERS:
            return None

        with self._lock:
            if self.dev is None:
                self.connect()
            if self.dev is None:
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
            except Exception as e:
                # Force reconnect next time but print the error!
                print(f"XMOS read error for {name}: {e}")
                self.dev = None
                return None

    def write(self, name, value):
        if name not in PARAMETERS:
            return False, f"Parameter {name} not found."

        data = PARAMETERS[name]
        if data[5] == 'ro':
            return False, f"Parameter {name} is read-only."

        with self._lock:
            if self.dev is None:
                self.connect()
            if self.dev is None:
                return False, "Not connected to USB device."

            try:
                id = data[0]
                if data[2] == 'int':
                    payload = struct.pack(b'iii', data[1], int(value), 1)
                else:
                    payload = struct.pack(b'ifi', data[1], float(value), 0)

                self.dev.ctrl_transfer(
                    usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
                    0, 0, id, payload, self.TIMEOUT)
                return True, "Success"
            except Exception as e:
                self.dev = None  # Force reconnect next time
                return False, f"USB Error: {str(e)}"

class XmosHardwareNode(Node):
    def __init__(self):
        super().__init__('xmos_hardware_node')

        self.declare_parameter('respeaker_vid', 10374)  # 0x2886
        self.declare_parameter('respeaker_pid', 24)     # 0x0018
        self.declare_parameter('polling_rate_hz', 10.0)

        vid = self.get_parameter('respeaker_vid').value
        pid = self.get_parameter('respeaker_pid').value
        polling_rate = self.get_parameter('polling_rate_hz').value

        self.hw = ReSpeakerUSBInterface(vid=vid, pid=pid)

        # Publishers for hardware readouts
        self.doa_pub = self.create_publisher(Int32, 'hardware_doa', 10)
        self.vad_pub = self.create_publisher(Bool, 'hardware_vad', 10)
        self.rt60_pub = self.create_publisher(Float32, 'hardware_rt60', 10)

        # Service for parameter tuning
        self.param_service = self.create_service(
            SetXmosParam,
            'set_xmos_param',
            self.set_param_callback
        )

        if self.hw.is_connected():
            self.get_logger().info('✅ ReSpeaker XVF-3000 connected. XMOS Hardware node active.')
            
            # Apply baseline static tuning automatically (replaces apply_tuning.sh)
            baseline_tuning = {
                'AGCGAIN': 1.0,
                'NLATTENONOFF': 1.0,
                'GAMMAVAD_SR': 5.0,
                'GAMMA_E': 3.0,
                'GAMMA_ETAIL': 3.0,
                'GAMMA_ENL': 5.0,
                'AGCONOFF': 0.0,
                'STATNOISEONOFF': 1.0,
                'NONSTATNOISEONOFF': 1.0,
                'STATNOISEONOFF_SR': 1.0,
                'NONSTATNOISEONOFF_SR': 1.0
            }
            self.get_logger().info('Applying baseline tuning...')
            for param, val in baseline_tuning.items():
                success, msg = self.hw.write(param, val)
                if not success:
                    self.get_logger().warn(f"Failed to set baseline {param}: {msg}")
                else:
                    self.get_logger().info(f"✅ Baseline: {param} = {val}")
                # The XMOS chip requires a small delay between rapid sequential writes
                time.sleep(0.1)
            
            # Fast polling timer (e.g. 10Hz) to read DOA, VAD, RT60
            self.poll_timer = self.create_timer(1.0 / polling_rate, self.poll_hardware)
        else:
            self.get_logger().error('❌ ReSpeaker XVF-3000 not found on USB. Polling disabled.')

    def set_param_callback(self, request, response):
        param = request.param_name.upper().strip()
        val = request.param_value

        self.get_logger().info(f"Setting {param} = {val}...")
        success, msg = self.hw.write(param, val)
        
        response.success = success
        response.message = msg
        
        if success:
            self.get_logger().info(f"✅ XMOS: {param} set to {val}")
        else:
            self.get_logger().warn(f"❌ XMOS error: {msg}")
            
        return response

    def poll_hardware(self):
        if not self.hw.is_connected():
            return
            
        # Poll VAD
        vad = self.hw.read('VOICEACTIVITY')
        if vad is not None:
            msg = Bool()
            msg.data = bool(vad)
            self.vad_pub.publish(msg)

        # Poll DOA
        doa = self.hw.read('DOAANGLE')
        if doa is not None:
            msg = Int32()
            msg.data = int(doa)
            self.doa_pub.publish(msg)
            
        # Poll RT60 (reverb time estimate)
        rt60 = self.hw.read('RT60')
        if rt60 is not None:
            msg = Float32()
            msg.data = float(rt60)
            self.rt60_pub.publish(msg)

def main(args=None):
    rclpy.init(args=args)
    node = XmosHardwareNode()
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
