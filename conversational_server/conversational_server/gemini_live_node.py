#!/usr/bin/env python3
"""
Gemini Live Node.

Streams microphone audio to Google Gemini Multimodal Live API over WebSocket
and publishes assistant audio back into the existing ROS2 audio playback pipeline.
Provides ROS interface for bi-directional audio streaming.
"""
import base64
import json
import math
import os
import threading
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio, TextChunk, Transcription, RobotCommand, WakeWord
from conversational_client.conversation_utils import (
    advance_attention_focus,
    can_accept_control_action,
    decide_attention,
    detect_control_action,
    has_direct_robot_address,
    is_reengagement_phrase,
)
from std_msgs.msg import Bool, String, Int32
from .language_utils import ConversationLanguageTracker
from .prompt_config import load_prompt_defaults
from .realtime_audio_filter import PlaybackInputFilter, PlaybackInputFilterConfig
from .realtime_text_utils import (
    StickySpeakerTracker,
    ignored_short_transcript_reason,
    is_probable_assistant_echo,
    is_resume_request,
    normalize_realtime_text,
)
from .realtime_turn_utils import (
    can_request_realtime_response,
    continued_turn_response_delay_ms,
)

try:
    import websocket
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    print("websocket-client not installed. Run: pip install websocket-client")

try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False

GEMINI_LIVE_WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)


def _find_workspace_root() -> Path | None:
    for base in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in [base] + list(base.parents):
            if parent.name == "voice_ros2":
                return parent
    return None


class StatefulResampler:
    """Resampler that maintains phase state between chunks."""
    def __init__(self, original_rate, target_rate):
        self.original_rate = original_rate
        self.target_rate = target_rate
        self.step = original_rate / target_rate
        self.current_pos = 0.0

    def resample(self, audio_data):
        if self.original_rate == self.target_rate or audio_data.size == 0:
            return audio_data
        n_in = len(audio_data)
        out_positions = []
        curr = self.current_pos
        while curr < n_in:
            out_positions.append(curr)
            curr += self.step
        self.current_pos = curr - n_in
        if not out_positions:
            return np.array([], dtype=audio_data.dtype)
        resampled = np.interp(out_positions, np.arange(n_in), audio_data.astype(np.float32))
        return np.clip(np.round(resampled), -32768, 32767).astype(np.int16)


