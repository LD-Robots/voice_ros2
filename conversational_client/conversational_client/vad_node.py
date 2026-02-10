#!/usr/bin/env python3
"""
vad_node.py
Voice Activity Detection - detectează când vorbește utilizatorul.

EXPLICAȚIE:
- Primește audio pe /audio_raw
- Analizează dacă audio conține voce (nu doar zgomot/tăcere)
- Publică True/False pe /voice_activity
- Folosit de alte noduri pentru:
  - A ști când să trimită audio la server (doar când vorbește)
  - Barge-in (oprește TTS când user vorbește)
"""

# ═══════════════════════════════════════════════════════════════════
# IMPORTURI
# ═══════════════════════════════════════════════════════════════════

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio, WakeWord
from std_msgs.msg import Bool
import numpy as np

# Încercăm să importăm WebRTC VAD (varianta simplă)
try:
    import webrtcvad
    WEBRTCVAD_AVAILABLE = True
except ImportError:
    WEBRTCVAD_AVAILABLE = False
    print("⚠️ webrtcvad not installed. Run: pip install webrtcvad")


# ═══════════════════════════════════════════════════════════════════
# CLASA NODULUI
# ═══════════════════════════════════════════════════════════════════

class VADNode(Node):
    """
    Nod ROS2 pentru Voice Activity Detection.
    
    Funcționare:
    1. Primește audio pe /audio_raw
    2. Analizează dacă conține voce (energie + frecvențe tipice vocii)
    3. Publică True/False pe /voice_activity
    """
    
    def __init__(self):
        super().__init__('vad_node')
        
        # ─────────────────────────────────────────────────────────
        # PARAMETRI
        # ─────────────────────────────────────────────────────────
        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('aggressiveness', 2)  # 0-3, 3 = mai agresiv
        self.declare_parameter('energy_threshold', 500)  # Prag energie RMS
        self.declare_parameter('wake_word_enabled', True)
        self.declare_parameter('session_timeout', 8.0)

        
        self.sample_rate = self.get_parameter('sample_rate').value
        self.aggressiveness = self.get_parameter('aggressiveness').value
        self.energy_threshold = self.get_parameter('energy_threshold').value
        self.wake_word_enabled = self.get_parameter('wake_word_enabled').value
        self.session_timeout = self.get_parameter('session_timeout').value
        
        # ─────────────────────────────────────────────────────────
        # STARE
        # ─────────────────────────────────────────────────────────
        self.is_speaking = False          # Starea curentă
        self.speech_frames = 0            # Câte frame-uri consecutive cu voce
        self.silence_frames = 0           # Câte frame-uri consecutive fără voce
        self.min_speech_frames = 5        # Câte frame-uri pentru a confirma voce (mărit pentru mai puține false positives)
        self.min_silence_frames = 10      # Câte frame-uri pentru a confirma tăcere
        self.is_robot_speaking = False    # True când robotul vorbește (TTS playback)
        self.is_gate_open = not self.wake_word_enabled
        self.session_timer = None
        self.is_speaking = False
        
        # ─────────────────────────────────────────────────────────
        # SUBSCRIBER
        # ─────────────────────────────────────────────────────────
        self.audio_sub = self.create_subscription(
            Audio,
            '/audio_raw',
            self.audio_callback,
            10
        )
        
        # Starea TTS - când robotul vorbește, ignorăm VAD
        self.robot_speaking_sub = self.create_subscription(
            Bool,
            '/is_speaking',
            self.robot_speaking_callback,
            10
        )

        self.wake_word_sub = self.create_subscription(
            WakeWord,
            '/wake_word',
            self.wake_word_callback,
            10
        )
        
        # Subscriber pentru starea sesiunii (de la wake_word_node)
        self.session_sub = self.create_subscription(
            Bool,
            '/session_active',
            self.session_callback,
            10
        )
        
        # ─────────────────────────────────────────────────────────
        # PUBLISHER
        # ─────────────────────────────────────────────────────────
        self.vad_pub = self.create_publisher(Bool, '/voice_activity', 10)
        self.end_session_pub = self.create_publisher(Bool, '/end_session_external', 10)
        
        # ─────────────────────────────────────────────────────────
        # INIȚIALIZARE WEBRTC VAD
        # ─────────────────────────────────────────────────────────
        self.vad = None
        if WEBRTCVAD_AVAILABLE:
            try:
                self.vad = webrtcvad.Vad(self.aggressiveness)
                self.get_logger().info(
                    f'🎯 VAD Node started (WebRTC, aggressiveness={self.aggressiveness})'
                )
            except Exception as e:
                self.get_logger().error(f'❌ Failed to init WebRTC VAD: {e}')
        else:
            self.get_logger().warn('⚠️ WebRTC VAD not available - using energy-based detection')
            self.get_logger().info(f'🎯 VAD Node started (energy threshold={self.energy_threshold})')
        
        self.frame_count = 0
        
        # Timer pentru reminder "READY TO LISTEN"
        self.reminder_timer = None
    
    def _start_reminder_timer(self):
        """Pornește timer-ul de reminder periodic."""
        if self.reminder_timer:
            self.reminder_timer.cancel()
        # Reminder la fiecare 3 secunde
        self.reminder_timer = self.create_timer(3.0, self._on_reminder)
        # Log imediat prima dată
        self.get_logger().info('🎤 READY TO LISTEN - speak now!')
    
    def _stop_reminder_timer(self):
        """Oprește timer-ul de reminder."""
        if self.reminder_timer:
            self.reminder_timer.cancel()
            self.reminder_timer = None
    
    def _on_reminder(self):
        """Callback pentru reminder periodic."""
        # Doar dacă poarta e deschisă și robotul nu vorbește
        if self.is_gate_open and not self.is_robot_speaking and not self.is_speaking:
            self.get_logger().info('🎤 READY TO LISTEN - speak now!')
        else:
            # Oprește timer-ul dacă condițiile nu mai sunt îndeplinite
            self._stop_reminder_timer()
    
    # ═══════════════════════════════════════════════════════════════════
    # CALLBACK AUDIO
    # ═══════════════════════════════════════════════════════════════════
    
    def robot_speaking_callback(self, msg: Bool):
        """Callback pentru starea TTS playback."""
        was_speaking = self.is_robot_speaking
        self.is_robot_speaking = msg.data
        
        # Când robotul începe să vorbească, oprim timer-ul de timeout și reminder-ul
        if self.is_robot_speaking and not was_speaking:
            self._stop_reminder_timer()
            if self.session_timer:
                self.session_timer.cancel()
                self.session_timer = None
            self.get_logger().debug('🤖 Robot is SPEAKING - please wait...')
        
        # Când robotul termină de vorbit, repornim timer-ul și reminder-ul
        elif not self.is_robot_speaking and was_speaking:
            if self.is_gate_open:
                self._reset_session_timer()
                self._start_reminder_timer()

    def session_callback(self, msg: Bool):
        """Callback pentru starea sesiunii (de la wake_word_node)."""
        if msg.data:
            # Session active - open gate
            if not self.is_gate_open:
                self.get_logger().debug('🔓 Session started - Opening Gate!')
                self.is_gate_open = True
                self._reset_session_timer()
        else:
            # Session ended - close gate
            if self.is_gate_open:
                self.get_logger().debug('🔒 Session ended - Closing Gate!')
                self.is_gate_open = False
                self.is_speaking = False
                self.speech_frames = 0
                self.silence_frames = 0
                
                # Stop session timer
                if self.session_timer:
                    self.session_timer.cancel()
                    self.session_timer = None
                
                # Publish voice_activity=False to stop ASR
                msg_out = Bool()
                msg_out.data = False
                self.vad_pub.publish(msg_out)

    def wake_word_callback(self, msg: WakeWord):
        # Deschide poarta cand aude "hello robot"
        self.get_logger().debug(f"🔓 Wake Word Detected: '{msg.word}' - Opening Gate!")
        self.is_gate_open = True
        self._reset_session_timer()

    def _reset_session_timer(self):
        # Reseteaza cronometrul sesiunii
        if self.session_timer:
            self.session_timer.cancel()
        self.session_timer = self.create_timer(self.session_timeout, self._on_session_timeout)

    def _on_session_timeout(self):
        # Nu închide poarta dacă robotul vorbește
        if self.is_robot_speaking:
            self.get_logger().debug('⏳ Session timeout skipped (robot still speaking)')
            return
            
        # Inchide poarta cand expira timpul
        self.get_logger().info("🔒 Session Timeout - Closing Gate.")
        self.is_gate_open = False
        self.is_speaking = False

        # Anuntam ca s-a terminat vorbirea
        msg = Bool()
        msg.data = False
        self.vad_pub.publish(msg)

        # Notifică wake_word_node să reseteze session_active
        end_msg = Bool()
        end_msg.data = True
        self.end_session_pub.publish(end_msg)

        if self.session_timer:
            self.session_timer.cancel()
            self.session_timer = None
    
    def audio_callback(self, msg: Audio):
        """Analizează fiecare chunk de audio pentru activitate vocală."""

        # Daca poarta e inchisa, ignoram tot
        if not self.is_gate_open:
            return
        
        # Nu procesa VAD când robotul vorbește (previne false positives)
        if self.is_robot_speaking:
            return
        
        audio = np.array(msg.data, dtype=np.int16)
        
        # Detectează voce
        has_voice = self._detect_voice(audio)
        
        # Logică de debounce (evită flickering)
        if has_voice:
            self.speech_frames += 1
            self.silence_frames = 0

            # Daca vorbim, resetam timer-ul
            if self.is_gate_open:
                self._reset_session_timer()
        else:
            self.silence_frames += 1
            self.speech_frames = 0
        
        # Schimbă starea doar după câteva frame-uri consecutive
        old_state = self.is_speaking
        
        if not self.is_speaking and self.speech_frames >= self.min_speech_frames:
            self.is_speaking = True
            self._stop_reminder_timer()  # Oprește reminder când user vorbește
            self.get_logger().debug('🗣️ Voice DETECTED - user is speaking')
        elif self.is_speaking and self.silence_frames >= self.min_silence_frames:
            self.is_speaking = False
            self.get_logger().debug('🤫 Voice ENDED - silence detected')
            # Repornește reminder-ul dacă suntem încă în modul listening
            if self.is_gate_open and not self.is_robot_speaking:
                self._start_reminder_timer()
        
        # Publică starea
        msg_out = Bool()
        msg_out.data = self.is_speaking
        self.vad_pub.publish(msg_out)
        
        self.frame_count += 1
    
    # ═══════════════════════════════════════════════════════════════════
    # DETECTARE VOCE
    # ═══════════════════════════════════════════════════════════════════
    def _detect_voice(self, audio: np.ndarray) -> bool:
        """
        Detectează dacă audio conține voce.
        Folosește WebRTC VAD sau fallback pe energie.
        """
        
        if self.vad is not None:
            # ─────────────────────────────────────────────────────
            # METODA 1: WebRTC VAD (mai precisă)
            # ─────────────────────────────────────────────────────
            try:
                # WebRTC VAD cere exact 10, 20 sau 30ms de audio
                # La 16kHz: 160, 320 sau 480 samples
                audio_bytes = audio.tobytes()
                
                # Ajustăm lungimea dacă e nevoie
                frame_len = len(audio)
                if frame_len == 320:  # 20ms la 16kHz
                    return self.vad.is_speech(audio_bytes, self.sample_rate)
                else:
                    # Fallback pe energie pentru lungimi non-standard
                    return self._energy_based_detection(audio)
            except Exception:
                return self._energy_based_detection(audio)
        else:
            # ─────────────────────────────────────────────────────
            # METODA 2: Energie RMS (fallback simplu)
            # ─────────────────────────────────────────────────────
            return self._energy_based_detection(audio)
    
    def _energy_based_detection(self, audio: np.ndarray) -> bool:
        """Detectare simplă bazată pe energia audio (RMS)."""
        # Calculează RMS (Root Mean Square) = energie medie
        rms = np.sqrt(np.mean(audio.astype(np.float32) ** 2))
        return rms > self.energy_threshold


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main(args=None):
    rclpy.init(args=args)
    node = VADNode()
    
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
