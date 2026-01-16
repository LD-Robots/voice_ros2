
#!/usr/bin/env python3
"""
audio_capture_node.py
Capturează audio de la microfon și publică pe /audio_raw

EXPLICAȚIE:
- Acest nod CAPTUREAZĂ audio de la microfonul robotului
- Publică fiecare chunk (bucată) de audio pe topicul /audio_raw
- Alte noduri (wake_word, vad) și serverul primesc acest audio
"""

# ═══════════════════════════════════════════════════════════════════
# IMPORTURI - bibliotecile de care avem nevoie
# ═══════════════════════════════════════════════════════════════════

import rclpy                              # Biblioteca principală ROS2 pentru Python
from rclpy.node import Node               # Clasa de bază - toate nodurile moștenesc din ea
from conversational_interfaces.msg import Audio  # Tipul de mesaj Audio definit de noi
import numpy as np                         # Pentru lucrul cu array-uri de numere

# ─────────────────────────────────────────────────────────────────────
# Încercăm să importăm PyAudio (biblioteca pentru acces la microfon)
# Dacă nu e instalată, nodul va rula în "dummy mode" (fără microfon real)
# ─────────────────────────────────────────────────────────────────────
try:
    import pyaudio                         # Biblioteca pentru acces la microfon/speaker
    PYAUDIO_AVAILABLE = True               # Flag: PyAudio e disponibil
except ImportError:
    PYAUDIO_AVAILABLE = False              # Flag: PyAudio NU e disponibil
    print("⚠️ PyAudio not installed. Run: pip install pyaudio")


# ═══════════════════════════════════════════════════════════════════
# CLASA NODULUI - aici e logica principală
# ═══════════════════════════════════════════════════════════════════

