#!/usr/bin/env python3
"""
Deepgram STT Node - Replaces Whisper with Deepgram Streaming STT.
"""
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio, Transcription
from std_msgs.msg import String, Bool
import json
import os
import threading
import queue
import time
import unicodedata
import re
import websockets.sync.client
from websockets.exceptions import ConnectionClosed

try:
    from rapidfuzz import fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    print("⚠️ rapidfuzz not installed. Anti-echo disabled.")

class DeepgramASRNode(Node):
    def __init__(self):
        super().__init__('asr_node')
        
        # Load .env explicitly
        try:
            from dotenv import load_dotenv
            import os
            # Try a few paths to find .env
            env_paths = [
                os.path.join(os.getcwd(), '.env'),
                os.path.join(os.path.expanduser('~'), 'voice_ros2', '.env')
            ]
            for path in env_paths:
                if os.path.exists(path):
                    load_dotenv(dotenv_path=path)
                    break
        except ImportError:
            self.get_logger().warn("python-dotenv not installed, assuming env vars are set.")
            
        self.declare_parameter('language', 'en')
        self.declare_parameter('model', 'nova-3')
        self.declare_parameter('echo_threshold', 85)
        self.declare_parameter('echo_min_length', 8)
        self.declare_parameter('echo_enabled', True)
        
        self.language = self.get_parameter('language').value
        self.model = self.get_parameter('model').value
        
        self.echo_threshold = self.get_parameter('echo_threshold').value
        self.echo_min_length = self.get_parameter('echo_min_length').value
        self.echo_enabled = self.get_parameter('echo_enabled').value and RAPIDFUZZ_AVAILABLE
        
        self.api_key = os.environ.get("DEEPGRAM_API_KEY", "")
        if not self.api_key:
            self.get_logger().error("DEEPGRAM_API_KEY environment variable is not set!")
        
        self.current_backend = 'legacy'
        self.last_bot_reply = ""
        
        self.audio_queue = queue.Queue()
        self.is_connected = False
        self.ws_connection = None
        self.is_speaking = False
        
        # ROS Subscriptions
        self.audio_sub = self.create_subscription(Audio, '/audio_raw', self.audio_callback, 10)
        self.vad_sub = self.create_subscription(Bool, '/voice_activity', self.vad_callback, 10)
        self.backend_sub = self.create_subscription(String, '/conversation_backend', self.backend_callback, 10)
        self.llm_response_sub = self.create_subscription(Transcription, '/llm_response', self.llm_response_callback, 10)
        
        # ROS Publishers
        self.transcription_pub = self.create_publisher(Transcription, '/transcription', 10)
        
        # Start connection threads
        self.connection_thread = threading.Thread(target=self.connection_manager, daemon=True)
        self.connection_thread.start()
        
        self.get_logger().info('🚀 Deepgram STT Node started! (Replaces Whisper)')

    def vad_callback(self, msg: Bool):
        was_speaking = getattr(self, 'is_speaking', False)
        self.is_speaking = msg.data
        
        # Deepgram needs silence to trigger its internal endpointing (is_final=True)
        # If we just stop sending audio abruptly, it might hang forever waiting for more audio.
        if not self.is_speaking and was_speaking:
            # Send 1.5 seconds of pure zeros (16000 samples/sec * 1.5s * 2 bytes/sample)
            silence_bytes = b'\x00' * 48000
            self.audio_queue.put(silence_bytes)

    def audio_callback(self, msg: Audio):
        if self.current_backend != 'legacy':
            return
            
        # To save money and bandwidth, we only stream audio to Deepgram when VAD is active,
        # plus a tiny bit of tail. Since Deepgram processes instantly, this works perfectly.
        if self.is_speaking:
            import numpy as np
            audio_bytes = np.array(msg.data, dtype=np.int16).tobytes()
            self.audio_queue.put(audio_bytes)

    def llm_response_callback(self, msg: Transcription):
        if msg.text:
            self.last_bot_reply = msg.text

    def backend_callback(self, msg: String):
        backend = msg.data.strip() or 'legacy'
        if backend != self.current_backend:
            self.current_backend = backend
            self.audio_queue.queue.clear()

    def _normalize_text(self, text: str) -> str:
        if not text:
            return ""
        text = text.lower()
        text = unicodedata.normalize('NFD', text)
        text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
        text = re.sub(r'[^a-z0-9\s]', '', text)
        return ' '.join(text.split()).strip()
    
    def _is_echo(self, transcription: str) -> bool:
        if not self.echo_enabled or not self.last_bot_reply:
            return False
        user_norm = self._normalize_text(transcription)
        bot_norm = self._normalize_text(self.last_bot_reply)
        
        if len(user_norm) < self.echo_min_length or len(bot_norm) < self.echo_min_length:
            return False
        
        similarity = fuzz.partial_ratio(user_norm, bot_norm)
        if similarity >= self.echo_threshold:
            self.get_logger().debug(f'🔇 Ignored echo (sim={similarity}%)')
            return True
        return False

    def connection_manager(self):
        """Maintains the Deepgram WebSocket connection"""
        while True:
            if self.current_backend != 'legacy':
                time.sleep(1.0)
                continue
                
            url = f"wss://api.deepgram.com/v1/listen?model={self.model}&encoding=linear16&sample_rate=16000&channels=1&smart_format=true"
            if self.language:
                url += f"&language={self.language}"
                
            headers = {"Authorization": f"Token {self.api_key}"}
            
            try:
                self.get_logger().info("Connecting to Deepgram...")
                with websockets.sync.client.connect(url, additional_headers=headers) as ws:
                    self.ws_connection = ws
                    self.is_connected = True
                    self.get_logger().info("✅ Connected to Deepgram WebSocket!")
                    
                    # Start receiver thread
                    recv_thread = threading.Thread(target=self.receive_loop, args=(ws,), daemon=True)
                    recv_thread.start()
                    
                    # Send loop
                    while self.is_connected and self.current_backend == 'legacy':
                        try:
                            # Send audio if available, else send KeepAlive every 5s
                            audio = self.audio_queue.get(timeout=5.0)
                            ws.send(audio)
                        except queue.Empty:
                            ws.send(json.dumps({"type": "KeepAlive"}))
                        except Exception as e:
                            self.get_logger().error(f"Send error: {e}")
                            break
            except Exception as e:
                self.get_logger().error(f"Deepgram connection failed: {e}")
            
            self.is_connected = False
            self.ws_connection = None
            time.sleep(2.0)  # Reconnect delay

    def receive_loop(self, ws):
        """Receives JSON transcripts from Deepgram"""
        try:
            for message in ws:
                if not self.is_connected:
                    break
                    
                data = json.loads(message)
                
                if data.get('type') == 'Results':
                    is_final = data.get('is_final', False)
                    alternatives = data['channel']['alternatives']
                    if not alternatives:
                        continue
                        
                    transcript = alternatives[0].get('transcript', '').strip()
                    confidence = alternatives[0].get('confidence', 0.0)
                    
                    if transcript and is_final:
                        if self._is_echo(transcript):
                            continue
                            
                        self.get_logger().info(f"🧏 [deepgram] {transcript}")
                        out = Transcription()
                        out.text = transcript
                        out.language = self.language
                        out.confidence = float(confidence)
                        self.transcription_pub.publish(out)
                        
        except ConnectionClosed:
            self.get_logger().warn("Deepgram connection closed.")
        except Exception as e:
            self.get_logger().error(f"Receive error: {e}")
        finally:
            self.is_connected = False

def main(args=None):
    rclpy.init(args=args)
    node = DeepgramASRNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
