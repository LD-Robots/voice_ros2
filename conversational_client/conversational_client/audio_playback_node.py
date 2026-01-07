#!/usr/bin/env python3
"""
audio_playback_node.py
Primește audio de la server (TTS) și îl redă pe speaker.

EXPLICAȚIE:
- Acest nod PRIMEȘTE audio pe topic /audio_out (de la server)
- Și îl REDĂ pe speaker-ul robotului
"""

# ═══════════════════════════════════════════════════════════════════
# IMPORTURI - bibliotecile de care avem nevoie
# ═══════════════════════════════════════════════════════════════════

import rclpy                              # Biblioteca principală ROS2 pentru Python
from rclpy.node import Node               # Clasa de bază - toate nodurile moștenesc din ea
from conversational_interfaces.msg import Audio  # Tipul de mesaj Audio pe care l-am definit
from std_msgs.msg import Bool              # Pentru comenzi stop
import numpy as np                         # Pentru lucrul cu array-uri de numere
from collections import deque              # Coadă pentru buffer audio
import threading                           # Pentru a rula playback-ul în paralel

# Încercăm să importăm PyAudio (pentru redare audio)
try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False
    print("⚠️ PyAudio not installed. Run: pip install pyaudio")


# ═══════════════════════════════════════════════════════════════════
# CLASA NODULUI - aici e logica principală
# ═══════════════════════════════════════════════════════════════════