class AudioCaptureNode(Node):
    """
    Nod ROS2 care capturează audio de la microfon.
    
    Funcționare:
    1. Deschide microfonul cu PyAudio
    2. La fiecare 20ms, citește un chunk de audio
    3. Publică chunk-ul pe topicul /audio_raw
    """
    
    def __init__(self):
        # ─────────────────────────────────────────────────────────
        # Apelează constructorul clasei părinte (Node)
        # 'audio_capture_node' = numele nodului (apare în ros2 node list)
        # ─────────────────────────────────────────────────────────
        super().__init__('audio_capture_node')
        
        # ─────────────────────────────────────────────────────────
        # PARAMETRI - valori configurabile din exterior (ros2 param)
        # declare_parameter = declară un parametru cu valoare default
        # ─────────────────────────────────────────────────────────
        self.declare_parameter('sample_rate', 16000)  # Frecvența: 16000 samples/secundă
        self.declare_parameter('channels', 1)          # Canale: 1 = mono, 2 = stereo
        self.declare_parameter('chunk_ms', 20)         # Mărime chunk: 20 milisecunde
        self.declare_parameter('device_index', -1)     # -1 = default, altfel index specific
        
        # ─────────────────────────────────────────────────────────
        # Citește valorile parametrilor declarați
        # ─────────────────────────────────────────────────────────
        self.sample_rate = self.get_parameter('sample_rate').value  # 16000
        self.channels = self.get_parameter('channels').value         # 1
        chunk_ms = self.get_parameter('chunk_ms').value               # 20
        device_index = self.get_parameter('device_index').value       # -1 = default
        
        # ─────────────────────────────────────────────────────────
        # CALCUL: Câte samples are un chunk?
        # La 16000Hz și 20ms: 16000 * 20 / 1000 = 320 samples
        # ─────────────────────────────────────────────────────────
        self.chunk_size = int(self.sample_rate * chunk_ms / 1000)
        
        # ─────────────────────────────────────────────────────────
        # PUBLISHER - creăm un publisher pentru topicul /audio_raw
        # Audio = tipul mesajului (definit în conversational_interfaces)
        # '/audio_raw' = numele topicului
        # 10 = dimensiunea cozii de mesaje (QoS)
        # ─────────────────────────────────────────────────────────
        self.audio_pub = self.create_publisher(
            Audio,           # Tipul mesajului
            '/audio_raw',    # Numele topicului
            10               # Queue size
        )
        
        # ─────────────────────────────────────────────────────────
        # SETUP PYAUDIO - deschide microfonul pentru captură
        # ─────────────────────────────────────────────────────────
        self.stream = None                 # Stream-ul audio (None dacă nu e deschis)
        if PYAUDIO_AVAILABLE:
            try:
                self.audio = pyaudio.PyAudio()  # Creează obiectul PyAudio
                
                # Dacă device_index e -1, găsește automat un USB mic sau folosește default
                if device_index == -1:
                    # Încearcă să găsească un microfon USB
                    for i in range(self.audio.get_device_count()):
                        info = self.audio.get_device_info_by_index(i)
                        if info['maxInputChannels'] > 0:  # E input device
                            name = info['name'].lower()
                            if 'usb' in name or 'me6s' in name:
                                device_index = i
                                self.get_logger().info(f'🎤 Found USB mic: {info["name"]} (index={i})')
                                break
                
                # Deschide microfonul
                open_params = {
                    'format': pyaudio.paInt16,
                    'channels': self.channels,
                    'rate': self.sample_rate,
                    'input': True,
                    'frames_per_buffer': self.chunk_size,
                }
                if device_index >= 0:
                    open_params['input_device_index'] = device_index
                    
                self.stream = self.audio.open(**open_params)
                
                # Log de confirmare
                actual_device = device_index if device_index >= 0 else 'default'
                self.get_logger().info(
                    f'🎤 Audio Capture started: {self.sample_rate}Hz, '
                    f'{self.channels}ch, chunk={self.chunk_size} samples ({chunk_ms}ms), '
                    f'device={actual_device}'
                )
            except Exception as e:
                # Eroare la deschiderea microfonului
                self.get_logger().error(f'❌ Failed to open microphone: {e}')
                self.stream = None
        else:
            # PyAudio nu e instalat - rulăm în dummy mode
            self.get_logger().warn('⚠️ PyAudio not available - running in dummy mode')
        
        # ─────────────────────────────────────────────────────────
        # TIMER - apelează capture_callback la fiecare chunk_ms ms
        # timer_period = 0.02 secunde = 20ms
        # ─────────────────────────────────────────────────────────
        timer_period = chunk_ms / 1000.0   # Conversie: 20ms → 0.02s
        self.timer = self.create_timer(timer_period, self.capture_callback)
        
        # Contor pentru logging periodic
        self.frame_count = 0
    
    # ═══════════════════════════════════════════════════════════════════
    # CALLBACK - apelată automat de timer la fiecare 20ms
    # ═══════════════════════════════════════════════════════════════════
    def capture_callback(self):
        """Citește audio de la microfon și publică pe topic."""
        
        # ─────────────────────────────────────────────────────────
        # VERIFICARE: Avem microfon deschis?
        # ─────────────────────────────────────────────────────────
        if self.stream is None:
            # NU avem microfon - publicăm silence (zerouri) pentru test
            if self.frame_count % 50 == 0:  # Log la fiecare secundă (50 chunks)
                self.get_logger().info('📢 Publishing silence (no microphone)')
            audio_data = [0] * self.chunk_size  # Array de zerouri
        else:
            # ─────────────────────────────────────────────────────────
            # CITIRE AUDIO - citim chunk_size samples de la microfon
            # ─────────────────────────────────────────────────────────
            try:
                # Citește bytes de la microfon
                data = self.stream.read(self.chunk_size, exception_on_overflow=False)
                
                # Convertește bytes → numpy array de int16
                audio_array = np.frombuffer(data, dtype=np.int16)
                
                # Convertește numpy array → Python list (pentru mesajul ROS2)
                audio_data = audio_array.tolist()
            except Exception as e:
                self.get_logger().error(f'❌ Audio read error: {e}')
                return  # Ieșim dacă e eroare
        
        # ─────────────────────────────────────────────────────────
        # CREEAZĂ MESAJUL ROS2 - tip Audio
        # ─────────────────────────────────────────────────────────
        msg = Audio()                       # Creează un mesaj gol
        msg.sample_rate = self.sample_rate  # Setează sample rate (16000)
        msg.channels = self.channels        # Setează canale (1)
        msg.data = audio_data               # Setează datele audio
        
        # ─────────────────────────────────────────────────────────
        # PUBLICĂ mesajul pe topicul /audio_raw
        # ─────────────────────────────────────────────────────────
        self.audio_pub.publish(msg)
        self.frame_count += 1               # Incrementează contorul
        
        # ─────────────────────────────────────────────────────────
        # LOG PERIODIC - la fiecare 5 secunde (250 chunks)
        # ─────────────────────────────────────────────────────────
        if self.frame_count % (50 * 5) == 0:
            self.get_logger().info(f'📊 Published {self.frame_count} audio frames')
    
    # ═══════════════════════════════════════════════════════════════════
    # CLEANUP - apelată când nodul se închide
    # ═══════════════════════════════════════════════════════════════════
    def destroy_node(self):
        """Curățare la închiderea nodului."""
        self.get_logger().info('🛑 Shutting down audio capture...')
        
        # Închide stream-ul și eliberează resursele PyAudio
        if self.stream is not None:
            self.stream.stop_stream()   # Oprește captura
            self.stream.close()         # Închide stream-ul
            self.audio.terminate()      # Eliberează PyAudio
        
        super().destroy_node()          # Apelează cleanup-ul părinte


# ═══════════════════════════════════════════════════════════════════
# MAIN - punctul de intrare (când rulezi: ros2 run ... audio_capture_node)
# ═══════════════════════════════════════════════════════════════════

def main(args=None):
    """Funcția principală - pornește nodul."""
    
    rclpy.init(args=args)           # Inițializează ROS2
    
    node = AudioCaptureNode()        # Creează nodul nostru
    
    try:
        rclpy.spin(node)             # Rulează nodul (loop infinit, așteaptă events)
    except KeyboardInterrupt:
        pass                         # Ctrl+C - ieșire normală
    finally:
        node.destroy_node()          # Curățare
        rclpy.shutdown()             # Oprește ROS2


# ─────────────────────────────────────────────────────────────────────
# Dacă rulezi fișierul direct (python audio_capture_node.py),
# apelează funcția main()
# ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    main()