class GeminiLiveNode(Node):
    def __init__(self):
        super().__init__("gemini_live_node")

        if DOTENV_AVAILABLE:
            workspace_root = _find_workspace_root()
            env_path = workspace_root / ".env" if workspace_root else None
            if env_path and env_path.exists():
                load_dotenv(dotenv_path=env_path)

        self.declare_parameter("model", "gemini-2.5-flash-native-audio-latest")
        self.declare_parameter("voice", "Kore")
        self.declare_parameter("input_sample_rate", 16000)
        self.declare_parameter("api_sample_rate", 24000)
        self.declare_parameter("capture_during_playback", True)
        self.declare_parameter("vad_threshold", 0.80)
        self.declare_parameter("vad_prefix_padding_ms", 400)
        self.declare_parameter("vad_silence_duration_ms", 600)
        self.declare_parameter("response_create_delay_ms", 100)
        self.declare_parameter("continued_turn_response_delay_ms", 450)
        self.declare_parameter("local_response_gating", False)
        self.declare_parameter("short_transcript_dedupe_window_s", 4.0)
        self.declare_parameter("playback_input_filter_enabled", True)
        self.declare_parameter("playback_input_filter_post_playback_ms", 900)
        self.declare_parameter("playback_input_filter_min_rms_dbfs", -24.0)
        self.declare_parameter("playback_input_filter_highpass_hz", 300.0)
        self.declare_parameter("playback_input_filter_zcr_min", 0.05)
        self.declare_parameter("playback_input_filter_zcr_max", 0.35)
        self.declare_parameter("playback_input_filter_leak_margin_db", 6.0)
        self.declare_parameter("playback_input_filter_leak_decay_ms", 1200)
        self.declare_parameter("playback_input_filter_hits_required", 4)
        self.declare_parameter("playback_input_filter_hold_ms", 240)
        self.declare_parameter("assistant_echo_filter_enabled", True)
        self.declare_parameter("assistant_echo_window_s", 8.0)
        self.declare_parameter("assistant_echo_similarity_threshold", 88.0)
        self.declare_parameter("assistant_echo_min_length", 8)
        self.declare_parameter("reconnect_delay_s", 3.0)
        # A speaker-change reconnect is *intentional* (persona refresh), not an
        # error — it must be fast so the user isn't left waiting after being
        # recognized. Error reconnects still use the longer reconnect_delay_s.
        self.declare_parameter("intentional_reconnect_delay_s", 0.3)
        # Within this window after a speaker-change reconnect, treat further
        # speaker switches as transient ID flips and update context cheaply
        # (mid-session inject) instead of tearing down the session again.
        self.declare_parameter("speaker_reconnect_debounce_s", 6.0)
        self.declare_parameter("sticky_speaker_timeout_s", 60.0)
        self.declare_parameter("speaker_switch_hits_required", 2)
        self.declare_parameter("language_switch_hits_required", 2)
        self.declare_parameter("focus_timeout_s", 45.0)
        self.declare_parameter("focus_recognition_window_s", 3.0)
        self.declare_parameter("allow_known_speaker_switch_without_address", True)
        self.declare_parameter("utterance_capture_prefix_ms", 400)
        self.declare_parameter("utterance_capture_min_ms", 800)
        self.declare_parameter("name_context_wait_ms", 950)
        self.declare_parameter("google_search_enabled", False)
        self.declare_parameter('doa_enabled', True)
        self.declare_parameter('doa_focus_margin', 90.0)
        # Let Gemini decide (via function-calling) when the current speaker
        # introduces their own name, instead of relying on static regex phrases.
        self.declare_parameter("name_capture_tool_enabled", True)
        self.declare_parameter("enable_local_fillers", False)
        self.declare_parameter("filler_chance", 0.70)
        self.declare_parameter("filler_volume", 0.80)
        self.declare_parameter("fillers_dir", "/home/delia/voice_ros2/voices/fillers")
        self.declare_parameter(
            "instructions",
            str(load_prompt_defaults().get("realtime_instructions", "")),
        )

        self.model = str(self.get_parameter("model").value)
        self.voice = str(self.get_parameter("voice").value)
        self.input_sample_rate = int(self.get_parameter("input_sample_rate").value)
        self.api_sample_rate = int(self.get_parameter("api_sample_rate").value)
        self.capture_during_playback = bool(self.get_parameter("capture_during_playback").value)
        self.vad_threshold = float(self.get_parameter("vad_threshold").value)
        self.vad_prefix_padding_ms = int(self.get_parameter("vad_prefix_padding_ms").value)
        self.vad_silence_duration_ms = int(self.get_parameter("vad_silence_duration_ms").value)
        self.response_create_delay_ms = max(0, int(self.get_parameter("response_create_delay_ms").value))
        self.continued_turn_response_delay_ms = max(0, int(self.get_parameter("continued_turn_response_delay_ms").value))
        self.local_response_gating = bool(self.get_parameter("local_response_gating").value)
        self.short_transcript_dedupe_window_s = float(self.get_parameter("short_transcript_dedupe_window_s").value)
        self.playback_input_filter_enabled = bool(self.get_parameter("playback_input_filter_enabled").value)
        self.playback_input_filter_post_playback_ms = max(0, int(self.get_parameter("playback_input_filter_post_playback_ms").value))
        zcr_min = max(0.0, min(1.0, float(self.get_parameter("playback_input_filter_zcr_min").value)))
        zcr_max = max(0.0, min(1.0, float(self.get_parameter("playback_input_filter_zcr_max").value)))
        if zcr_min > zcr_max:
            zcr_min, zcr_max = zcr_max, zcr_min
        self.playback_input_filter = PlaybackInputFilter(
            PlaybackInputFilterConfig(
                min_rms_dbfs=float(self.get_parameter("playback_input_filter_min_rms_dbfs").value),
                highpass_hz=float(self.get_parameter("playback_input_filter_highpass_hz").value),
                zcr_min=zcr_min,
                zcr_max=zcr_max,
                leak_margin_db=float(self.get_parameter("playback_input_filter_leak_margin_db").value),
                leak_decay_ms=max(0, int(self.get_parameter("playback_input_filter_leak_decay_ms").value)),
                hits_required=max(1, int(self.get_parameter("playback_input_filter_hits_required").value)),
                hold_ms=max(0, int(self.get_parameter("playback_input_filter_hold_ms").value)),
            )
        )
        self.assistant_echo_filter_enabled = bool(self.get_parameter("assistant_echo_filter_enabled").value)
        self.assistant_echo_window_s = max(0.0, float(self.get_parameter("assistant_echo_window_s").value))
        self.assistant_echo_similarity_threshold = float(self.get_parameter("assistant_echo_similarity_threshold").value)
        self.assistant_echo_min_length = max(1, int(self.get_parameter("assistant_echo_min_length").value))
        self.reconnect_delay_s = float(self.get_parameter("reconnect_delay_s").value)
        self.intentional_reconnect_delay_s = float(
            self.get_parameter("intentional_reconnect_delay_s").value
        )
        self.speaker_reconnect_debounce_s = float(
            self.get_parameter("speaker_reconnect_debounce_s").value
        )
        self._last_speaker_reconnect_s = 0.0
        self.utterance_capture_prefix_ms = int(self.get_parameter("utterance_capture_prefix_ms").value)
        self.utterance_capture_min_ms = int(self.get_parameter("utterance_capture_min_ms").value)
        self.name_context_wait_ms = max(0, int(self.get_parameter("name_context_wait_ms").value))
        self.google_search_enabled = bool(self.get_parameter("google_search_enabled").value)
        self.doa_enabled = bool(self.get_parameter('doa_enabled').value)
        self.doa_focus_margin = float(
            self.get_parameter('doa_focus_margin').value
        )
        self.name_capture_tool_enabled = bool(self.get_parameter("name_capture_tool_enabled").value)
        self.enable_local_fillers = bool(self.get_parameter("enable_local_fillers").value)
        self.filler_chance = float(self.get_parameter("filler_chance").value)
        self.filler_volume = float(self.get_parameter("filler_volume").value)
        self.fillers_dir = str(self.get_parameter("fillers_dir").value)
        self.fillers_cache = {'ro': [], 'en': []}
        self._user_audio_frames_sent = 0
        if self.enable_local_fillers:
            self._precache_fillers()

        self.base_instructions = str(self.get_parameter("instructions").value)
        self.speaker_tracker = StickySpeakerTracker(
            float(self.get_parameter("sticky_speaker_timeout_s").value),
            int(self.get_parameter("speaker_switch_hits_required").value),
        )
        self.focus_timeout_s = float(self.get_parameter("focus_timeout_s").value)
        self.focus_recognition_window_s = float(self.get_parameter("focus_recognition_window_s").value)
        self.allow_known_speaker_switch_without_address = bool(
            self.get_parameter("allow_known_speaker_switch_without_address").value
        )
        self.language_tracker = ConversationLanguageTracker(
            int(self.get_parameter("language_switch_hits_required").value)
        )

        self.api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            self.get_logger().error("GOOGLE_API_KEY (or GEMINI_API_KEY) not set!")
            raise RuntimeError("GOOGLE_API_KEY not set")
        if not WEBSOCKET_AVAILABLE:
            raise RuntimeError("websocket-client not available")

        # Conversation state
        self.session_active = False
        self.conversation_paused = False
        self._pause_pending = False
        self.robot_speaking = False
        self.waiting_for_robot_confirmation = False
        self.current_speaker = "Unknown"
        self.last_raw_speaker = "Unknown"
        self.current_backend = "gemini_live"
        self.focused_speaker = "Unknown"
        self.last_focus_time = 0.0
        self.pending_focus_speaker = "Unknown"
        self.pending_focus_at = 0.0
        self.person_context = {
            "speaker": "Unknown",
            "preferred_name": "",
            "preferred_language": "",
            "facts": [],
        }
        self._speaker_context_cache = {}
        self._setup_speaker = 'Unknown'
        self._setup_preferred_name = ''

        # DOA tracking
        self.latest_doa_angle = -1
        self.focused_doa_angle = -1
        self.current_segment_doa_angles = []
        self.doa_history = []

        # WebSocket state
        self._ws_app = None
        self._ws_thread = threading.Thread(
            target=self._websocket_loop, daemon=True, name="gemini-live-ws"
        )
        self._running = True
        self._connected = threading.Event()
        self._send_lock = threading.Lock()
        self._setup_sent = False
        self._last_reconnect_time = 0.0
        self._reconnect_cooldown_s = 1.5
        self._pending_reconnect_reason = ""
        self._intentional_reconnect = False  # True when we close WS on purpose (context update)

        # Response tracking
        self._assistant_text = defaultdict(str)
        self._published_final_items = set()
        self._active_turn_id = ""
        self._response_active = False
        self._response_create_pending = False
        self._user_speaking = False
        self._ignore_model_response = False
        self._audio_chunks_sent = 0
        self._current_turn_audio_started = False

        # Audio
        self._last_playback_progress = {"stream_id": "", "item_id": "", "played_ms": 0, "stopped": False}
        self._last_truncate_signature = ("", -1)
        self._last_truncate_time = 0.0
        self._pending_resume_text = ""
        self._pending_resume_remaining = ""
        self._pending_resume_played_ms = 0
        self._resume_requested = False
        self._last_response_request_item_id = ""
        self._pending_response_timer = None
        self._pending_response_item_id = ""
        self._pending_response_reason = ""
        self._deferred_response_item_id = ""
        self._deferred_response_reason = ""
        self._assistant_name_question_active = False
        self._paused_transcript_pending = ""
        self._paused_transcript_at = 0.0
        self._last_accepted_user_transcript_norm = ""
        self._last_accepted_user_transcript_at = 0.0
        self._last_robot_speaking_end_ms = 0
        self._playback_guard_was_active = False
        self._playback_frames_blocked = 0
        self._last_assistant_audio_at = 0.0
        self._recent_assistant_outputs = []
        self._recent_input_audio = []
        self._current_user_audio = []
        self._capture_user_audio = False
        self._current_user_transcript = ""
        self._current_input_sample_rate = self.input_sample_rate
        self._current_input_channels = 1
        self._goodbye_pending = False  # True after user said goodbye, waiting for Gemini to finish
        self._pending_context_update = False  # True when context inject was blocked by active response
        self._is_new_user_turn = True


        # Publishers
        self.audio_pub = self.create_publisher(Audio, "audio_out", 10)
        self.user_audio_segment_pub = self.create_publisher(Audio, "realtime_user_audio_segment", 10)
        self.transcription_pub = self.create_publisher(Transcription, "transcription", 10)
        self.stream_pub = self.create_publisher(TextChunk, "llm_stream", 10)
        self.response_pub = self.create_publisher(Transcription, "llm_response", 10)
        self.pause_state_pub = self.create_publisher(Bool, "conversation_pause", 10)
        self.tts_stop_pub = self.create_publisher(Bool, "stop_playback", 10)
        self.tts_command_pub = self.create_publisher(String, "tts_command", 10)
        self.status_pub = self.create_publisher(String, "gemini_live_status", 10)
        self.session_pub = self.create_publisher(Bool, "session_active", 10)
        self.end_session_pub = self.create_publisher(Bool, "end_session_external", 10)
        self.introduced_name_pub = self.create_publisher(String, "introduced_name", 10)

        # Subscriptions
        self.audio_sub = self.create_subscription(Audio, "audio_clean", self.audio_callback, 10)
        self.session_sub = self.create_subscription(Bool, "session_active", self.session_callback, 10)
        self.speaking_sub = self.create_subscription(Bool, "is_speaking", self.speaking_callback, 10)
        self.stop_sub = self.create_subscription(Bool, "stop_playback", self.stop_callback, 10)
        self.progress_sub = self.create_subscription(String, "audio_playback_progress", self.playback_progress_callback, 10)
        self.speaker_sub = self.create_subscription(String, "speaker_id", self.speaker_callback, 10)
        self.robot_command_sub = self.create_subscription(RobotCommand, "robot_commands", self.robot_command_callback, 10)
        self.robot_status_sub = self.create_subscription(String, "robot_command_status", self.robot_status_callback, 10)
        self.backend_sub = self.create_subscription(String, "conversation_backend", self.backend_callback, 10)
        self.person_context_sub = self.create_subscription(String, "person_context", self.person_context_callback, 10)
        self.pause_sub = self.create_subscription(Bool, "conversation_pause", self.pause_callback, 10)
        self.vad_sub = self.create_subscription(Bool, "voice_activity", self.vad_callback, 10)
        self.wake_word_sub = self.create_subscription(WakeWord, "wake_word", self.wake_word_callback, 10)
        self.doa_sub = self.create_subscription(
            Int32, 'doa_angle', self.doa_callback, 10
        )

        self._ws_thread.start()
        self._publish_status("connecting")
        self.get_logger().info(f"Gemini Live Node started: model={self.model}, voice={self.voice}")

    def destroy_node(self):
        self._running = False
        self._connected.clear()
        self._cancel_pending_response_create()
        try:
            if self._ws_app is not None:
                self._ws_app.close()
        except Exception:
            pass
        return super().destroy_node()

    # ─── ROS Callbacks ───────────────────────────────────────────────────────

    def session_callback(self, msg: Bool):
        self.session_active = bool(msg.data)
        if self.session_active:
            self.get_logger().debug("Gemini Live session ACTIVE")
            # Start capturing user audio immediately so speaker_id_node gets
            # a segment at the end of the first user turn.
            self._start_user_audio_capture()
            self._is_new_user_turn = True
        else:
            self.get_logger().debug("Gemini Live session INACTIVE")
            self.conversation_paused = False
            # Cache the speaker and context (do not reset current_speaker or speaker_tracker)
            self.language_tracker.reset()
            self.focused_speaker = "Unknown"
            self.last_focus_time = 0.0
            self.pending_focus_speaker = "Unknown"
            self.pending_focus_at = 0.0
            self._cancel_and_clear()

    def wake_word_callback(self, msg: WakeWord):
        """
        Inject a greeting into Gemini when wake word is detected.

        Instead of playing a cached 'ack' sound, let Gemini respond naturally
        to the greeting so the conversation feels alive from the first word.
        """
        if self.current_backend != "gemini_live":
            return
        if not self._connected.is_set() or not self._setup_sent:
            return
        word = (msg.word or "").strip()
        # Only act on the hello wake word, not barge-in or stop models
        if "hello" not in word and "wake" not in word:
            return
        self.get_logger().info(f"Gemini: injecting greeting for wake word '{word}'")
        if self.doa_enabled and self.latest_doa_angle != -1:
            self.focused_doa_angle = self.latest_doa_angle
            self.doa_history = [self.focused_doa_angle]
            self.get_logger().info(
                f'Locking focused DOA angle to wake word direction: '
                f'{self.focused_doa_angle}°'
            )
        # Inject as a user turn so Gemini responds with a natural greeting
        self._send_raw({
            "clientContent": {
                "turns": [{"role": "user", "parts": [{"text": "Hello!"}]}],
                "turnComplete": True,
            }
        })

    def speaking_callback(self, msg: Bool):
        was_speaking = self.robot_speaking
        self.robot_speaking = bool(msg.data)
        now_ms = int(time.time() * 1000)
        if self.robot_speaking:
            self._last_assistant_audio_at = time.monotonic()
            if not was_speaking and self.playback_input_filter_enabled:
                self.playback_input_filter.reset()
                self._playback_frames_blocked = 0
            # Clear user audio buffer when robot starts speaking to avoid contamination/leak!
            self._current_user_audio = []
        elif was_speaking:
            self._last_robot_speaking_end_ms = now_ms
            self._is_new_user_turn = True
            self.get_logger().info("Gemini Live: robot finished speaking, turn initialized")

    def pause_callback(self, msg: Bool):
        self._apply_pause_state(bool(msg.data), publish=False)

    def _apply_pause_state(self, paused: bool, *, publish: bool):
        if paused == self.conversation_paused:
            return
        self.conversation_paused = paused
        if paused:
            self.get_logger().info("Gemini Live conversation paused")
            self._cancel_pending_response_create()
            # Silence any reply already in flight so the robot goes quiet at once.
            self._mark_response_inactive()
            stop_msg = Bool()
            stop_msg.data = True
            self.tts_stop_pub.publish(stop_msg)
        else:
            self.get_logger().info("Gemini Live conversation resumed")
        if not paused and self._paused_transcript_pending:
            self._schedule_response_create("", reason="resume_from_pause")
            self._paused_transcript_pending = ""
            self._paused_transcript_at = 0.0
        if publish:
            msg = Bool()
            msg.data = paused
            self.pause_state_pub.publish(msg)

    def backend_callback(self, msg: String):
        backend = msg.data.strip() or "legacy"
        if backend == self.current_backend:
            return
        self.current_backend = backend
        if backend != "gemini_live":
            self._cancel_and_clear()
        else:
            if self._connected.is_set() and not self._setup_sent:
                self._send_setup()

    def stop_callback(self, msg: Bool):
        if msg.data:
            self._clear_playback_progress()
            self._mark_response_inactive()

    def playback_progress_callback(self, msg: String):
        try:
            payload = json.loads(msg.data)
        except Exception:
            return
        if bool(payload.get("stopped", False)):
            self._clear_playback_progress()
            self._mark_response_inactive()
            return
        self._last_playback_progress = {
            "stream_id": str(payload.get("stream_id", "") or ""),
            "item_id": str(payload.get("item_id", "") or ""),
            "played_ms": int(payload.get("played_ms", 0) or 0),
            "stopped": bool(payload.get("stopped", False)),
        }

    def speaker_callback(self, msg: String):
        raw_speaker = msg.data.strip() or 'Unknown'
        self.last_raw_speaker = raw_speaker
        speaker = self.speaker_tracker.update(raw_speaker)
        if raw_speaker != 'Unknown' and speaker == raw_speaker:
            self.pending_focus_speaker = raw_speaker
            self.pending_focus_at = time.monotonic()
            if self.doa_enabled and self.latest_doa_angle != -1:
                self.focused_doa_angle = self.latest_doa_angle
                self.doa_history = [self.focused_doa_angle]
                self.get_logger().info(
                    f'Locking focused DOA angle to identified speaker: '
                    f'{self.focused_doa_angle}°'
                )
            
        speaker_changed = speaker != self.current_speaker
        if speaker_changed:
            self.current_speaker = speaker

            # Sync self.person_context with cached speaker context if available
            if speaker in self._speaker_context_cache:
                self.person_context = self._speaker_context_cache[speaker]
                self.language_tracker.seed(
                    str(self.person_context.get('preferred_language', ''))
                )

        # A speaker change no longer forces a session rebuild or a text
        # injection. self.current_speaker and self.person_context are kept fresh
        # above, and the model pulls the latest identity on demand via the
        # get_speaker_info tool (declared in _send_setup, served in the toolCall
        # handler). This removes the reconnect stall on every speaker switch.

    def person_context_callback(self, msg: String):
        try:
            payload = json.loads(msg.data)
        except Exception:
            return
        new_context = {
            'speaker': str(payload.get('speaker', 'Unknown') or 'Unknown'),
            'preferred_name': str(payload.get('preferred_name', '') or ''),
            'preferred_language': str(
                payload.get('preferred_language', '') or ''
            ),
            'facts': list(payload.get('facts', []) or []),
        }

        # Cache context for this speaker
        speaker = new_context['speaker']
        if speaker != 'Unknown':
            self._speaker_context_cache[speaker] = new_context

        if new_context == self.person_context:
            return

        self.person_context = new_context
        self.language_tracker.seed(
            str(self.person_context.get('preferred_language', ''))
        )
        
        # No mid-session injection needed. The model uses get_speaker_info tool CALL.
        pass

    def robot_command_callback(self, msg: RobotCommand):
        # Suppress assistant chatter during robot commands
        pass

    def robot_status_callback(self, msg: String):
        status = msg.data.strip()
        mute_statuses = {"confirmation_required"}
        resume_statuses = {
            "confirmation_accepted", "confirmation_rejected", "confirmation_timeout",
            "canceled", "completed", "executing", "stopped",
        }
        if status in mute_statuses and not self.waiting_for_robot_confirmation:
            self.waiting_for_robot_confirmation = True
            self._request_reconnect("robot_confirmation_required")
        elif status in resume_statuses and self.waiting_for_robot_confirmation:
            self.waiting_for_robot_confirmation = False
            self._request_reconnect("robot_confirmation_done")

    def doa_callback(self, msg: Int32):
        if not self.doa_enabled:
            return
        self.latest_doa_angle = msg.data
        if self.session_active and not self.robot_speaking:
            self.current_segment_doa_angles.append(msg.data)
            self.doa_history.append(msg.data)
            if len(self.doa_history) > 10:
                self.doa_history.pop(0)

    def audio_callback(self, msg: Audio):
        if not self.session_active:
            return
        if self.current_backend != "gemini_live":
            return
        if not msg.data:
            return

        input_sample_rate = int(msg.sample_rate or self.input_sample_rate)
        self._current_input_sample_rate = input_sample_rate
        self._current_input_channels = int(msg.channels or 1)

        # Buffer incoming user audio during the current turn, even during a reconnect gap
        if not self.robot_speaking:
            self._remember_input_audio(msg.data)
            self._user_audio_frames_sent += 1
            if self._capture_user_audio:
                self._current_user_audio.extend(msg.data)

        if not self._connected.is_set() or not self._setup_sent:
            return
        if not self.capture_during_playback and self.robot_speaking:
            return

        now_ms = int(time.time() * 1000)
        pcm = np.array(msg.data, dtype=np.int16)
        if pcm.size == 0:
            return

        if self._should_filter_playback_input(now_ms):
            if not self.playback_input_filter.should_forward(
                pcm, sample_rate=input_sample_rate, now_ms=now_ms
            ):
                self._playback_frames_blocked += 1
                return
        elif self._playback_guard_was_active:
            self.playback_input_filter.reset()
            self._playback_guard_was_active = False
            self._playback_frames_blocked = 0

        # Deferred Context Injection disabled. The model uses get_speaker_info tool CALL.
        if self._is_new_user_turn:
            self._is_new_user_turn = False
            self._user_audio_frames_sent = 0

        # Spatial DOA Gating
        if (self.doa_enabled and self.focused_doa_angle != -1 and
                self.latest_doa_angle != -1):
            gating_doa = (
                self._get_circular_average(self.doa_history)
                if self.doa_history
                else self.latest_doa_angle
            )
            if gating_doa != -1:
                dist = self._angular_distance(
                    gating_doa, self.focused_doa_angle
                )
                if dist > self.doa_focus_margin:
                    pcm = np.zeros_like(pcm)
                    if self._audio_chunks_sent % 50 == 0:
                        self.get_logger().info(
                            f'🚫 DOA Gating: mute (gating_DOA={gating_doa}°, '
                            f'focus={self.focused_doa_angle}°, diff={dist:.1f}°)'
                        )

        # Resample to API rate (Gemini expects 16kHz)
        pcm_api = self._resample_pcm16(pcm, input_sample_rate, self.api_sample_rate)
        encoded = base64.b64encode(pcm_api.tobytes()).decode("ascii")
        self._send_raw({
            "realtimeInput": {
                "mediaChunks": [{"mimeType": f"audio/pcm;rate={self.api_sample_rate}", "data": encoded}]
            }
        })
        self._audio_chunks_sent += 1

    # ─── WebSocket ───────────────────────────────────────────────────────────

    def _websocket_loop(self):
        while self._running:
            url = f"{GEMINI_LIVE_WS_URL}?key={self.api_key}"
            self._setup_sent = False
            self._ws_app = websocket.WebSocketApp(
                url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            try:
                self._ws_app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                self.get_logger().error(f"Gemini Live WebSocket failed: {exc}")
            self._connected.clear()
            if self._running:
                # Intentional reconnects (persona/context refresh) reopen almost
                # immediately; only genuine errors back off the full delay.
                if self._intentional_reconnect:
                    time.sleep(max(0.0, self.intentional_reconnect_delay_s))
                else:
                    time.sleep(max(1.0, self.reconnect_delay_s))

    def _on_open(self, ws):
        self._intentional_reconnect = False  # Reset flag on successful reconnect
        self.get_logger().info("Connected to Gemini Live API")
        self._connected.set()
        self._publish_status("online")
        self._is_new_user_turn = True
        self._send_setup()

    def _on_close(self, ws, status_code, msg):
        self._connected.clear()
        self._setup_sent = False
        # Publish 'reconnecting' for intentional closes (context/speaker updates)
        # so backend_manager does NOT fall back to legacy during the brief gap.
        if self._intentional_reconnect:
            self._publish_status("reconnecting")
        else:
            self._publish_status("offline")
        self.get_logger().warn(f"Gemini Live disconnected: code={status_code}, msg={msg}")

    def _on_error(self, ws, error):
        self._publish_status("error")
        self.get_logger().error(f"Gemini Live error: {error}")

    def _on_message(self, ws, message):
        try:
            event = json.loads(message)
        except json.JSONDecodeError:
            self.get_logger().warn("Received non-JSON from Gemini Live")
            return

        # Error from server
        if "error" in event:
            err = event["error"]
            self.get_logger().error(f"Gemini Live API error: {err}")
            self._publish_status("error")
            return

        # Setup acknowledgement
        if "setupComplete" in event:
            self.get_logger().debug("Gemini Live session setup confirmed")
            self._replay_accumulated_audio()
            return

        # Tool calls from server (Google Search or custom tools)
        if 'toolCall' in event:
            tool_call = event.get('toolCall', {})
            function_calls = tool_call.get('functionCalls', [])
            self.get_logger().info(f'Gemini Live toolCall: {len(function_calls)} calls')

            responses = []
            pause_request = None  # applied AFTER the toolResponse is sent
            for fc in function_calls:
                call_id = fc.get('id', '')
                name = fc.get('name', '')
                args = fc.get('args', {})
                self.get_logger().info(f'  - tool: {name}, args: {args}')
                if name == 'remember_person':
                    result = self._handle_remember_person(args)
                    responses.append({
                        'id': call_id,
                        'name': name,
                        'response': {'result': result}
                    })
                elif name == 'get_speaker_info':
                    pref_name = self._voice_correlated_preferred_name() or 'Unknown'
                    pref_lang = (
                        self.person_context.get('preferred_language', '') or
                        'Unknown'
                    )
                    facts = self.person_context.get('facts', []) or []
                    
                    tool_output = {
                        'speaker': self.current_speaker,
                        'preferred_name': pref_name,
                        'preferred_language': pref_lang,
                        'facts': facts,
                    }
                    responses.append({
                        'id': call_id,
                        'name': name,
                        'response': {
                            'output': tool_output,
                            'result': tool_output,
                            **tool_output
                        }
                    })
                elif name == 'set_conversation_pause':
                    # Robust pause/resume: the model decides from the audio's
                    # meaning, so it works even when the transcript is garbled
                    # into a non-Latin script and dropped by the text filters.
                    # Defer the state change until after the toolResponse is
                    # sent — _apply_pause_state may reconnect (closing the
                    # socket), and we must not lose the response.
                    pause_request = bool(args.get('paused', True))
                    responses.append({
                        'id': call_id,
                        'name': name,
                        'response': {'result': 'paused' if pause_request else 'resumed'}
                    })
                else:
                    responses.append({
                        'id': call_id,
                        'name': name,
                        'response': {'result': 'ok'}
                    })
            if responses:
                self._send_raw({
                    'toolResponse': {
                        'functionResponses': responses
                    }
                })
            if pause_request is not None:
                if pause_request:
                    self._pause_pending = True
                    self.get_logger().info("Gemini Live: pause request received, deferring pause until turn completes")
                else:
                    self._pause_pending = False
                    self._apply_pause_state(False, publish=True)
            return

        server_content = event.get("serverContent")
        if not server_content:
            return

        # Barge-in: model was interrupted by user speech
        if server_content.get("interrupted"):
            self.get_logger().info("Gemini Live: user interrupted assistant")
            self._user_speaking = True
            self._cancel_pending_response_create()
            self._mark_response_inactive()
            stop_msg = Bool()
            stop_msg.data = True
            self.tts_stop_pub.publish(stop_msg)
            self._publish_captured_user_audio_segment()
            self._start_user_audio_capture()  # restart for the next utterance
            self._schedule_deferred_response_after_speech_stop()
            self._current_user_transcript = ""
            if self._pause_pending:
                self.get_logger().info("Gemini Live: interruption during pending pause, applying pause state now")
                self._apply_pause_state(True, publish=True)
                self._pause_pending = False
            return

        # Input transcription (user speech)
        input_transcription = server_content.get("inputTranscription")
        if input_transcription:
            # Gemini streams the input transcription as incremental chunks that
            # already carry their own spacing (a new word arrives with a leading
            # space, a continuation without one). Concatenate raw — do NOT strip
            # per chunk or insert spaces, otherwise single words get split apart
            # ("robot" -> "ro bot"). Regressed once in 6790ad8; keep it raw.
            transcript = str(input_transcription.get("text", "") or "")
            if transcript:
                self._current_user_transcript += transcript
                self.get_logger().debug(f"Gemini Live intermediate chunk: '{transcript}' (accumulated: '{self._current_user_transcript}')")

        # Model turn (assistant response)
        model_turn = server_content.get("modelTurn")
        if model_turn:
            if self._ignore_model_response:
                return
            parts = model_turn.get("parts", []) or []
            for part in parts:
                inline_data = part.get("inlineData")
                if inline_data:
                    self._handle_output_audio(inline_data)
                text = part.get("text")
                if text:
                    self._handle_text_delta_gemini(text)
                # Function call (Google Search or other tools)
                function_call = part.get("functionCall")
                if function_call:
                    self.get_logger().info(f"Gemini function call: {function_call.get('name')}")

        # Turn complete
        if server_content.get("turnComplete"):
            self.get_logger().debug("Gemini Live: turn complete")
            self._user_speaking = False
            self._publish_captured_user_audio_segment()
            self._start_user_audio_capture()
            self._is_new_user_turn = True
            self.get_logger().info("Gemini Live: turn complete, turn initialized")

            # Calculate average DOA for the segment
            avg_doa = -1
            if self.doa_enabled and self.current_segment_doa_angles:
                avg_doa = self._get_circular_average(
                    self.current_segment_doa_angles
                )
                self.get_logger().info(f'DOA segment average: {avg_doa}°')
            self.current_segment_doa_angles = []

            # Check if side conversation
            if (self.doa_enabled and self.focused_doa_angle != -1 and 
                    avg_doa != -1):
                dist = self._angular_distance(avg_doa, self.focused_doa_angle)
                if dist > self.doa_focus_margin:
                    transcript = self._current_user_transcript or ''
                    normalized = normalize_realtime_text(transcript)
                    direct_address = has_direct_robot_address(normalized)
                    reengagement = is_reengagement_phrase(normalized)
                    
                    if not (direct_address or reengagement):
                        self.get_logger().info(
                            f'🚫 Blocking side conversation: DOA={avg_doa}°, '
                            f'focus={self.focused_doa_angle}°, diff={dist:.1f}°'
                        )
                        self._ignore_model_response = True
                        stop_msg = Bool()
                        stop_msg.data = True
                        self.tts_stop_pub.publish(stop_msg)
                        self._current_user_transcript = ''
                        self._handle_turn_complete()
                        return

            if self._current_user_transcript:
                self._handle_input_transcript(self._current_user_transcript)
                self._current_user_transcript = ""
            self._handle_turn_complete()
            self._ignore_model_response = False

    def _handle_output_audio(self, inline_data: dict):
        # While paused ("hold on … until I'm back") the robot must stay silent
        # even though the native-audio model keeps generating. We still receive
        # input transcription (so resume/re-engagement is detected), but drop the
        # model's speech instead of playing it.
        if self.conversation_paused:
            return
        mime = str(inline_data.get("mimeType", "") or "")
        data_b64 = str(inline_data.get("data", "") or "")
        if not data_b64:
            return

        try:
            pcm_bytes = base64.b64decode(data_b64)
        except Exception as exc:
            self.get_logger().error(f"Failed to decode Gemini audio: {exc}")
            return

        pcm = np.frombuffer(pcm_bytes, dtype=np.int16)
        if pcm.size == 0:
            return

        if not self._current_turn_audio_started:
            self._current_turn_audio_started = True
            self._mark_response_active(self._active_turn_id)
            self.get_logger().debug("Gemini Live started audio output")

        self._last_assistant_audio_at = time.monotonic()

        out = Audio()
        out.sample_rate = 24000
        out.channels = 1
        out.data = pcm.tolist()
        out.stream_id = self._active_turn_id
        out.item_id = self._active_turn_id
        self.audio_pub.publish(out)

    def _handle_text_delta_gemini(self, text: str):
        if not text:
            return
        self._mark_response_active(self._active_turn_id)
        self._assistant_text[self._active_turn_id] += text
        chunk = TextChunk()
        chunk.text = text
        chunk.language = ""
        chunk.is_final = False
        chunk.session_id = self._active_turn_id
        self.stream_pub.publish(chunk)

    def _handle_turn_complete(self):
        turn_id = self._active_turn_id
        text = self._assistant_text.get(turn_id, "").strip()
        if text:
            self._publish_assistant_final(turn_id, turn_id)
            self._remember_assistant_output(text)

        self._mark_response_inactive(turn_id)
        self._current_turn_audio_started = False
        self._last_response_request_item_id = ""
        self._assistant_name_question_active = False
        self._last_accepted_user_transcript_norm = ""

        if self._pause_pending:
            self.get_logger().info("Gemini Live: turn complete, applying deferred pause state")
            self._apply_pause_state(True, publish=True)
            self._pause_pending = False

        # Generate a new turn id for next response
        import uuid
        self._active_turn_id = str(uuid.uuid4())[:8]

        # If goodbye was detected, close the session now that Gemini finished speaking
        if self._goodbye_pending:
            self._goodbye_pending = False
            self.get_logger().info("Gemini finished goodbye reply — closing session")
            session_msg = Bool()
            session_msg.data = False
            self.session_pub.publish(session_msg)
            end_msg = Bool()
            end_msg.data = True
            self.end_session_pub.publish(end_msg)
            return

        if self._pending_resume_text:
            self._clear_pending_resume()
            self._send_setup()
        if self._pending_reconnect_reason:
            reason = self._pending_reconnect_reason
            self._pending_reconnect_reason = ""
            self._request_reconnect(reason)
            
        if not self._user_speaking and not self.conversation_paused and not self.waiting_for_robot_confirmation:
            self._schedule_deferred_response_after_response_done()


    def _handle_input_transcript(self, transcript: str):
        ignore_reason = self._ignored_transcript_reason(transcript)
        if ignore_reason:
            self.get_logger().info(f"Ignoring transcript ({ignore_reason}): {transcript}")
            return

        self.get_logger().info(f"Gemini user transcript: {transcript}")
        focus_candidate = self._consume_focus_candidate()
        normalized = self._normalize_text(transcript)
        direct_address = has_direct_robot_address(normalized)
        control_action = detect_control_action(normalized)
        reengagement = is_reengagement_phrase(normalized)

        if normalized:
            self._last_accepted_user_transcript_norm = normalized
            self._last_accepted_user_transcript_at = time.monotonic()

        if control_action == "hold_on" and can_accept_control_action(
            control_action,
            current_speaker=self.current_speaker,
            focused_speaker=self.focused_speaker,
            session_active=self.session_active,
            conversation_paused=self.conversation_paused,
            direct_address=direct_address,
            normalized_text=normalized,
        ):
            self._apply_pause_state(True, publish=True)
            return

        allow, reason, effective_focus, effective_focus_time = decide_attention(
            session_active=self.session_active,
            conversation_paused=self.conversation_paused,
            current_speaker=self.current_speaker,
            focused_speaker=self.focused_speaker,
            last_focus_time=self.last_focus_time,
            focus_timeout_s=self.focus_timeout_s,
            allow_known_speaker_switch_without_address=self.allow_known_speaker_switch_without_address,
            direct_address=direct_address,
            reengagement=reengagement,
            robot_directive=False,
            control_action=control_action,
            normalized_text=normalized,
        )
        self.focused_speaker = effective_focus
        self.last_focus_time = effective_focus_time

        if not allow:
            self.get_logger().info(f"Ignored Gemini transcript from {self.current_speaker}: {transcript} ({reason})")
            return

        if self.conversation_paused:
            if control_action in ("continue", "repeat") or reengagement:
                self._paused_transcript_pending = transcript
                self._paused_transcript_at = time.monotonic()
                self._apply_pause_state(False, publish=True)
            return

        self.focused_speaker, self.last_focus_time = advance_attention_focus(
            current_speaker=self.current_speaker,
            focused_speaker=self.focused_speaker,
            last_focus_time=self.last_focus_time,
            allow=True,
            recognized_speaker=focus_candidate,
        )

        if self._pending_resume_text:
            self._resume_requested = self._is_resume_request(transcript)
            if self._resume_requested:
                self.get_logger().info("Resume request detected")

        active_language = self.language_tracker.observe(
            transcript,
            preferred_language=str(self.person_context.get("preferred_language", "")),
        )
        self._assistant_name_question_active = self._is_assistant_name_question(normalized)
        self._send_setup()

        # Goodbye detection: let Gemini say farewell naturally, then close the session.
        if self._detect_goodbye(normalized) and not self._goodbye_pending:
            self._goodbye_pending = True
            self.get_logger().info(f"Goodbye detected in transcript: '{transcript}' — Gemini will close session after reply")
            # Gemini will respond naturally; we close the session after its turn completes.

        out = Transcription()
        out.text = transcript
        out.language = active_language
        out.confidence = 1.0
        self.transcription_pub.publish(out)

        self._user_speaking = False

    def _detect_goodbye(self, normalized_text: str) -> bool:
        """Return True if the normalized transcript is a goodbye phrase."""
        GOODBYE_KEYWORDS = (
            'bye bye', 'la revedere', 'goodbye', 'see you later', 'see you',
            'ne vedem', 'pa pa', 'bye', 'pa',
        )
        GOODBYE_CONTEXT_WORDS = {
            'ok', 'okay', 'robot', 'for', 'now', 'thanks', 'thank', 'you',
            'please', 'well', 'then', 'so', 'alright', 'all', 'right', 'bye',
            'goodbye', 'bine', 'pa', 'robotule', 'multumesc', 'merci', 'te',
            'rog', 'gata', 'acum', 'deocamdata',
        }
        import re
        def _phrase_pattern(phrase: str) -> str:
            tokens = [re.escape(t) for t in phrase.split() if t]
            return r'\b' + r'\s+'.join(tokens) + r'\b'

        for clause in re.split(r'[.!?]+', normalized_text or ''):
            clause = clause.strip()
            if not clause:
                continue
            for keyword in GOODBYE_KEYWORDS:
                m = re.search(_phrase_pattern(keyword), clause)
                if not m:
                    continue
                remainder = ' '.join((clause[:m.start()] + ' ' + clause[m.end():]).split())
                if not remainder or all(t in GOODBYE_CONTEXT_WORDS for t in remainder.split()):
                    return True
        return False

    # ─── Session Setup ───────────────────────────────────────────────────────

    def _handle_remember_person(self, args: dict) -> str:
        """Forward a Gemini-detected self-introduction to the enrollment path.

        The LLM decides *that* a self-introduction happened (any language /
        phrasing); person_memory_store_node still owns the audio buffer, the
        Unknown-speaker gate, and the actual voiceprint enrollment.
        """
        name = str((args or {}).get("name", "") or "").strip()
        language = str((args or {}).get("language", "") or "").strip()
        if not name:
            return "no name provided"
        payload = {"preferred_name": name, "preferred_language": language}
        msg = String()
        msg.data = json.dumps(payload, separators=(",", ":"))
        self.introduced_name_pub.publish(msg)
        self.get_logger().info(f"remember_person -> enrollment request for '{name}'")
        return "acknowledged"

    def _send_setup(self):
        if not self._connected.is_set() or self.current_backend != "gemini_live":
            return
        if self._setup_sent:
            return  # Gemini Live API expects 'setup' only once per session

        instructions = self._build_instructions()

        tools: list = []
        if self.google_search_enabled:
            tools.append({"googleSearch": {}})

        # Register remember_person tool if enabled
        if self.name_capture_tool_enabled:
            tools.append({
                "functionDeclarations": [{
                    "name": "remember_person",
                    "description": (
                        "Call this when the CURRENT speaker states, spells, or "
                        "asks to be called by their OWN name, in any language or "
                        "phrasing (e.g. 'my name is Mario', 'call me Mario', "
                        "'sunt Mario', 'eu sunt Mario'). Do NOT call it for other "
                        "people's names or when merely mentioning a name."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "the speaker's own name",
                            },
                            "language": {
                                "type": "string",
                                "description": "language code if evident, e.g. en or ro",
                            },
                        },
                        "required": ["name"],
                    },
                }]
            })

        # Register get_speaker_info tool
        tools.append({
            'functionDeclarations': [
                {
                    'name': 'get_speaker_info',
                    'description': (
                        'Retrieve the current speaker\'s identity context, '
                        'including their preferred spoken name, preferred language, '
                        'and any known background facts.'
                    ),
                    'parameters': {
                        'type': 'OBJECT',
                        'properties': {}
                    }
                }
            ]
        })

        # Register set_conversation_pause tool (robust pause/resume by intent)
        tools.append({
            'functionDeclarations': [
                {
                    'name': 'set_conversation_pause',
                    'description': (
                        'Pause or resume the conversation. Call with paused=true '
                        'when the user asks you to wait, hold on, give them a '
                        'moment, or pause (they may talk to other people '
                        'meanwhile and you must stay silent). Call with '
                        'paused=false only when the same user says they are '
                        'back, ready, or to continue.'
                    ),
                    'parameters': {
                        'type': 'OBJECT',
                        'properties': {
                            'paused': {
                                'type': 'BOOLEAN',
                                'description': 'true to pause, false to resume',
                            }
                        },
                        'required': ['paused'],
                    }
                }
            ]
        })

        setup_payload: dict = {
            "model": f"models/{self.model}",
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": self.voice}
                    }
                },
            },
            "systemInstruction": {
                "parts": [{"text": instructions}]
            },
            "realtimeInputConfig": {
                "automaticActivityDetection": {
                    "disabled": False,
                    "prefixPaddingMs": self.vad_prefix_padding_ms,
                    "silenceDurationMs": self.vad_silence_duration_ms,
                }
            },
            "inputAudioTranscription": {},
        }
        if tools:
            setup_payload["tools"] = tools

        setup_msg = {"setup": setup_payload}

        self._send_raw(setup_msg)
        self._setup_sent = True
        self._setup_speaker = self.current_speaker
        self._setup_preferred_name = self._voice_correlated_preferred_name()

    # ─── Instructions ────────────────────────────────────────────────────────

    def _build_instructions(self) -> str:
        extras = []
        assistant_name_question = self._assistant_name_question_active
        extras.append(
            'Your own assistant name is Robot. '
            'If the user asks your name, answer "Robot". '
            'Do not use any speaker preferred name as your own identity.'
        )
        extras.append(
            'You have a get_speaker_info tool that returns the CURRENT user\'s '
            'preferred_name, preferred_language and known facts. The speaker can '
            'change mid-conversation, so call get_speaker_info whenever you need '
            'to personalize — before greeting, when asked "do you remember me / '
            'my name", or when the speaker may have changed — and address the '
            'user by the preferred_name it returns. Never invent a name.'
        )
        extras.append(
            'You have a set_conversation_pause tool. When the user asks you to '
            'wait, hold on, give them a moment, or pause — even briefly, and '
            'even if the words are unclear — first verbally confirm very politely '
            'and naturally that you are waiting (e.g. say "Sure, I\'ll hold on" or '
            '"No problem, take your time" in English, or "Sigur, te aștept" or '
            '"Nicio problemă, ia-ți timp" in Romanian, depending on the user\'s language), '
            'and then immediately call set_conversation_pause with paused=true. '
            'Once you do, stay silent. When the same user says they are back, '
            'ready, or to continue, call set_conversation_pause with '
            'paused=false and resume.'
        )


        if assistant_name_question:
            extras.append('The user is asking your name right now. Answer clearly with "My name is Robot."')
            extras.append("For this turn, ignore user profile names when composing the answer.")
        if self.current_speaker != "Unknown":
            extras.append(f"Current internal speaker label: {self.current_speaker}. This is a technical identifier, not a spoken name.")
        preferred_name = self._voice_correlated_preferred_name()
        if preferred_name and not assistant_name_question:
            extras.append(f"Preferred spoken name for the current speaker: {preferred_name}. Never call the user by internal labels.")
            extras.append("If the user asks whether you remember their name, answer directly with the preferred spoken name.")
        preferred_language = self.person_context.get("preferred_language", "")
        if preferred_language:
            extras.append(f"Preferred language for this speaker: {preferred_language}.")
        conversation_language = self.language_tracker.current_language
        if conversation_language == "ro":
            extras.append("Current conversation language is Romanian. Keep speaking Romanian unless the user clearly asks to switch.")
        elif conversation_language == "en":
            extras.append("Current conversation language is English. Keep speaking English unless the user clearly asks to switch.")
        facts = self.person_context.get("facts", []) or []
        if facts:
            extras.append("Known personal facts: " + "; ".join(str(f) for f in facts[:8]) + ".")
        if self.waiting_for_robot_confirmation:
            extras.append("A risky robot command is awaiting confirmation. Do not speak. Let the user answer yes/no.")
        if self.conversation_paused:
            extras.append("The user told you to wait. Stay silent until the conversation resumes.")
        if self._last_accepted_user_transcript_norm and (time.monotonic() - self._last_accepted_user_transcript_at) < 5.0:
            extras.append(f"The user just said: '{self._last_accepted_user_transcript_norm}'. Respond to this now.")
        if self._pending_resume_text:
            extras.append(
                "There is an interrupted assistant reply pending. "
                "If the user asks to continue/resume/reia raspunsul/continua, continue from where it was cut."
            )
            if self._pending_resume_remaining:
                extras.append(f'Approximate remaining part of the interrupted reply: "{self._pending_resume_remaining}"')
            else:
                extras.append(f'Interrupted reply to continue: "{self._pending_resume_text}"')
        return " ".join([self.base_instructions, *extras]).strip()

    # ─── Response scheduling ──────────────────────────────────────────────────

    def _schedule_response_create(self, item_id: str, *, reason: str) -> bool:
        return self._schedule_response_create_with_delay(item_id, reason=reason, delay_ms=self.response_create_delay_ms)

    def _schedule_response_create_with_delay(self, item_id: str, *, reason: str, delay_ms: int) -> bool:
        allowed, block_reason = can_request_realtime_response(
            user_speaking=self._user_speaking,
            conversation_paused=self.conversation_paused,
            waiting_for_robot_confirmation=self.waiting_for_robot_confirmation,
            response_create_pending=self._response_create_pending,
            response_active=self._response_active,
            item_id=item_id,
            last_response_request_item_id=self._last_response_request_item_id,
        )
        if not allowed:
            if block_reason in ("user_speaking", "response_pending", "response_active"):
                self._remember_deferred_response(item_id, reason)
            return False
        self._clear_deferred_response()
        self._cancel_pending_response_create()
        delay_ms = max(0, delay_ms)
        if delay_ms <= 0:
            return self._request_response_create(item_id, reason=reason)
        self._pending_response_item_id = item_id
        self._pending_response_reason = reason
        delay_s = delay_ms / 1000.0
        self._pending_response_timer = self.create_timer(delay_s, self._fire_pending_response_create)
        return True

    def _fire_pending_response_create(self):
        item_id = self._pending_response_item_id
        reason = self._pending_response_reason or "delayed_response"
        self._cancel_pending_response_create()
        self._request_response_create(item_id, reason=reason)

    def _request_response_create(self, item_id: str, *, reason: str) -> bool:
        self._cancel_pending_response_create()
        allowed, block_reason = can_request_realtime_response(
            user_speaking=self._user_speaking,
            conversation_paused=self.conversation_paused,
            waiting_for_robot_confirmation=self.waiting_for_robot_confirmation,
            response_create_pending=self._response_create_pending,
            response_active=self._response_active,
            item_id=item_id,
            last_response_request_item_id=self._last_response_request_item_id,
        )
        if not allowed:
            if block_reason in ("user_speaking", "response_pending", "response_active"):
                self._remember_deferred_response(item_id, reason)
            return False
        self._clear_deferred_response()
        # Gemini responds automatically after user turn; no explicit response.create needed.
        # We just update state to allow tracking.
        self._mark_response_create_pending()
        self._last_response_request_item_id = item_id
        self.get_logger().debug(f"Gemini response requested ({reason})")
        return True

    def _cancel_pending_response_create(self):
        timer = self._pending_response_timer
        self._pending_response_timer = None
        self._pending_response_item_id = ""
        self._pending_response_reason = ""
        if timer is None:
            return
        try:
            timer.cancel()
        except Exception:
            pass

    def _schedule_deferred_response_after_speech_stop(self) -> bool:
        if not self._deferred_response_item_id and not self._deferred_response_reason:
            return False
        delay_ms = continued_turn_response_delay_ms(self.response_create_delay_ms, self.continued_turn_response_delay_ms)
        return self._schedule_response_create_with_delay(
            self._deferred_response_item_id,
            reason=self._deferred_response_reason or "continued_turn_after_resume",
            delay_ms=delay_ms,
        )

    def _schedule_deferred_response_after_response_done(self) -> bool:
        if not self._deferred_response_item_id and not self._deferred_response_reason:
            return False
        return self._schedule_response_create_with_delay(
            self._deferred_response_item_id,
            reason=self._deferred_response_reason or "deferred_after_response_done",
            delay_ms=self.response_create_delay_ms,
        )

    # ─── Utilities ───────────────────────────────────────────────────────────

    def _request_reconnect(self, reason: str, immediate_user_turn: bool = False):
        """
        Close the WebSocket to force reconnection with a fresh setup message.

        Gemini Live API accepts 'setup' only once per session, so the only way
        to update system instructions mid-conversation is to reconnect.
        A cooldown prevents rapid reconnections when multiple context updates
        arrive in quick succession (e.g. speaker + language in the same second).
        """
        if self.current_backend != "gemini_live":
            return
        if not self._connected.is_set():
            return  # Already disconnected; reconnect loop will handle it
            
        # Defer reconnect if the turn is not completely idle, unless immediate_user_turn is True and robot is not speaking
        is_busy = self._response_active or self._response_create_pending or self._user_speaking
        if self.robot_speaking or (is_busy and not immediate_user_turn):
            self._pending_reconnect_reason = reason
            self.get_logger().info(f"Gemini Live: deferring reconnect ({reason}) until turn completes")
            return
            
        now = time.monotonic()
        if (now - self._last_reconnect_time) < self._reconnect_cooldown_s:
            self.get_logger().debug(
                f"Gemini reconnect skipped (cooldown active): {reason}"
            )
            return
        self._last_reconnect_time = now
        self.get_logger().info(f"Gemini Live: reconnecting to refresh context ({reason})")
        self._setup_sent = False
        self._intentional_reconnect = True  # Signal that this close is intentional
        try:
            if self._ws_app is not None:
                self._ws_app.close()
        except Exception:
            pass

    def _send_raw(self, payload: dict) -> bool:
        if not self._connected.is_set() or self._ws_app is None:
            return False
        try:
            with self._send_lock:
                self._ws_app.send(json.dumps(payload, separators=(",", ":")))
            return True
        except Exception as exc:
            self.get_logger().error(f"Failed to send Gemini event: {exc}")
            self._connected.clear()
            return False

    @staticmethod
    def _resample_pcm16(audio: np.ndarray, original_rate: int, target_rate: int) -> np.ndarray:
        if original_rate == target_rate or audio.size == 0:
            return audio.astype(np.int16, copy=False)
        duration = audio.size / float(original_rate)
        target_samples = max(1, round(duration * target_rate))
        source_positions = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
        target_positions = np.linspace(0.0, 1.0, num=target_samples, endpoint=False)
        resampled = np.interp(target_positions, source_positions, audio.astype(np.float32))
        return np.clip(np.round(resampled), -32768, 32767).astype(np.int16)

    def _replay_accumulated_audio(self):
        if not self._current_user_audio:
            return
        self.get_logger().info(f"Replaying {len(self._current_user_audio)} samples of accumulated user audio to new session")
        pcm = np.array(self._current_user_audio, dtype=np.int16)
        pcm_api = self._resample_pcm16(pcm, self._current_input_sample_rate, self.api_sample_rate)
        
        chunk_size = 4800
        with self._send_lock:
            for i in range(0, pcm_api.size, chunk_size):
                chunk = pcm_api[i:i+chunk_size]
                encoded = base64.b64encode(chunk.tobytes()).decode("ascii")
                payload = {
                    "realtimeInput": {
                        "mediaChunks": [{"mimeType": f"audio/pcm;rate={self.api_sample_rate}", "data": encoded}]
                    }
                }
                if self._connected.is_set() and self._ws_app is not None:
                    try:
                        self._ws_app.send(json.dumps(payload, separators=(",", ":")))
                    except Exception as exc:
                        self.get_logger().error(f"Failed to send replayed chunk: {exc}")
                        self._connected.clear()
                        break

    def _should_filter_playback_input(self, now_ms: int) -> bool:
        if not self.playback_input_filter_enabled:
            return False
        active = self.robot_speaking
        if not active and self._last_robot_speaking_end_ms > 0:
            active = (now_ms - self._last_robot_speaking_end_ms) <= self.playback_input_filter_post_playback_ms
        if active:
            self._playback_guard_was_active = True
        return active

    def _remember_input_audio(self, samples):
        if not samples:
            return
        max_samples = max(1, int(self._current_input_sample_rate * max(0, self.utterance_capture_prefix_ms) / 1000.0))
        self._recent_input_audio.extend(samples)
        if len(self._recent_input_audio) > max_samples:
            self._recent_input_audio = self._recent_input_audio[-max_samples:]

    def _start_user_audio_capture(self):
        self._capture_user_audio = True
        self._current_user_audio = list(self._recent_input_audio)

    def _publish_captured_user_audio_segment(self):
        if not self._capture_user_audio:
            return
        self._capture_user_audio = False
        min_samples = max(1, int(self._current_input_sample_rate * max(0, self.utterance_capture_min_ms) / 1000.0))
        if len(self._current_user_audio) < min_samples:
            self._current_user_audio = []
            return
        out = Audio()
        out.sample_rate = self._current_input_sample_rate
        out.channels = self._current_input_channels
        out.data = list(self._current_user_audio)
        self.user_audio_segment_pub.publish(out)
        self._current_user_audio = []

    def _publish_assistant_final(self, item_id: str, response_id: str):
        if not item_id or item_id in self._published_final_items:
            return
        text = self._assistant_text.get(item_id, "").strip()
        if not text:
            return
        out = Transcription()
        out.text = text
        out.language = ""
        out.confidence = 1.0
        self.response_pub.publish(out)
        final_chunk = TextChunk()
        final_chunk.text = ""
        final_chunk.language = ""
        final_chunk.is_final = True
        final_chunk.session_id = response_id or item_id
        self.stream_pub.publish(final_chunk)
        self._published_final_items.add(item_id)

    def _remember_assistant_output(self, text: str):
        normalized = self._normalize_text(text)
        if not normalized:
            return
        now = time.monotonic()
        self._recent_assistant_outputs.append((normalized, now))
        retention_window_s = max(self.assistant_echo_window_s * 2.0, 6.0)
        cutoff = now - retention_window_s
        self._recent_assistant_outputs = [
            (t, ts) for t, ts in self._recent_assistant_outputs if ts >= cutoff
        ][-12:]

    def _is_recent_assistant_echo(self, text: str) -> bool:
        if self.assistant_echo_window_s <= 0.0:
            return False
        now = time.monotonic()
        playback_window_active = self.robot_speaking or (
            self._last_assistant_audio_at > 0.0
            and (now - self._last_assistant_audio_at) <= self.assistant_echo_window_s
        )
        if not playback_window_active:
            return False
        cutoff = now - self.assistant_echo_window_s
        self._recent_assistant_outputs = [(t, ts) for t, ts in self._recent_assistant_outputs if ts >= cutoff]
        for assistant_text, _ in reversed(self._recent_assistant_outputs):
            if is_probable_assistant_echo(text, assistant_text,
                threshold=self.assistant_echo_similarity_threshold,
                min_length=self.assistant_echo_min_length):
                return True
        return False

    def _cancel_and_clear(self):
        self._cancel_pending_response_create()
        self._assistant_text.clear()
        self._published_final_items.clear()
        self._clear_playback_progress()
        self._clear_pending_resume()
        self._last_response_request_item_id = ""
        self._capture_user_audio = False
        self._current_user_audio = []
        self._recent_input_audio = []
        self._current_user_transcript = ""
        self._user_speaking = False
        self._playback_guard_was_active = False
        self._playback_frames_blocked = 0
        self._last_robot_speaking_end_ms = 0
        self._last_assistant_audio_at = 0.0
        self._recent_assistant_outputs = []
        self.playback_input_filter.reset()
        self._clear_deferred_response()
        self._mark_response_inactive()
        self.focused_doa_angle = -1
        self.doa_history = []

    def _mark_response_active(self, response_id: str = ""):
        self._response_create_pending = False
        self._response_active = True

    def _mark_response_inactive(self, response_id: str = ""):
        self._response_create_pending = False
        self._response_active = False
        pass

    def _mark_response_create_pending(self):
        self._response_create_pending = True

    def _clear_playback_progress(self):
        self._last_playback_progress = {"stream_id": "", "item_id": "", "played_ms": 0, "stopped": False}
        self._last_truncate_signature = ("", -1)

    def _publish_status(self, status: str):
        msg = String()
        msg.data = status
        self.status_pub.publish(msg)

    def _consume_focus_candidate(self) -> str:
        now = time.monotonic()
        candidate = self.pending_focus_speaker
        candidate_at = self.pending_focus_at
        self.pending_focus_speaker = "Unknown"
        self.pending_focus_at = 0.0
        if candidate == "Unknown":
            return "Unknown"
        if (now - candidate_at) > self.focus_recognition_window_s:
            return "Unknown"
        return candidate

    def _voice_correlated_preferred_name(self) -> str:
        if self.current_speaker == "Unknown":
            return ""
        context_speaker = str(self.person_context.get("speaker", "Unknown") or "Unknown")
        if context_speaker == self.current_speaker:
            name = str(self.person_context.get("preferred_name", "") or "").strip()
            if name:
                return name
        # Fallback to cache if context_speaker doesn't match current_speaker
        cached_context = self._speaker_context_cache.get(self.current_speaker)
        if cached_context:
            return str(cached_context.get("preferred_name", "") or "").strip()
        return ""

    def _ignored_transcript_reason(self, text: str) -> str:
        reason = ignored_short_transcript_reason(
            text,
            waiting_for_robot_confirmation=self.waiting_for_robot_confirmation,
            last_accepted_transcript_norm=self._last_accepted_user_transcript_norm,
            last_accepted_transcript_at=self._last_accepted_user_transcript_at,
            dedupe_window_s=self.short_transcript_dedupe_window_s,
        )
        if reason:
            return reason
        if self.assistant_echo_filter_enabled and self._is_recent_assistant_echo(text):
            return "assistant_echo"
        return ""

    @staticmethod
    def _normalize_text(text: str) -> str:
        return normalize_realtime_text(text)

    def _remember_deferred_response(self, item_id: str, reason: str):
        self._deferred_response_item_id = item_id
        self._deferred_response_reason = reason

    def _clear_deferred_response(self):
        self._deferred_response_item_id = ""
        self._deferred_response_reason = ""

    def _clear_pending_resume(self):
        self._pending_resume_text = ""
        self._pending_resume_remaining = ""
        self._pending_resume_played_ms = 0
        self._resume_requested = False

    def _is_resume_request(self, text: str) -> bool:
        return is_resume_request(text)

    @staticmethod
    def _is_assistant_name_question(normalized_text: str) -> bool:
        text = (normalized_text or "").strip()
        if not text:
            return False
        patterns = (
            "what is your name", "what s your name", "who are you",
            "numele tau", "cum te cheama", "cum te numesti", "care e numele tau",
        )
        return any(p in text for p in patterns)

    @staticmethod
    def _get_circular_average(angles: list) -> int:
        if not angles:
            return -1
        x_sum = sum(math.cos(math.radians(a)) for a in angles)
        y_sum = sum(math.sin(math.radians(a)) for a in angles)
        avg_rad = math.atan2(y_sum, x_sum)
        avg_deg = math.degrees(avg_rad)
        return int(round(avg_deg)) % 360

    @staticmethod
    def _angular_distance(a: int, b: int) -> int:
        diff = (a - b + 180) % 360 - 180
        return abs(diff)

    def _precache_fillers(self):
        import wave
        import os
        import numpy as np

        ro_files = ['charon_hmm_ro.wav', 'charon_pai_ro.wav', 'charon_aaa_ro.wav', 'charon_sa_vedem_ro.wav']
        en_files = ['charon_hmm_en.wav', 'charon_well_en.wav', 'charon_let_see_en.wav', 'charon_uhm_en.wav']

        for lang, files in [('ro', ro_files), ('en', en_files)]:
            for fname in files:
                fpath = os.path.join(self.fillers_dir, fname)
                if not os.path.exists(fpath):
                    self.get_logger().warn(f"Filler file not found: {fpath}")
                    continue
                try:
                    with wave.open(fpath, 'rb') as w:
                        rate = w.getframerate()
                        channels = w.getnchannels()
                        n_frames = w.getnframes()
                        data = w.readframes(n_frames)
                        pcm = np.frombuffer(data, dtype=np.int16)

                        self.fillers_cache[lang].append({
                            'name': fname,
                            'pcm': pcm,
                            'rate': rate,
                            'channels': channels
                        })
                    self.get_logger().info(f"Loaded filler: {fname} ({rate}Hz, {channels}ch, {len(pcm)} samples)")
                except Exception as e:
                    self.get_logger().error(f"Error loading filler {fname}: {e}")

    def vad_callback(self, msg: Bool):
        if not self.session_active or self.current_backend != "gemini_live":
            return
        if not self.enable_local_fillers:
            return

        # Trigger on VAD transition to False (user stopped speaking)
        if not msg.data:
            # Check conditions for playing filler:
            # 1. Robot is not currently speaking or about to speak
            # 2. We haven't started playing the response yet
            # 3. User actually spoke (to filter out noise spikes, we require at least 15 frames)
            if (not self.robot_speaking and 
                    not self._current_turn_audio_started and 
                    self._user_audio_frames_sent >= 15):
                
                import random
                lang = self.language_tracker.current_language or 'ro'
                if lang not in self.fillers_cache or not self.fillers_cache[lang]:
                    lang = 'ro'
                    
                cache_list = self.fillers_cache.get(lang, [])
                if cache_list:
                    if random.random() < self.filler_chance:
                        filler = random.choice(cache_list)
                        try:
                            # Scale volume
                            pcm = (filler['pcm'].astype(np.float32) * self.filler_volume).astype(np.int16)
                            
                            out = Audio()
                            out.sample_rate = filler['rate']
                            out.channels = filler['channels']
                            out.data = pcm.tolist()
                            # Use turn_id + "_filler" to force audio_playback_node to interrupt it
                            # if the real response arrives immediately.
                            out.stream_id = self._active_turn_id + "_filler"
                            out.item_id = "local_filler"
                            self.audio_pub.publish(out)
                            self.get_logger().info(f"🎙️ Playing local filler: {filler['name']} ({lang.upper()})")
                        except Exception as e:
                            self.get_logger().error(f"Error playing local filler: {e}")


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = GeminiLiveNode()
        rclpy.spin(node)
    except RuntimeError as exc:
        print(f"Failed to start Gemini Live node: {exc}")
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()