class AudioPlaybackNode(Node):
    """
    Nod ROS2 care redă audio primit de la server.
    
    Funcționare:
    1. Ascultă pe topic-ul /audio_out
    2. Când primește audio, îl pune într-un buffer
    3. Un thread separat redă audio-ul din buffer pe speaker
    """
    
    def __init__(self):
        # Apelează constructorul clasei părinte (Node)
        # 'audio_playback_node' = numele nodului (apare în ros2 node list)
        super().__init__('audio_playback_node')
        
        # ─────────────────────────────────────────────────────────
        # PARAMETRI - valori configurabile din exterior
        # ─────────────────────────────────────────────────────────
        self.declare_parameter('sample_rate', 16000)   # Frecvența audio
        self.declare_parameter('channels', 1)          # 1 = mono, 2 = stereo
        
        self.sample_rate = self.get_parameter('sample_rate').value
        self.channels = self.get_parameter('channels').value
        
        # ─────────────────────────────────────────────────────────
        # BUFFER - coadă pentru audio (acumulăm înainte de redare)
        # ─────────────────────────────────────────────────────────
        self.audio_buffer = deque(maxlen=100)  # Max 100 chunks (~2 secunde)
        self.is_playing = False
        
        # ─────────────────────────────────────────────────────────
        # SUBSCRIBER - ascultăm pe topic-ul /audio_out
        # ─────────────────────────────────────────────────────────
        # Când serverul trimite audio TTS, ajunge aici
        self.audio_sub = self.create_subscription(
            Audio,              # Tipul mesajului (definit în conversational_interfaces)
            '/audio_out',       # Numele topic-ului pe care ascultăm
            self.audio_callback,  # Funcția apelată când primim mesaj
            10                  # Dimensiunea cozii de mesaje
        )
        
        # ─────────────────────────────────────────────────────────
        # SETUP PYAUDIO - pentru redare pe speaker
        # ─────────────────────────────────────────────────────────
        self.stream = None
        if PYAUDIO_AVAILABLE:
            try:
                self.audio = pyaudio.PyAudio()
                self.stream = self.audio.open(
                    format=pyaudio.paInt16,    # Format: 16-bit integer
                    channels=self.channels,     # Mono sau stereo
                    rate=self.sample_rate,      # 16000 Hz
                    output=True,                # OUTPUT (nu input!) = speaker
                    frames_per_buffer=1024      # Mărimea buffer-ului
                )
                self.get_logger().info(f'🔊 Audio Playback ready: {self.sample_rate}Hz')
            except Exception as e:
                self.get_logger().error(f'❌ Failed to open speaker: {e}')
                self.stream = None
        else:
            self.get_logger().warn('⚠️ PyAudio not available - audio will not play')
        
        # ─────────────────────────────────────────────────────────
        # THREAD PENTRU PLAYBACK - rulează în paralel
        # ─────────────────────────────────────────────────────────
        self.running = True
        self.playback_thread = threading.Thread(target=self._playback_loop, daemon=True)
        self.playback_thread.start()
        
        # ─────────────────────────────────────────────────────────
        # SUBSCRIBER PENTRU STOP - permite barge_in_node să oprească playback-ul
        # ─────────────────────────────────────────────────────────
        self.stop_sub = self.create_subscription(
            Bool,
            '/stop_playback',
            self.stop_callback,
            10
        )
        
        self.get_logger().info('🔊 Audio Playback Node started - waiting for audio on /audio_out')
    
    # ═══════════════════════════════════════════════════════════════════
    # CALLBACK - apelată când primim mesaj pe /audio_out
    # ═══════════════════════════════════════════════════════════════════
    def audio_callback(self, msg: Audio):
        """
        Această funcție e apelată AUTOMAT de ROS2 
        de fiecare dată când primim un mesaj pe /audio_out.
        
        Args:
            msg: Mesajul Audio primit (conține sample_rate, channels, data)
        """
        # Convertim lista de int16 la numpy array
        audio_data = np.array(msg.data, dtype=np.int16)
        
        # Punem chunk-ul în buffer
        self.audio_buffer.append(audio_data)
        self.is_playing = True
        
        # Log (doar din când în când, să nu inunde)
        if len(self.audio_buffer) == 1:
            self.get_logger().info(f'🎵 Received audio, starting playback...')
    
    # ═══════════════════════════════════════════════════════════════════
    # PLAYBACK LOOP - rulează continuu în thread separat
    # ═══════════════════════════════════════════════════════════════════
    def _playback_loop(self):
        """
        Acest loop rulează într-un thread separat.
        Ia audio din buffer și îl redă pe speaker.
        """
        import time
        
        while self.running:
            if self.audio_buffer and self.stream is not None:
                # Ia primul chunk din buffer
                chunk = self.audio_buffer.popleft()
                # Redă-l pe speaker
                self.stream.write(chunk.tobytes())
            else:
                # Buffer gol - așteptăm puțin
                self.is_playing = False
                time.sleep(0.01)  # 10ms pauză
    
    # ═══════════════════════════════════════════════════════════════════
    # STOP PLAYBACK - oprește playback când user vorbește peste (barge-in)
    # ═══════════════════════════════════════════════════════════════════
    def stop_playback(self):
        """Oprește playback-ul curent (pentru barge-in)."""
        self.audio_buffer.clear()
        self.is_playing = False
        self.get_logger().info('⏹️ Playback stopped')
    
    def stop_callback(self, msg: Bool):
        """Callback pentru comanda de stop (de la barge_in_node)."""
        if msg.data:
            self.stop_playback()
    
    # ═══════════════════════════════════════════════════════════════════
    # CLEANUP - la închiderea nodului
    # ═══════════════════════════════════════════════════════════════════
    def destroy_node(self):
        """Curățare la oprirea nodului."""
        self.get_logger().info('🛑 Shutting down audio playback...')
        self.running = False
        if self.stream is not None:
            self.stream.stop_stream()
            self.stream.close()
            self.audio.terminate()
        super().destroy_node()


# ═══════════════════════════════════════════════════════════════════
# MAIN - punctul de intrare
# ═══════════════════════════════════════════════════════════════════

def main(args=None):
    """Funcția principală - pornește nodul."""
    
    rclpy.init(args=args)           # Inițializează ROS2
    
    node = AudioPlaybackNode()       # Creează nodul nostru
    
    try:
        rclpy.spin(node)             # Rulează nodul (așteaptă mesaje)
    except KeyboardInterrupt:
        pass                         # Ctrl+C - ieșire normală
    finally:
        node.destroy_node()          # Curățare
        rclpy.shutdown()             # Oprește ROS2


if __name__ == '__main__':
    main()
