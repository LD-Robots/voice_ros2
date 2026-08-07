#!/usr/bin/env python3
"""
OpenAI Realtime Node.

Streams microphone audio to OpenAI Realtime over WebSocket and publishes
assistant audio back into the existing ROS2 audio playback pipeline.
"""
import base64
import json
import os
import random
import threading
import time
import wave
from collections import defaultdict
from pathlib import Path

import numpy as np
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio, TextChunk, Transcription, RobotCommand, WakeWord
from conversational_client.conversation_utils import (
    advance_attention_focus,
    can_accept_control_action,
    can_accept_reengagement,
    decide_attention,
    detect_control_action,
    has_direct_robot_address,
    infer_addressing_intent,
    is_reengagement_phrase,
)
from std_msgs.msg import Bool, Int32, String
from .language_utils import ConversationLanguageTracker
from .openai_web_search import (
    DEFAULT_WEB_SEARCH_CONTEXT_SIZE,
    DEFAULT_WEB_SEARCH_MAX_OUTPUT_TOKENS,
    DEFAULT_WEB_SEARCH_MODEL,
    DEFAULT_WEB_SEARCH_SOURCES_LIMIT,
    DEFAULT_WEB_SEARCH_TIMEOUT_S,
    WAIT_FOR_USER_FUNCTION_NAME,
    WEB_SEARCH_FUNCTION_NAME,
    build_realtime_web_search_tool,
    build_realtime_wait_for_user_tool,
    build_web_search_tool_output,
    call_openai_web_search,
)
from .prompt_config import load_prompt_defaults
from .realtime_audio_filter import PlaybackInputFilter, PlaybackInputFilterConfig
from .realtime_text_utils import (
    StickySpeakerTracker,
    ignored_short_transcript_reason,
    is_probable_assistant_echo,
    is_resume_request,
    normalize_realtime_text,
    should_preserve_paused_transcript,
)
from .realtime_tools import (
    GET_SPEAKER_INFO_FUNCTION_NAME,
    REMEMBER_PERSON_FUNCTION_NAME,
    REPORT_EMOTION_FUNCTION_NAME,
    SET_PAUSE_FUNCTION_NAME,
    build_get_speaker_info_tool,
    build_remember_person_tool,
    build_report_emotion_tool,
    build_set_conversation_pause_tool,
    normalize_emotion,
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
    print("⚠️ websocket-client not installed. Run: pip install websocket-client")

try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False


# Response reasons that are NOT a reply to a user turn. Server VAD never
# creates these, so they stay allowed even when the server owns turn-taking
# (local_response_gating=False).
SERVER_MODE_ALLOWED_REASONS = ('tool_output_ready', 'resume_from_pause', 'wake_word_greeting')


def _find_workspace_root() -> Path | None:
    """Resolve the workspace root portably (no hardcoded directory name)."""
    try:
        from conversational_client.workspace_paths import find_workspace_root
    except ImportError:
        pass
    else:
        return find_workspace_root()

    markers = (
        Path('conversational_client') / 'package.xml',
        Path('conversational_server') / 'package.xml',
    )
    for base in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in [base] + list(base.parents):
            if all((parent / marker).is_file() for marker in markers):
                return parent
    return None

class StatefulResampler:
    """Resampler that maintains phase state between chunks to avoid pitch/speed drift."""
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

class OpenAIRealtimeNode(Node):
    def __init__(self):
        super().__init__('openai_realtime_node')

        if DOTENV_AVAILABLE:
            workspace_root = _find_workspace_root()
            env_path = workspace_root / '.env' if workspace_root else None
            if env_path and env_path.exists():
                load_dotenv(dotenv_path=env_path)

        self.declare_parameter('model', 'gpt-realtime-2')
        self.declare_parameter('voice', 'cedar')
        self.declare_parameter('api_base_url', 'wss://api.openai.com/v1/realtime')
        self.declare_parameter('input_sample_rate', 16000)
        self.declare_parameter('api_sample_rate', 24000)
        self.declare_parameter('capture_during_playback', True)
        self.declare_parameter('input_transcription_enabled', True)
        self.declare_parameter('input_transcription_model', 'gpt-4o-transcribe')
        # Empty = auto-detect. Pin only if the deployment is single-language;
        # this stack is bilingual RO/EN, so bias with the prompt instead.
        self.declare_parameter('input_transcription_language', '')
        self.declare_parameter(
            'input_transcription_prompt',
            'Bilingual Romanian and English conversation with a humanoid robot. '
            'Spoken robot commands include: robot stop, robot move forward, '
            'robot move backward, robot turn around, robot turn left, robot turn right, '
            'robot raise hands, robot lower hands, robot wave, robot dance, '
            'robot sit down, robot stand up.',
        )
        self.declare_parameter('vad_threshold', 0.90)
        self.declare_parameter('vad_prefix_padding_ms', 400)
        self.declare_parameter('vad_silence_duration_ms', 550)
        self.declare_parameter('response_create_delay_ms', 100)
        self.declare_parameter('continued_turn_response_delay_ms', 450)
        self.declare_parameter('local_response_gating', True)
        self.declare_parameter('short_transcript_dedupe_window_s', 4.0)
        self.declare_parameter('playback_input_filter_enabled', True)
        self.declare_parameter('playback_input_filter_post_playback_ms', 900)
        self.declare_parameter('playback_input_filter_min_rms_dbfs', -24.0)
        self.declare_parameter('playback_input_filter_highpass_hz', 300.0)
        self.declare_parameter('playback_input_filter_zcr_min', 0.05)
        self.declare_parameter('playback_input_filter_zcr_max', 0.35)
        self.declare_parameter('playback_input_filter_leak_margin_db', 6.0)
        self.declare_parameter('playback_input_filter_leak_decay_ms', 1200)
        self.declare_parameter('playback_input_filter_hits_required', 4)
        self.declare_parameter('playback_input_filter_hold_ms', 240)
        self.declare_parameter('assistant_echo_filter_enabled', True)
        self.declare_parameter('assistant_echo_window_s', 8.0)
        self.declare_parameter('assistant_echo_similarity_threshold', 88.0)
        self.declare_parameter('assistant_echo_min_length', 8)
        self.declare_parameter('reconnect_delay_s', 3.0)
        self.declare_parameter('sticky_speaker_timeout_s', 60.0)
        self.declare_parameter('speaker_switch_hits_required', 2)
        self.declare_parameter('language_switch_hits_required', 2)
        self.declare_parameter('focus_timeout_s', 45.0)
        self.declare_parameter('focus_recognition_window_s', 3.0)
        self.declare_parameter('allow_known_speaker_switch_without_address', True)
        self.declare_parameter('semantic_addressing_enabled', True)
        self.declare_parameter('loud_environment_mode', True)
        self.declare_parameter('multi_speaker_window_s', 8.0)
        self.declare_parameter('multi_speaker_switch_threshold', 2)
        self.declare_parameter('indirect_address_score_threshold', 0.62)
        self.declare_parameter('loud_indirect_address_score_threshold', 0.74)
        self.declare_parameter('diarization_candidate_timeout_s', 12.0)
        self.declare_parameter('diarization_candidate_confidence_threshold', 0.55)
        self.declare_parameter('diarization_response_wait_ms', 1200)
        self.declare_parameter('utterance_capture_prefix_ms', 400)
        self.declare_parameter('utterance_capture_min_ms', 800)
        self.declare_parameter('name_context_wait_ms', 950)
        self.declare_parameter('wait_for_user_tool_enabled', True)
        # Robot-context tools (emotion / personalization / name capture / pause).
        self.declare_parameter('emotion_tool_enabled', True)
        self.declare_parameter('speaker_info_tool_enabled', True)
        self.declare_parameter('name_capture_tool_enabled', True)
        self.declare_parameter('pause_tool_enabled', True)
        # Local fillers mask response latency with pre-recorded audio.
        self.declare_parameter('enable_local_fillers', False)
        self.declare_parameter('filler_chance', 0.75)
        self.declare_parameter('filler_volume', 0.80)
        self.declare_parameter('filler_delay_ms', 2000)
        self.declare_parameter('fillers_dir', '')
        # ReSpeaker direction-of-arrival, injected as spatial context.
        self.declare_parameter('doa_enabled', True)
        self.declare_parameter('doa_focus_margin', 120.0)
        self.declare_parameter('reasoning_enabled', True)
        self.declare_parameter('reasoning_effort', 'medium')
        self.declare_parameter('web_search_enabled', True)
        self.declare_parameter('web_search_model', DEFAULT_WEB_SEARCH_MODEL)
        self.declare_parameter('web_search_context_size', DEFAULT_WEB_SEARCH_CONTEXT_SIZE)
        self.declare_parameter('web_search_timeout_s', DEFAULT_WEB_SEARCH_TIMEOUT_S)
        self.declare_parameter(
            'web_search_max_output_tokens',
            DEFAULT_WEB_SEARCH_MAX_OUTPUT_TOKENS,
        )
        self.declare_parameter(
            'web_search_sources_limit',
            DEFAULT_WEB_SEARCH_SOURCES_LIMIT,
        )
        self.declare_parameter(
            'instructions',
            str(load_prompt_defaults().get('realtime_instructions', '')),
        )

        self.model = str(self.get_parameter('model').value)
        self.voice = str(self.get_parameter('voice').value)
        self.api_base_url = str(self.get_parameter('api_base_url').value).rstrip('/')
        self.input_sample_rate = int(self.get_parameter('input_sample_rate').value)
        self.api_sample_rate = int(self.get_parameter('api_sample_rate').value)
        self.capture_during_playback = bool(self.get_parameter('capture_during_playback').value)
        self.input_transcription_enabled = bool(
            self.get_parameter('input_transcription_enabled').value
        )
        self.input_transcription_model = str(
            self.get_parameter('input_transcription_model').value
        )
        self.input_transcription_language = str(
            self.get_parameter('input_transcription_language').value
        ).strip()
        self.input_transcription_prompt = str(
            self.get_parameter('input_transcription_prompt').value
        ).strip()
        self.vad_threshold = float(self.get_parameter('vad_threshold').value)
        self.vad_prefix_padding_ms = int(self.get_parameter('vad_prefix_padding_ms').value)
        self.vad_silence_duration_ms = int(
            self.get_parameter('vad_silence_duration_ms').value
        )
        self.response_create_delay_ms = max(
            0,
            int(self.get_parameter('response_create_delay_ms').value),
        )
        self.continued_turn_response_delay_ms = max(
            0,
            int(self.get_parameter('continued_turn_response_delay_ms').value),
        )
        self.local_response_gating = bool(self.get_parameter('local_response_gating').value)
        self.short_transcript_dedupe_window_s = float(
            self.get_parameter('short_transcript_dedupe_window_s').value
        )
        self.playback_input_filter_enabled = bool(
            self.get_parameter('playback_input_filter_enabled').value
        )
        self.playback_input_filter_post_playback_ms = max(
            0,
            int(self.get_parameter('playback_input_filter_post_playback_ms').value),
        )
        zcr_min = max(
            0.0,
            min(1.0, float(self.get_parameter('playback_input_filter_zcr_min').value)),
        )
        zcr_max = max(
            0.0,
            min(1.0, float(self.get_parameter('playback_input_filter_zcr_max').value)),
        )
        if zcr_min > zcr_max:
            zcr_min, zcr_max = zcr_max, zcr_min
        self.playback_input_filter = PlaybackInputFilter(
            PlaybackInputFilterConfig(
                min_rms_dbfs=float(
                    self.get_parameter('playback_input_filter_min_rms_dbfs').value
                ),
                highpass_hz=float(
                    self.get_parameter('playback_input_filter_highpass_hz').value
                ),
                zcr_min=zcr_min,
                zcr_max=zcr_max,
                leak_margin_db=float(
                    self.get_parameter('playback_input_filter_leak_margin_db').value
                ),
                leak_decay_ms=max(
                    0,
                    int(self.get_parameter('playback_input_filter_leak_decay_ms').value),
                ),
                hits_required=max(
                    1,
                    int(self.get_parameter('playback_input_filter_hits_required').value),
                ),
                hold_ms=max(
                    0,
                    int(self.get_parameter('playback_input_filter_hold_ms').value),
                ),
            )
        )
        self.assistant_echo_filter_enabled = bool(
            self.get_parameter('assistant_echo_filter_enabled').value
        )
        self.assistant_echo_window_s = max(
            0.0,
            float(self.get_parameter('assistant_echo_window_s').value),
        )
        self.assistant_echo_similarity_threshold = float(
            self.get_parameter('assistant_echo_similarity_threshold').value
        )
        self.assistant_echo_min_length = max(
            1,
            int(self.get_parameter('assistant_echo_min_length').value),
        )
        self.reconnect_delay_s = float(self.get_parameter('reconnect_delay_s').value)
        self.utterance_capture_prefix_ms = int(
            self.get_parameter('utterance_capture_prefix_ms').value
        )
        self.utterance_capture_min_ms = int(
            self.get_parameter('utterance_capture_min_ms').value
        )
        self.name_context_wait_ms = max(
            0,
            int(self.get_parameter('name_context_wait_ms').value),
        )
        self.wait_for_user_tool_enabled = bool(
            self.get_parameter('wait_for_user_tool_enabled').value
        )
        self.emotion_tool_enabled = bool(self.get_parameter('emotion_tool_enabled').value)
        self.speaker_info_tool_enabled = bool(
            self.get_parameter('speaker_info_tool_enabled').value
        )
        self.name_capture_tool_enabled = bool(
            self.get_parameter('name_capture_tool_enabled').value
        )
        self.pause_tool_enabled = bool(self.get_parameter('pause_tool_enabled').value)
        self.doa_enabled = bool(self.get_parameter('doa_enabled').value)
        self.doa_focus_margin = float(self.get_parameter('doa_focus_margin').value)
        self.current_doa = None
        self.enable_local_fillers = bool(self.get_parameter('enable_local_fillers').value)
        self.filler_chance = float(self.get_parameter('filler_chance').value)
        self.filler_volume = float(self.get_parameter('filler_volume').value)
        self.filler_delay_ms = max(0, int(self.get_parameter('filler_delay_ms').value))
        self.fillers_dir = str(self.get_parameter('fillers_dir').value).strip()
        if not os.path.isdir(self.fillers_dir):
            workspace_root = _find_workspace_root()
            fallback = (workspace_root / 'voices' / 'fillers') if workspace_root else None
            if fallback is not None and fallback.is_dir():
                self.fillers_dir = str(fallback)
        self.fillers_cache: dict[str, list] = {'ro': [], 'en': []}
        self._filler_timer = None
        if self.enable_local_fillers:
            self._precache_fillers()
        self.reasoning_enabled = bool(self.get_parameter('reasoning_enabled').value)
        self.reasoning_effort = str(self.get_parameter('reasoning_effort').value).strip()
        self.web_search_enabled = bool(self.get_parameter('web_search_enabled').value)
        self.web_search_model = str(self.get_parameter('web_search_model').value)
        self.web_search_context_size = str(
            self.get_parameter('web_search_context_size').value
        )
        self.web_search_timeout_s = float(self.get_parameter('web_search_timeout_s').value)
        self.web_search_max_output_tokens = max(
            32,
            int(self.get_parameter('web_search_max_output_tokens').value),
        )
        self.web_search_sources_limit = max(
            1,
            int(self.get_parameter('web_search_sources_limit').value),
        )
        self.base_instructions = str(self.get_parameter('instructions').value)
        self.speaker_tracker = StickySpeakerTracker(
            float(self.get_parameter('sticky_speaker_timeout_s').value),
            int(self.get_parameter('speaker_switch_hits_required').value),
        )
        self.focus_timeout_s = float(self.get_parameter('focus_timeout_s').value)
        self.focus_recognition_window_s = float(
            self.get_parameter('focus_recognition_window_s').value
        )
        self.allow_known_speaker_switch_without_address = bool(
            self.get_parameter('allow_known_speaker_switch_without_address').value
        )
        self.semantic_addressing_enabled = bool(
            self.get_parameter('semantic_addressing_enabled').value
        )
        self.loud_environment_mode = bool(self.get_parameter('loud_environment_mode').value)
        self.multi_speaker_window_s = float(self.get_parameter('multi_speaker_window_s').value)
        self.multi_speaker_switch_threshold = int(
            self.get_parameter('multi_speaker_switch_threshold').value
        )
        self.indirect_address_score_threshold = float(
            self.get_parameter('indirect_address_score_threshold').value
        )
        self.loud_indirect_address_score_threshold = float(
            self.get_parameter('loud_indirect_address_score_threshold').value
        )
        self.diarization_candidate_timeout_s = max(
            0.0,
            float(self.get_parameter('diarization_candidate_timeout_s').value),
        )
        self.diarization_candidate_confidence_threshold = max(
            0.0,
            min(
                1.0,
                float(self.get_parameter('diarization_candidate_confidence_threshold').value),
            ),
        )
        self.diarization_response_wait_ms = max(
            0,
            int(self.get_parameter('diarization_response_wait_ms').value),
        )
        self.language_tracker = ConversationLanguageTracker(
            int(self.get_parameter('language_switch_hits_required').value)
        )

        self.api_key = os.environ.get('OPENAI_API_KEY')
        if not self.api_key:
            self.get_logger().error('OPENAI_API_KEY environment variable not set!')
            raise RuntimeError('OPENAI_API_KEY not set')
        if not WEBSOCKET_AVAILABLE:
            self.get_logger().error('websocket-client package not installed!')
            raise RuntimeError('websocket-client not available')

        self.session_active = False
        self.conversation_paused = False
        self.robot_speaking = False
        self.waiting_for_robot_confirmation = False
        self.current_speaker = 'Unknown'
        self.last_raw_speaker = 'Unknown'
        self.current_backend = 'openai_realtime'
        self.focused_speaker = 'Unknown'
        self.last_focus_time = 0.0
        self.pending_focus_speaker = 'Unknown'
        self.pending_focus_at = 0.0
        self.recent_speaker_events = []
        self.diarization_candidate = {
            'speaker': 'Unknown',
            'confidence': 0.0,
            'source': '',
            'expires_at': 0.0,
        }
        self.person_context = {
            'speaker': 'Unknown',
            'preferred_name': '',
            'preferred_language': '',
            'facts': [],
        }

        self._ws_app = None
        self._ws_thread = threading.Thread(
            target=self._websocket_loop,
            daemon=True,
            name='openai-realtime-ws',
        )
        self._running = True
        self._connected = threading.Event()
        self._send_lock = threading.Lock()

        self._assistant_text = defaultdict(str)
        self._published_final_items = set()
        self._active_response_id = ''
        self._response_active = False
        self._response_create_pending = False
        self._user_speaking = False
        self._audio_chunks_sent = 0
        self._seen_output_audio_for_response = set()

        # Output Resampler (Convert 24kHz OpenAI -> 16kHz System)
        self.output_resampler = StatefulResampler(24000, 16000)
        self._last_playback_progress = {
            'stream_id': '',
            'item_id': '',
            'played_ms': 0,
            'stopped': False,
        }
        self._last_truncate_signature = ('', -1)
        self._last_truncate_time = 0.0
        self._pending_resume_text = ''
        self._pending_resume_remaining = ''
        self._pending_resume_played_ms = 0
        self._resume_requested = False
        self._last_response_request_item_id = ''
        self._pending_response_timer = None
        self._pending_response_item_id = ''
        self._pending_response_reason = ''
        self._deferred_response_item_id = ''
        self._deferred_response_reason = ''
        self._assistant_name_question_active = False
        self._paused_transcript_pending = ''
        self._paused_transcript_at = 0.0
        self._last_accepted_user_transcript_norm = ''
        self._last_accepted_user_transcript_at = 0.0
        self._last_robot_speaking_end_ms = 0
        self._playback_guard_was_active = False
        self._playback_frames_blocked = 0
        self._last_assistant_audio_at = 0.0
        self._recent_assistant_outputs = []
        self._recent_input_audio = []
        self._current_user_audio = []
        self._capture_user_audio = False
        self._current_input_sample_rate = self.input_sample_rate
        self._current_input_channels = 1
        self._handled_tool_call_ids = set()
        self._tool_call_lock = threading.Lock()
        self._pending_diarization_policy = None
        self._pending_diarization_policy_timer = None
        # Filler state: only mask latency when the user really spoke and the
        # model has not begun answering yet.
        self._current_turn_audio_started = False
        self._user_audio_frames_sent = 0
        self._active_turn_id = ''
        self.last_user_emotion = ''
        
        # State pentru Resampling fara drift (24kHz -> 16kHz)
        self._resample_accumulator = 0.0

        self.audio_pub = self.create_publisher(Audio, 'audio_out', 10)
        self.user_audio_segment_pub = self.create_publisher(Audio, 'realtime_user_audio_segment', 10)
        self.transcription_pub = self.create_publisher(Transcription, 'transcription', 10)
        self.stream_pub = self.create_publisher(TextChunk, 'llm_stream', 10)
        self.response_pub = self.create_publisher(Transcription, 'llm_response', 10)
        self.pause_state_pub = self.create_publisher(Bool, 'conversation_pause', 10)
        self.tts_stop_pub = self.create_publisher(Bool, 'stop_playback', 10)
        self.status_pub = self.create_publisher(String, 'openai_realtime_status', 10)
        self.response_policy_pub = self.create_publisher(String, 'realtime_response_policy', 10)
        # report_user_emotion -> downstream consumers (expression, logging, HRI)
        self.user_emotion_pub = self.create_publisher(String, 'user_emotion', 10)
        # remember_person -> person_memory_store_node owns the actual enrollment
        self.introduced_name_pub = self.create_publisher(String, 'introduced_name', 10)

        self.audio_sub = self.create_subscription(Audio, 'audio_raw', self.audio_callback, 10)
        self.session_sub = self.create_subscription(
            Bool,
            'session_active',
            self.session_callback,
            10,
        )
        self.speaking_sub = self.create_subscription(
            Bool,
            'is_speaking',
            self.speaking_callback,
            10,
        )
        self.stop_sub = self.create_subscription(Bool, 'stop_playback', self.stop_callback, 10)
        self.progress_sub = self.create_subscription(
            String,
            'audio_playback_progress',
            self.playback_progress_callback,
            10,
        )
        self.speaker_sub = self.create_subscription(
            String,
            'speaker_id',
            self.speaker_callback,
            10,
        )
        self.speaker_candidate_sub = self.create_subscription(
            String,
            'speaker_id_candidate',
            self.speaker_candidate_callback,
            10,
        )
        self.robot_command_sub = self.create_subscription(
            RobotCommand,
            'robot_command',
            self.robot_command_callback,
            10,
        )
        self.robot_status_sub = self.create_subscription(
            String,
            'robot_command_status',
            self.robot_status_callback,
            10,
        )
        self.backend_sub = self.create_subscription(
            String,
            'conversation_backend',
            self.backend_callback,
            10,
        )
        self.person_context_sub = self.create_subscription(
            String,
            'person_context',
            self.person_context_callback,
            10,
        )
        self.pause_sub = self.create_subscription(
            Bool,
            'conversation_pause',
            self.pause_callback,
            10,
        )
        self.doa_sub = self.create_subscription(
            Int32,
            'doa_angle',
            self.doa_callback,
            10,
        )
        self.vad_sub = self.create_subscription(
            Bool,
            'voice_activity',
            self.vad_callback,
            10,
        )
        self.wake_word_sub = self.create_subscription(
            WakeWord,
            'wake_word',
            self.wake_word_callback,
            10,
        )

        self._ws_thread.start()
        self._publish_status('connecting')
        self.get_logger().info(
            f'OpenAI Realtime Node started with model={self.model}, voice={self.voice}'
        )

    def destroy_node(self):
        self._running = False
        self._connected.clear()
        self._cancel_pending_response_create()
        self._cancel_pending_diarization_policy()
        self._cancel_filler()
        try:
            if self._ws_app is not None:
                self._ws_app.close()
        except Exception:
            pass
        return super().destroy_node()

    def session_callback(self, msg: Bool):
        self.session_active = bool(msg.data)
        if self.session_active:
            self.get_logger().info('OpenAI conversation session is ACTIVE')
            self._refresh_session()
        else:
            self.get_logger().info('OpenAI conversation session is INACTIVE')
            self.speaker_tracker.reset()
            self.current_speaker = 'Unknown'
            self.last_raw_speaker = 'Unknown'
            self.language_tracker.reset()
            self.focused_speaker = 'Unknown'
            self.last_focus_time = 0.0
            self.pending_focus_speaker = 'Unknown'
            self.pending_focus_at = 0.0
            self.recent_speaker_events.clear()
            self._clear_diarization_candidate()
            self._cancel_pending_diarization_policy()
            self._cancel_and_clear()

    def speaking_callback(self, msg: Bool):
        was_speaking = self.robot_speaking
        self.robot_speaking = bool(msg.data)
        now_ms = int(time.time() * 1000)

        if self.robot_speaking:
            self._last_assistant_audio_at = time.monotonic()
            if not was_speaking and self.playback_input_filter_enabled:
                self.playback_input_filter.reset()
                self._playback_frames_blocked = 0
        elif was_speaking:
            self._last_robot_speaking_end_ms = now_ms

    def pause_callback(self, msg: Bool):
        self._apply_pause_state(bool(msg.data), publish=False)

    def _apply_pause_state(self, paused: bool, *, publish: bool):
        if paused == self.conversation_paused:
            return
        self.conversation_paused = paused
        if paused:
            self.get_logger().info('OpenAI conversation paused: listening without responding')
            self._cancel_pending_response_create()
            self._truncate_current_audio('conversation_paused')
            # Hard-stop whatever is already in the speaker queue: truncating the
            # server-side item does nothing for audio the playback node already has.
            self._mark_response_inactive()
            self.tts_stop_pub.publish(Bool(data=True))
        else:
            self.get_logger().info('OpenAI conversation resumed')
        self._refresh_session()
        if not paused and self._paused_transcript_pending:
            self._schedule_response_create('', reason='resume_from_pause')
            self._paused_transcript_pending = ''
            self._paused_transcript_at = 0.0
        if publish:
            msg = Bool()
            msg.data = paused
            self.pause_state_pub.publish(msg)

    def backend_callback(self, msg: String):
        backend = msg.data.strip() or 'legacy'
        if backend == self.current_backend:
            return
        self.current_backend = backend
        if backend != 'openai_realtime':
            self._cancel_and_clear()
        self._refresh_session()

    def stop_callback(self, msg: Bool):
        if msg.data:
            self._truncate_current_audio('tts_stop')
            self._clear_playback_progress()

    def playback_progress_callback(self, msg: String):
        try:
            payload = json.loads(msg.data)
        except Exception:
            return
        if bool(payload.get('stopped', False)):
            self._clear_playback_progress()
            self._mark_response_inactive()
            return
        self._last_playback_progress = {
            'stream_id': str(payload.get('stream_id', '') or ''),
            'item_id': str(payload.get('item_id', '') or ''),
            'played_ms': int(payload.get('played_ms', 0) or 0),
            'stopped': bool(payload.get('stopped', False)),
        }

    def speaker_callback(self, msg: String):
        raw_speaker = msg.data.strip() or 'Unknown'
        self.last_raw_speaker = raw_speaker
        speaker = self.speaker_tracker.update(raw_speaker)
        if raw_speaker != 'Unknown' and speaker == raw_speaker:
            self.pending_focus_speaker = raw_speaker
            self.pending_focus_at = time.monotonic()
        self._record_speaker_event(speaker)
        if speaker != self.current_speaker:
            self.current_speaker = speaker
            self._refresh_session()

    def speaker_candidate_callback(self, msg: String):
        try:
            payload = json.loads(msg.data)
        except Exception as exc:
            self.get_logger().warn(f'Invalid speaker candidate payload: {exc}')
            return

        speaker = str(payload.get('speaker', 'Unknown') or 'Unknown').strip() or 'Unknown'
        confidence = float(payload.get('confidence', 0.0) or 0.0)
        if speaker == 'Unknown' or confidence < self.diarization_candidate_confidence_threshold:
            return

        now = time.monotonic()
        self.diarization_candidate = {
            'speaker': speaker,
            'confidence': confidence,
            'source': str(payload.get('source', 'speaker_candidate') or 'speaker_candidate'),
            'expires_at': now + self.diarization_candidate_timeout_s,
        }

        previous = self.current_speaker
        self.current_speaker = speaker
        self.last_raw_speaker = speaker
        self.pending_focus_speaker = speaker
        self.pending_focus_at = now
        self.speaker_tracker.current_speaker = speaker
        self.speaker_tracker.last_known_at = now
        self.speaker_tracker.pending_speaker = 'Unknown'
        self.speaker_tracker.pending_hits = 0
        self._record_speaker_event(speaker)

        if previous != speaker:
            self.get_logger().info(
                f'Diarization assist selected speaker={speaker} '
                f'(confidence={confidence:.2f}, previous={previous})'
            )
            self._refresh_session()

        if self._pending_diarization_policy is not None:
            self._fire_pending_diarization_policy()

    def person_context_callback(self, msg: String):
        try:
            payload = json.loads(msg.data)
        except Exception:
            return
        self.person_context = {
            'speaker': str(payload.get('speaker', 'Unknown') or 'Unknown'),
            'preferred_name': str(payload.get('preferred_name', '') or ''),
            'preferred_language': str(payload.get('preferred_language', '') or ''),
            'facts': list(payload.get('facts', []) or []),
        }
        self.language_tracker.seed(self.person_context.get('preferred_language', ''))
        self._refresh_session()

    def doa_callback(self, msg: Int32):
        """Track where the current speaker is, for spatial awareness in the prompt."""
        if not self.doa_enabled:
            return
        try:
            angle = int(msg.data)
        except (TypeError, ValueError):
            return
        if angle < 0:
            return
        previous = self.current_doa
        self.current_doa = angle
        # Only rebuild the session on a meaningful move — a session update per
        # 10 Hz DOA sample would thrash the connection.
        if previous is None or abs(angle - previous) >= self.doa_focus_margin:
            self.get_logger().debug(f'DOA moved: {previous} -> {angle} deg')
            self._refresh_session()

    def wake_word_callback(self, msg: WakeWord):
        """Greet the user when the wake word fires.

        The legacy pipeline plays a cached 'ack' sound here, but tts_node
        suppresses that outside the legacy backend — so without this the robot
        stayed completely silent after "hello robot". Instead of a canned sound,
        inject a system turn and let the model greet in its own voice.
        """
        if self.current_backend != 'openai_realtime':
            return
        if not self._connected.is_set():
            return
        word = (msg.word or '').strip().lower()
        # Only the hello model greets; the stop/goodbye models must not.
        if 'hello' not in word and 'wake' not in word:
            return

        preferred_name = self._voice_correlated_preferred_name()
        who = f"The user '{preferred_name}'" if preferred_name else 'The user'
        greeting = (
            f"[System: {who} just said 'Hello Robot' to get your attention. "
            'Greet them warmly in one short sentence, then wait for their request.]'
        )
        if not self._send_event({
            'type': 'conversation.item.create',
            'item': {
                'type': 'message',
                'role': 'user',
                'content': [{'type': 'input_text', 'text': greeting}],
            },
        }):
            return
        self.get_logger().info(f"Wake word '{word}' -> injecting greeting turn")
        self._request_response_create('', reason='wake_word_greeting')

    def vad_callback(self, msg: Bool):
        """Play a local filler when the user stops speaking, to mask model latency."""
        if not self.enable_local_fillers or msg.data:
            return
        if not self.session_active or self.current_backend != 'openai_realtime':
            return
        if self.conversation_paused or self.waiting_for_robot_confirmation:
            return
        # Only fill a real gap: the user must have actually spoken, and the model
        # must not have started answering already.
        if self.robot_speaking or self._current_turn_audio_started:
            return
        if self._user_audio_frames_sent < 15:
            return
        self._schedule_filler()

    def _schedule_filler(self):
        if self._filler_timer is not None:
            return
        delay_s = self.filler_delay_ms / 1000.0

        def _fire():
            self._filler_timer = None
            # Re-check: the answer may have arrived during the delay, which is
            # exactly the case where a filler would talk over the robot.
            if self.robot_speaking or self._current_turn_audio_started:
                return
            if self.conversation_paused or self.waiting_for_robot_confirmation:
                return
            self._play_filler()

        self._filler_timer = threading.Timer(delay_s, _fire)
        self._filler_timer.daemon = True
        self._filler_timer.start()

    def _cancel_filler(self):
        if self._filler_timer is not None:
            self._filler_timer.cancel()
            self._filler_timer = None

    def _precache_fillers(self):
        """Load the filler WAVs once at startup so playback costs no disk I/O."""
        ro_files = ['hmm_ro.wav', 'pai_ro.wav', 'aaa_ro.wav', 'sa_vedem_ro.wav']
        en_files = ['hmm_en.wav', 'well_en.wav', 'let_see_en.wav', 'uhm_en.wav']

        if not self.fillers_dir or not os.path.isdir(self.fillers_dir):
            self.get_logger().warn(
                f'Local fillers enabled but directory is missing: {self.fillers_dir!r}'
            )
            return

        for lang, files in (('ro', ro_files), ('en', en_files)):
            for fname in files:
                fpath = os.path.join(self.fillers_dir, fname)
                if not os.path.exists(fpath):
                    self.get_logger().warn(f'Filler file not found: {fpath}')
                    continue
                try:
                    with wave.open(fpath, 'rb') as handle:
                        rate = handle.getframerate()
                        channels = handle.getnchannels()
                        pcm = np.frombuffer(
                            handle.readframes(handle.getnframes()),
                            dtype=np.int16,
                        )
                    self.fillers_cache[lang].append({
                        'name': fname,
                        'pcm': pcm,
                        'rate': rate,
                        'channels': channels,
                    })
                    self.get_logger().info(
                        f'Loaded filler: {fname} ({rate}Hz, {channels}ch, {len(pcm)} samples)'
                    )
                except Exception as exc:
                    self.get_logger().error(f'Error loading filler {fname}: {exc}')

    def _play_filler(self):
        lang = self.language_tracker.current_language or 'ro'
        if not self.fillers_cache.get(lang):
            lang = 'ro'
        cache_list = self.fillers_cache.get(lang) or []
        if not cache_list or random.random() >= self.filler_chance:
            return

        filler = random.choice(cache_list)
        try:
            pcm = (filler['pcm'].astype(np.float32) * self.filler_volume).astype(np.int16)
            out = Audio()
            out.sample_rate = filler['rate']
            out.channels = filler['channels']
            out.data = pcm.tolist()
            # A distinct stream_id lets audio_playback_node drop the filler the
            # moment the real response starts streaming.
            out.stream_id = f'{self._active_turn_id}_filler'
            out.item_id = 'local_filler'
            self.audio_pub.publish(out)
            self.get_logger().info(
                f"🎙️ Playing local filler: {filler['name']} ({lang.upper()})"
            )
        except Exception as exc:
            self.get_logger().error(f'Error playing local filler: {exc}')

    def robot_command_callback(self, msg: RobotCommand):
        # Keep robot motion logic local; suppress assistant chatter for commands.
        self._truncate_current_audio('robot_command')

    def robot_status_callback(self, msg: String):
        status = msg.data.strip()
        mute_statuses = {'confirmation_required'}
        resume_statuses = {
            'confirmation_accepted',
            'confirmation_rejected',
            'confirmation_timeout',
            'canceled',
            'completed',
            'executing',
            'stopped',
        }

        if status in mute_statuses and not self.waiting_for_robot_confirmation:
            self.waiting_for_robot_confirmation = True
            self._refresh_session()
        elif status in resume_statuses and self.waiting_for_robot_confirmation:
            self.waiting_for_robot_confirmation = False
            self._refresh_session()

    def audio_callback(self, msg: Audio):
        if not self.session_active or not self._connected.is_set():
            return
        if self.current_backend != 'openai_realtime':
            return
        if not self.capture_during_playback and self.robot_speaking:
            return
        if not msg.data:
            return

        input_sample_rate = int(msg.sample_rate or self.input_sample_rate)
        now_ms = int(time.time() * 1000)
        pcm = np.array(msg.data, dtype=np.int16)
        if pcm.size == 0:
            return

        if self._should_filter_playback_input(now_ms):
            xf = pcm.astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(xf * xf) + 1e-12))
            dbfs = 20.0 * np.log10(rms + 1e-12)
            if getattr(self, '_debug_dbfs_counter', 0) % 5 == 0:  # Printeaza o data la ~300ms
                self.get_logger().info(f'[Ecou Boxe] Nivel microfon = {dbfs:.2f} dbFS')
            self._debug_dbfs_counter = getattr(self, '_debug_dbfs_counter', 0) + 1
            
            if not self.playback_input_filter.should_forward(
                pcm,
                sample_rate=input_sample_rate,
                now_ms=now_ms,
            ):
                self._playback_frames_blocked += 1
                if self._playback_frames_blocked % 40 == 0:
                    self.get_logger().debug(
                        'Blocked microphone frame during playback '
                        f'({self._playback_frames_blocked} blocked frames)'
                    )
                return
        elif self._playback_guard_was_active:
            self.playback_input_filter.reset()
            self._playback_guard_was_active = False
            self._playback_frames_blocked = 0

        self._current_input_sample_rate = input_sample_rate
        self._current_input_channels = int(msg.channels or 1)
        self._remember_input_audio(msg.data)
        if self._capture_user_audio:
            self._current_user_audio.extend(msg.data)

        pcm = self._resample_pcm16(pcm, input_sample_rate, self.api_sample_rate)
        encoded = base64.b64encode(pcm.tobytes()).decode('ascii')
        self._send_event({
            'type': 'input_audio_buffer.append',
            'audio': encoded,
        })
        self._audio_chunks_sent += 1
        self._user_audio_frames_sent += 1
        if self._audio_chunks_sent % 50 == 0:
            self.get_logger().info(
                f'Streaming audio to OpenAI Realtime ({self._audio_chunks_sent} chunks sent)'
            )

    def _websocket_loop(self):
        while self._running:
            url = f'{self.api_base_url}?model={self.model}'
            headers = [
                f'Authorization: Bearer {self.api_key}',
            ]
            self._ws_app = websocket.WebSocketApp(
                url,
                header=headers,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )

            try:
                self._ws_app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                self.get_logger().error(f'OpenAI Realtime WebSocket failed: {exc}')

            self._connected.clear()
            if self._running:
                time.sleep(max(1.0, self.reconnect_delay_s))

    def _on_open(self, ws):
        self.get_logger().info('Connected to OpenAI Realtime API')
        self._connected.set()
        self._publish_status('online')
        self._refresh_session()

    def _on_close(self, ws, status_code, msg):
        self._connected.clear()
        self._publish_status('offline')
        self.get_logger().warn(f'OpenAI Realtime disconnected: code={status_code}, msg={msg}')

    def _on_error(self, ws, error):
        self._publish_status('error')
        self.get_logger().error(f'OpenAI Realtime error: {error}')

    def _on_message(self, ws, message):
        try:
            event = json.loads(message)
        except json.JSONDecodeError:
            self.get_logger().warn('Received non-JSON event from OpenAI Realtime')
            return

        event_type = event.get('type', '')

        if event_type == 'input_audio_buffer.speech_started':
            self.get_logger().info('OpenAI Realtime detected user speech start')
            self._user_speaking = True
            self._cancel_pending_response_create()
            stop_msg = Bool()
            stop_msg.data = True
            self.tts_stop_pub.publish(stop_msg)
            self._truncate_current_audio('speech_started')
            self._start_user_audio_capture()
            return

        if event_type == 'input_audio_buffer.speech_stopped':
            self.get_logger().info('OpenAI Realtime detected user speech stop')
            self._user_speaking = False
            self._publish_captured_user_audio_segment()
            self._schedule_deferred_response_after_speech_stop()
            return

        if event_type == 'response.created':
            response = event.get('response', {}) or {}
            response_id = str(response.get('id', '') or '')
            self._mark_response_active(response_id)
            if response_id:
                self.get_logger().info(f'OpenAI Realtime started response {response_id}')
            return

        if event_type == 'conversation.item.input_audio_transcription.completed':
            item_id = str(event.get('item_id', '') or '')
            transcript = (event.get('transcript') or '').strip()
            if transcript:
                ignore_reason = self._ignored_transcript_reason(transcript)
                if ignore_reason:
                    self.get_logger().info(
                        f'Ignoring short/accidental transcript ({ignore_reason}): {transcript}'
                    )
                    self._delete_conversation_item(item_id, ignore_reason)
                    return
                self.get_logger().info(f'OpenAI transcript: {transcript}')
                self._handle_user_transcript_completed(item_id, transcript)
            return

        if event_type in ('response.audio.delta', 'response.output_audio.delta'):
            self._handle_output_audio_delta(event)
            return

        if event_type in (
            'response.audio_transcript.delta',
            'response.output_audio_transcript.delta',
            'response.text.delta',
        ):
            self._handle_text_delta(event)
            return

        if event_type in (
            'response.audio_transcript.done',
            'response.output_audio_transcript.done',
            'response.text.done',
        ):
            self._handle_text_done(event)
            return

        if event_type in (
            'response.output_item.done',
            'response.function_call_arguments.done',
        ):
            self._handle_function_call_event(event)
            return

        if event_type == 'response.done':
            self._handle_response_done(event)
            return

        if event_type == 'error':
            error = event.get('error', {})
            message = error.get('message') or str(error)
            if self._is_benign_realtime_error(message):
                if 'already has an active response in progress' in message.lower():
                    self._clear_response_create_pending()
                self.get_logger().warn(f'OpenAI Realtime benign API warning: {message}')
            else:
                self._publish_status('error')
                self.get_logger().error(f'OpenAI Realtime API error: {message}')

    def _handle_output_audio_delta(self, event: dict):
        delta = event.get('delta')
        if not delta:
            return

        # Drop the model's speech while paused or while the gate is waiting for a
        # spoken confirmation. A system-prompt "stay silent" is not honoured by a
        # native-audio model, so the audio has to be dropped here. Input
        # transcription deliberately keeps running so the resume phrase (or the
        # yes/no) is still heard.
        if self.conversation_paused or self.waiting_for_robot_confirmation:
            return

        self._mark_response_active(str(event.get('response_id', '') or ''))
        response_id = str(event.get('response_id', '') or '')
        # The real answer is on its way: no filler may follow it.
        self._current_turn_audio_started = True
        self._cancel_filler()
        if response_id and response_id not in self._seen_output_audio_for_response:
            self._seen_output_audio_for_response.add(response_id)
            self.get_logger().info(f'OpenAI Realtime started audio output for response {response_id}')

        try:
            pcm_bytes = base64.b64decode(delta)
        except Exception as exc:
            self.get_logger().error(f'Failed to decode OpenAI audio delta: {exc}')
            return

        pcm = np.frombuffer(pcm_bytes, dtype=np.int16)
        if pcm.size == 0:
            return

        # STATEFUL RESAMPLING: 24kHz -> 16kHz
        pcm_resampled = self.output_resampler.resample(pcm)
        if pcm_resampled.size == 0:
            return

        self._last_assistant_audio_at = time.monotonic()

        out = Audio()
        out.sample_rate = 16000  # Now always 16kHz
        out.channels = 1
        out.data = pcm_resampled.tolist()
        out.stream_id = str(event.get('response_id', '') or '')
        out.item_id = str(event.get('item_id', '') or '')
        self.audio_pub.publish(out)

    def _handle_text_delta(self, event: dict):
        item_id = str(event.get('item_id', '') or '')
        response_id = str(event.get('response_id', '') or '')
        delta = str(event.get('delta', '') or '')
        if not delta:
            return

        self._mark_response_active(response_id)

        self._assistant_text[item_id] += delta

        chunk = TextChunk()
        chunk.text = delta
        chunk.language = ''
        chunk.is_final = False
        chunk.session_id = response_id or item_id
        self.stream_pub.publish(chunk)

    def _handle_text_done(self, event: dict):
        item_id = str(event.get('item_id', '') or '')
        response_id = str(event.get('response_id', '') or '')
        transcript = str(event.get('transcript') or event.get('text') or '').strip()
        if transcript:
            self._assistant_text[item_id] = transcript

        self._publish_assistant_final(item_id, response_id)

    def _handle_response_done(self, event: dict):
        response = event.get('response', {}) or {}
        response_id = str(response.get('id', '') or '')
        output_items = response.get('output', []) or []

        # Turn boundary: reset the filler gate so the next user turn can be masked.
        self._current_turn_audio_started = False
        self._user_audio_frames_sent = 0
        self._active_turn_id = response_id
        self._cancel_filler()

        for item in output_items:
            item_id = str(item.get('id', '') or '')
            content = item.get('content', []) or []
            if item_id and item_id not in self._assistant_text:
                text_parts = []
                for part in content:
                    transcript = part.get('transcript') or part.get('text') or ''
                    if transcript:
                        text_parts.append(str(transcript))
                if text_parts:
                    self._assistant_text[item_id] = ''.join(text_parts)

            text = self._assistant_text.get(item_id, '').strip()
            if not text:
                continue

            self._publish_assistant_final(item_id, response_id)

        if response_id:
            self.get_logger().info(f'OpenAI Realtime finished response {response_id}')
        if response_id:
            self._mark_response_inactive(response_id)
        else:
            self._mark_response_inactive()
        self._last_response_request_item_id = ''
        self._assistant_name_question_active = False
        if self._pending_resume_text:
            self._clear_pending_resume()
            self._refresh_session()
        if (
            not self._user_speaking
            and not self.conversation_paused
            and not self.waiting_for_robot_confirmation
        ):
            self._schedule_deferred_response_after_response_done()

    def _refresh_session(self):
        if not self._connected.is_set() or self.current_backend != 'openai_realtime':
            return

        instructions = self._build_instructions()
        turn_detection = {
            'type': 'server_vad',
            'threshold': self.vad_threshold,
            'prefix_padding_ms': self.vad_prefix_padding_ms,
            'silence_duration_ms': self.vad_silence_duration_ms,
            # create_response defaults to TRUE server-side: the API answers the
            # moment its VAD hears silence. That bypasses every local gate this
            # node exists for (attention focus, diarization policy, transcript
            # filters, wait_for_user) AND races our own response.create — the
            # server answered ~5ms after speech_stop, then our deferred request
            # fired again when that response finished, so every user turn got
            # TWO spoken answers. With local gating on, this node is the sole
            # authority on when to speak.
            'create_response': not self.local_response_gating,
            # Keep server-side barge-in: new user speech still cancels playback.
            'interrupt_response': True,
        }

        session = {
            'type': 'realtime',
            'instructions': instructions,
            'audio': {
                'input': {
                    'format': {
                        'type': 'audio/pcm',
                        'rate': self.api_sample_rate
                    },
                    'turn_detection': turn_detection
                },
                'output': {
                    'voice': self.voice,
                    'format': {
                        'type': 'audio/pcm',
                        'rate': self.api_sample_rate
                    }
                }
            }
        }
        tools = []
        if self.wait_for_user_tool_enabled:
            tools.append(build_realtime_wait_for_user_tool())
        if self.web_search_enabled:
            tools.append(build_realtime_web_search_tool())
        if self.emotion_tool_enabled:
            tools.append(build_report_emotion_tool())
        if self.speaker_info_tool_enabled:
            tools.append(build_get_speaker_info_tool())
        if self.name_capture_tool_enabled:
            tools.append(build_remember_person_tool())
        if self.pause_tool_enabled:
            tools.append(build_set_conversation_pause_tool())
        if tools:
            session['tools'] = tools
            session['tool_choice'] = 'auto'
        if self._reasoning_config_enabled():
            session['reasoning'] = {'effort': self.reasoning_effort}
        if self.input_transcription_enabled:
            transcription = {'model': self.input_transcription_model}
            # Without a language hint the transcriber free-guesses and lands on
            # whatever fits the acoustics — observed real output for a bilingual
            # RO/EN speaker: Korean '어', Japanese '頑張って。', and command
            # phrases mangled beyond recognition ("robot move forward" ->
            # "Rjowmet mondfoward."). Those wreck command matching AND trip the
            # unsupported-script filter, which silently drops the turn.
            if self.input_transcription_language:
                transcription['language'] = self.input_transcription_language
            if self.input_transcription_prompt:
                transcription['prompt'] = self.input_transcription_prompt
            session['audio']['input']['transcription'] = transcription

        self._send_event({
            'type': 'session.update',
            'session': session,
        })

    def _reasoning_config_enabled(self) -> bool:
        if not self.reasoning_enabled or not self.reasoning_effort:
            return False
        return self.model.startswith('gpt-realtime-2')

    def _should_filter_playback_input(self, now_ms: int) -> bool:
        if not self.playback_input_filter_enabled:
            return False

        active = self.robot_speaking
        if not active and self._last_robot_speaking_end_ms > 0:
            active = (
                (now_ms - self._last_robot_speaking_end_ms)
                <= self.playback_input_filter_post_playback_ms
            )

        if active:
            self._playback_guard_was_active = True
        return active

    def _remember_input_audio(self, samples):
        if not samples:
            return
        max_samples = max(
            1,
            int(self._current_input_sample_rate * max(0, self.utterance_capture_prefix_ms) / 1000.0),
        )
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

        min_samples = max(
            1,
            int(self._current_input_sample_rate * max(0, self.utterance_capture_min_ms) / 1000.0),
        )
        if len(self._current_user_audio) < min_samples:
            self._current_user_audio = []
            return

        out = Audio()
        out.sample_rate = self._current_input_sample_rate
        out.channels = self._current_input_channels
        out.data = list(self._current_user_audio)
        self.user_audio_segment_pub.publish(out)
        self._current_user_audio = []

    def _remember_assistant_output(self, text: str):
        normalized = self._normalize_text(text)
        if not normalized:
            return

        now = time.monotonic()
        self._recent_assistant_outputs.append((normalized, now))
        retention_window_s = max(self.assistant_echo_window_s * 2.0, 6.0)
        cutoff = now - retention_window_s
        self._recent_assistant_outputs = [
            (assistant_text, ts)
            for assistant_text, ts in self._recent_assistant_outputs
            if ts >= cutoff
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
        self._recent_assistant_outputs = [
            (assistant_text, ts)
            for assistant_text, ts in self._recent_assistant_outputs
            if ts >= cutoff
        ]
        if not self._recent_assistant_outputs:
            return False

        for assistant_text, _ in reversed(self._recent_assistant_outputs):
            if is_probable_assistant_echo(
                text,
                assistant_text,
                threshold=self.assistant_echo_similarity_threshold,
                min_length=self.assistant_echo_min_length,
            ):
                return True
        return False

    def _build_instructions(self) -> str:
        extras = []
        assistant_name_question = self._assistant_name_question_active
        extras.append(
            'Your own assistant name is Robot. '
            'If the user asks your name, answer "Robot". '
            'Do not use any speaker preferred name as your own identity.'
        )
        if assistant_name_question:
            extras.append(
                'The user is asking your name right now. '
                'Answer clearly with "My name is Robot." and do not mention any user name.'
            )
            extras.append(
                'For this turn, ignore user profile names when composing the answer.'
            )
        if self.current_speaker != 'Unknown':
            extras.append(
                f'Current internal speaker label: {self.current_speaker}. '
                'This is a technical identifier, not a spoken name.'
            )
        diarization_candidate = self._active_diarization_candidate()
        if diarization_candidate:
            extras.append(
                'Diarization assist recently matched the current voice to '
                f'{diarization_candidate.get("speaker", "Unknown")} '
                f'with confidence {float(diarization_candidate.get("confidence", 0.0) or 0.0):.2f}. '
                'Use this only as speaker context; still follow the turn policy for whether to answer.'
            )
        preferred_name = self._voice_correlated_preferred_name()
        if preferred_name and not assistant_name_question:
            extras.append(
                f'Preferred spoken name for the current speaker: {preferred_name}. '
                'Never call the user by internal labels like speaker_001.'
            )
            extras.append(
                'If the user asks whether you remember their name or asks what their name is, '
                'answer directly with the preferred spoken name.'
            )
        preferred_language = self.person_context.get('preferred_language', '')
        if preferred_language:
            extras.append(f'Preferred language for this speaker: {preferred_language}.')
        conversation_language = self.language_tracker.current_language
        if conversation_language == 'ro':
            extras.append(
                'Current conversation language is Romanian. Keep speaking Romanian unless the user clearly asks to switch language.'
            )
        elif conversation_language == 'en':
            extras.append(
                'Current conversation language is English. Keep speaking English unless the user clearly asks to switch language.'
            )
        facts = self.person_context.get('facts', []) or []
        if facts:
            extras.append('Known personal facts: ' + '; '.join(str(fact) for fact in facts[:8]) + '.')
        if self.waiting_for_robot_confirmation:
            extras.append(
                'A risky robot command is awaiting local confirmation. '
                'Do not speak. Let the user answer yes/no without assistant chatter.'
            )
        if self.conversation_paused:
            extras.append(
                'The user told you to wait because they are talking with someone else. '
                'Stay silent until the local controller resumes the conversation.'
            )
        if self.wait_for_user_tool_enabled or self.web_search_enabled:
            extras.append(
                'Turn policy: before each response, silently classify the latest user audio as one of: '
                'no_response, direct_answer, needs_search, or clarification_needed. '
                'Do not reveal this classification or your private reasoning.'
            )
        if self.wait_for_user_tool_enabled:
            extras.append(
                'If the latest audio is silence, background noise, TV audio, assistant echo, a side conversation, '
                'or speech not addressed to Robot, call wait_for_user and do not speak afterward. '
                'Do not say "I am here", "I did not catch that", "take your time", or similar filler for no-response audio. '
                'Resume normal replies only when the user clearly addresses Robot, asks for help, or continues the active conversation.'
            )
            extras.append(
                'If the user is clearly addressing Robot but the audio is unclear, ask one short clarification question instead of calling tools or guessing.'
            )
        if self._pending_resume_text:
            extras.append(
                'There is an interrupted assistant reply pending. '
                'If the user asks to continue, resume, reia raspunsul, continua, or continue please, '
                'continue that interrupted reply from where it was cut, without restarting from the beginning.'
            )
            if self._pending_resume_remaining:
                extras.append(
                    f'Approximate remaining part of the interrupted reply: "{self._pending_resume_remaining}"'
                )
            else:
                extras.append(
                    f'Interrupted reply to continue from naturally: "{self._pending_resume_text}"'
                )
            extras.append(
                'If the user asks a different question or changes topic, answer the new request normally and '
                'ignore the interrupted reply.'
            )
        if self.web_search_enabled:
            extras.append(
                'When the user asks for current, live, recent, online, or otherwise time-sensitive information, '
                'or explicitly asks you to search the internet, call the web_search tool before answering. '
                'Use web_search for news, weather, prices, sports scores, laws or rules that may have changed, '
                'company or product updates, public figures, schedules, recommendations involving spending time or money, '
                'or any request to look something up. '
                'Do not use web_search for stable facts, casual chat, local robot commands, or personal-memory questions. '
                'Do not pretend to have browsed if you did not use the tool.'
            )
        if self.speaker_info_tool_enabled:
            extras.append(
                "You have a get_speaker_info tool that returns the CURRENT speaker's "
                'preferred spoken name, preferred language, and known facts. The speaker '
                'can change mid-conversation, so call get_speaker_info whenever you need '
                'to address someone by name or recall what you know about them, rather '
                'than assuming the context above is still current.'
            )
        if self.emotion_tool_enabled:
            extras.append(
                "Whenever you detect a clear emotional state in the user's voice, call "
                'the report_user_emotion tool with the emotion and a brief reason, and '
                'mirror it in your delivery: match happy, enthusiastic, or playful energy '
                'with a brighter, livelier voice; counterbalance sad, tired, or anxious '
                'with a softer, calmer, reassuring tone; and answer frustration with a '
                'steady, empathetic voice. Reporting the emotion is silent — keep '
                'answering the user normally in the same turn.'
            )
        if self.name_capture_tool_enabled:
            extras.append(
                'When the user introduces themselves by name, in any language or phrasing, '
                'call the remember_person tool with that name so the robot can learn their voice.'
            )
        if self.pause_tool_enabled:
            extras.append(
                'You have a set_conversation_pause tool. When the user asks you to wait, '
                'hold on, give them a moment, or pause, call it with paused=true and then '
                'stay silent — they may talk to other people meanwhile. Call it with '
                'paused=false only when the same user says they are back, ready, or want '
                'to continue.'
            )
        if self.doa_enabled and self.current_doa is not None:
            extras.append(
                f'The current speaker is at approximately {self.current_doa} degrees '
                'relative to the robot. Use this only for spatial awareness; never read '
                'the angle out loud.'
            )
        return ' '.join([self.base_instructions, *extras]).strip()

    def _voice_correlated_preferred_name(self) -> str:
        if self.current_speaker == 'Unknown':
            return ''
        context_speaker = str(self.person_context.get('speaker', 'Unknown') or 'Unknown')
        if context_speaker != self.current_speaker:
            return ''
        return str(self.person_context.get('preferred_name', '') or '').strip()

    @staticmethod
    def _is_name_identity_question(normalized_text: str) -> bool:
        text = (normalized_text or '').strip()
        if not text:
            return False
        patterns = (
            'do you know my name',
            'do you remember my name',
            'what is my name',
            'what s my name',
            'know my name',
            'remember my name',
            'numele meu',
            'cum ma cheama',
            'stii numele meu',
            'imi stii numele',
            'tii minte numele meu',
        )
        return any(pattern in text for pattern in patterns)

    @staticmethod
    def _is_assistant_name_question(normalized_text: str) -> bool:
        text = (normalized_text or '').strip()
        if not text:
            return False
        patterns = (
            'what is your name',
            'what s your name',
            'who are you',
            'numele tau',
            'cum te cheama',
            'cum te numesti',
            'care e numele tau',
        )
        return any(pattern in text for pattern in patterns)

    def _cancel_and_clear(self):
        self._cancel_pending_response_create()
        self._cancel_pending_diarization_policy()
        if not self._connected.is_set():
            return
        if self._response_active or self._response_create_pending:
            self._send_event({'type': 'response.cancel'})
        self._send_event({'type': 'input_audio_buffer.clear'})
        self._assistant_text.clear()
        self._published_final_items.clear()
        self._seen_output_audio_for_response.clear()
        self._clear_playback_progress()
        self._clear_pending_resume()
        self._last_response_request_item_id = ''
        self._capture_user_audio = False
        self._current_user_audio = []
        self._recent_input_audio = []
        self._user_speaking = False
        self._playback_guard_was_active = False
        self._playback_frames_blocked = 0
        self._last_robot_speaking_end_ms = 0
        self._last_assistant_audio_at = 0.0
        self._recent_assistant_outputs = []
        self.playback_input_filter.reset()
        self._clear_deferred_response()
        self._mark_response_inactive()

    def _truncate_current_audio(self, reason: str):
        if not self._connected.is_set():
            return

        item_id = self._last_playback_progress.get('item_id', '')
        played_ms = int(self._last_playback_progress.get('played_ms', 0) or 0)
        signature = (item_id, played_ms)
        now = time.monotonic()

        if (
            signature == self._last_truncate_signature
            and (now - self._last_truncate_time) < 0.5
        ):
            return

        self._last_truncate_signature = signature
        self._last_truncate_time = now

        should_truncate = self._response_active or bool(item_id)
        if self._response_create_pending:
            should_truncate = True
        if not should_truncate:
            return

        self._capture_pending_resume(item_id, played_ms)
        if self._response_active or self._response_create_pending:
            self._send_event({'type': 'response.cancel'})
        if item_id:
            self._send_event({
                'type': 'conversation.item.truncate',
                'item_id': item_id,
                'content_index': 0,
                'audio_end_ms': max(0, played_ms),
            })
        self._mark_response_inactive()
        self.get_logger().debug(
            f'Truncated current assistant audio ({reason}): item={item_id}, played_ms={played_ms}'
        )
        self._refresh_session()

    def _cancel_pending_response_create(self):
        timer = self._pending_response_timer
        self._pending_response_timer = None
        self._pending_response_item_id = ''
        self._pending_response_reason = ''
        if timer is None:
            return
        try:
            timer.cancel()
        except Exception:
            pass

    def _schedule_response_create(self, item_id: str, *, reason: str) -> bool:
        return self._schedule_response_create_with_delay(
            item_id,
            reason=reason,
            delay_ms=self.response_create_delay_ms,
        )

    def _schedule_response_create_with_delay(
        self,
        item_id: str,
        *,
        reason: str,
        delay_ms: int,
    ) -> bool:
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
            if block_reason == 'user_speaking':
                self._remember_deferred_response(item_id, reason)
                self.get_logger().debug(
                    f'Deferred realtime response ({reason}) while user is still speaking'
                )
            elif block_reason in ('response_pending', 'response_active'):
                self._remember_deferred_response(item_id, reason)
                self.get_logger().debug(
                    f'Queued realtime response ({reason}) while another response is still busy'
                )
            return False

        self._clear_deferred_response()
        self._cancel_pending_response_create()
        delay_ms = max(0, int(delay_ms))
        if delay_ms <= 0:
            return self._request_response_create(item_id, reason=reason)

        self._pending_response_item_id = item_id
        self._pending_response_reason = reason
        delay_s = delay_ms / 1000.0
        self._pending_response_timer = self.create_timer(
            delay_s,
            self._fire_pending_response_create,
        )
        self.get_logger().debug(
            f'Scheduled realtime response in {delay_s:.3f}s ({reason}) for item={item_id or "n/a"}'
        )
        return True

    def _fire_pending_response_create(self):
        item_id = self._pending_response_item_id
        reason = self._pending_response_reason or 'delayed_response'
        self._cancel_pending_response_create()
        self._request_response_create(item_id, reason=reason)

    def _send_event(self, event: dict) -> bool:
        if not self._connected.is_set() or self._ws_app is None:
            return False

        payload = json.dumps(event, separators=(',', ':'))
        try:
            with self._send_lock:
                self._ws_app.send(payload)
            return True
        except Exception as exc:
            self.get_logger().error(f'Failed to send OpenAI Realtime event: {exc}')
            self._connected.clear()
            return False

    @staticmethod
    def _resample_pcm16(audio: np.ndarray, original_rate: int, target_rate: int) -> np.ndarray:
        if original_rate == target_rate or audio.size == 0:
            return audio.astype(np.int16, copy=False)

        duration = audio.size / float(original_rate)
        target_samples = max(1, int(round(duration * target_rate)))

        source_positions = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
        target_positions = np.linspace(0.0, 1.0, num=target_samples, endpoint=False)
        resampled = np.interp(target_positions, source_positions, audio.astype(np.float32))
        return np.clip(np.round(resampled), -32768, 32767).astype(np.int16)

    def _publish_assistant_final(self, item_id: str, response_id: str):
        if not item_id or item_id in self._published_final_items:
            return

        text = self._assistant_text.get(item_id, '').strip()
        if not text:
            return

        out = Transcription()
        out.text = text
        out.language = ''
        out.confidence = 1.0
        self.response_pub.publish(out)

        final_chunk = TextChunk()
        final_chunk.text = ''
        final_chunk.language = ''
        final_chunk.is_final = True
        final_chunk.session_id = response_id or item_id
        self.stream_pub.publish(final_chunk)

        self._published_final_items.add(item_id)
        self._remember_assistant_output(text)

    def _mark_response_active(self, response_id: str = ''):
        self._response_create_pending = False
        self._response_active = True
        if response_id:
            self._active_response_id = response_id

    def _mark_response_inactive(self, response_id: str = ''):
        self._response_create_pending = False
        if not response_id or response_id == self._active_response_id:
            self._response_active = False
            self._active_response_id = ''

    def _mark_response_create_pending(self):
        self._response_create_pending = True

    def _clear_response_create_pending(self):
        self._response_create_pending = False

    def _clear_playback_progress(self):
        self._last_playback_progress = {
            'stream_id': '',
            'item_id': '',
            'played_ms': 0,
            'stopped': False,
        }
        self._last_truncate_signature = ('', -1)

    def _publish_status(self, status: str):
        msg = String()
        msg.data = status
        self.status_pub.publish(msg)

    def _cancel_current_pending_response(self, reason: str):
        self._cancel_pending_response_create()
        if self._response_active or self._response_create_pending:
            self._send_event({'type': 'response.cancel'})
            self._mark_response_inactive()
        self._send_event({'type': 'input_audio_buffer.clear'})
        self.get_logger().debug(f'Canceled pending realtime response ({reason})')

    def _delete_conversation_item(self, item_id: str, reason: str):
        if not item_id:
            return
        self._send_event({
            'type': 'conversation.item.delete',
            'item_id': item_id,
        })
        self.get_logger().debug(f'Deleted realtime conversation item {item_id} ({reason})')

    def _consume_focus_candidate(self) -> str:
        now = time.monotonic()
        candidate = self.pending_focus_speaker
        candidate_at = self.pending_focus_at
        self.pending_focus_speaker = 'Unknown'
        self.pending_focus_at = 0.0
        if candidate == 'Unknown':
            return 'Unknown'
        if (now - candidate_at) > self.focus_recognition_window_s:
            return 'Unknown'
        return candidate

    def _record_speaker_event(self, speaker: str):
        now = time.monotonic()
        speaker = (speaker or '').strip() or 'Unknown'
        if speaker == 'Unknown':
            return
        self.recent_speaker_events.append((now, speaker))
        cutoff = now - max(0.5, self.multi_speaker_window_s)
        self.recent_speaker_events = [
            (event_time, event_speaker)
            for event_time, event_speaker in self.recent_speaker_events
            if event_time >= cutoff
        ]

    def _has_multi_speaker_context(self) -> bool:
        now = time.monotonic()
        cutoff = now - max(0.5, self.multi_speaker_window_s)
        self.recent_speaker_events = [
            (event_time, event_speaker)
            for event_time, event_speaker in self.recent_speaker_events
            if event_time >= cutoff
        ]
        speakers = {
            speaker
            for _, speaker in self.recent_speaker_events
            if speaker and speaker != 'Unknown'
        }
        return len(speakers) >= max(2, self.multi_speaker_switch_threshold)

    @staticmethod
    def _is_benign_realtime_error(message: str) -> bool:
        normalized = (message or '').strip().lower()
        return (
            'cancellation failed: no active response found' in normalized
            or 'already has an active response in progress' in normalized
            or ('audio content of' in normalized and 'already shorter than' in normalized)
        )

    def _capture_pending_resume(self, item_id: str, played_ms: int):
        text = self._assistant_text.get(item_id, '').strip() if item_id else ''
        if not text:
            return

        self._pending_resume_text = text
        self._pending_resume_played_ms = max(0, int(played_ms))
        self._pending_resume_remaining = self._estimate_remaining_text(text, played_ms)
        self._resume_requested = False

    def _clear_pending_resume(self):
        self._pending_resume_text = ''
        self._pending_resume_remaining = ''
        self._pending_resume_played_ms = 0
        self._resume_requested = False

    def _request_response_create(self, item_id: str, *, reason: str) -> bool:
        # When local gating is off the server auto-answers user turns, so asking
        # for another response here would double up. Node-initiated reasons are
        # still allowed: server VAD never creates those.
        if not self.local_response_gating and reason not in SERVER_MODE_ALLOWED_REASONS:
            return False
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
            if block_reason == 'user_speaking':
                self._remember_deferred_response(item_id, reason)
            elif block_reason in ('response_pending', 'response_active'):
                self._remember_deferred_response(item_id, reason)
            return False
        self._clear_deferred_response()
        if not self._send_event({'type': 'response.create'}):
            return False
        self._mark_response_create_pending()
        self._last_response_request_item_id = item_id
        self.get_logger().debug(
            f'Requested realtime response ({reason}) for item={item_id or "n/a"}'
        )
        return True

    def _estimate_remaining_text(self, text: str, played_ms: int) -> str:
        clean_text = text.strip()
        if not clean_text:
            return ''
        if played_ms <= 0:
            return clean_text

        chars_per_second = 18.0
        approx_chars_spoken = int((played_ms / 1000.0) * chars_per_second)
        if approx_chars_spoken <= 0:
            return clean_text
        if approx_chars_spoken >= len(clean_text):
            return ''

        cut_idx = approx_chars_spoken
        while cut_idx < len(clean_text) and not clean_text[cut_idx].isspace():
            cut_idx += 1
        if cut_idx >= len(clean_text):
            return ''

        return clean_text[cut_idx:].lstrip(' ,.;:!?-')

    def _is_resume_request(self, text: str) -> bool:
        return is_resume_request(text)

    def _should_preserve_paused_transcript(self, text: str) -> bool:
        return should_preserve_paused_transcript(text)

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
            return 'assistant_echo'
        return ''

    @staticmethod
    def _normalize_text(text: str) -> str:
        return normalize_realtime_text(text)

    def _remember_deferred_response(self, item_id: str, reason: str):
        self._deferred_response_item_id = item_id
        self._deferred_response_reason = reason

    def _clear_deferred_response(self):
        self._deferred_response_item_id = ''
        self._deferred_response_reason = ''

    def _schedule_deferred_response_after_speech_stop(self) -> bool:
        if not self._deferred_response_item_id and not self._deferred_response_reason:
            return False
        delay_ms = continued_turn_response_delay_ms(
            self.response_create_delay_ms,
            self.continued_turn_response_delay_ms,
        )
        return self._schedule_response_create_with_delay(
            self._deferred_response_item_id,
            reason=self._deferred_response_reason or 'continued_turn_after_resume',
            delay_ms=delay_ms,
        )

    def _schedule_deferred_response_after_response_done(self) -> bool:
        if not self._deferred_response_item_id and not self._deferred_response_reason:
            return False
        return self._schedule_response_create_with_delay(
            self._deferred_response_item_id,
            reason=self._deferred_response_reason or 'deferred_after_response_done',
            delay_ms=self.response_create_delay_ms,
        )

    def _handle_user_transcript_completed(self, item_id: str, transcript: str):
        normalized = self._normalize_text(transcript)
        direct_address = has_direct_robot_address(normalized)
        control_action = detect_control_action(normalized)
        reengagement = is_reengagement_phrase(normalized)
        addressing_intent = (
            infer_addressing_intent(normalized, transcript)
            if self.semantic_addressing_enabled
            else None
        )
        focus_candidate = self._consume_focus_candidate()

        context = {
            'item_id': item_id,
            'transcript': transcript,
            'normalized': normalized,
            'direct_address': direct_address,
            'control_action': control_action,
            'reengagement': reengagement,
            'addressing_intent': addressing_intent,
            'focus_candidate': focus_candidate,
        }

        if control_action == 'hold_on' and can_accept_control_action(
            control_action,
            current_speaker=self.current_speaker,
            focused_speaker=self.focused_speaker,
            session_active=self.session_active,
            conversation_paused=self.conversation_paused,
            direct_address=direct_address,
            normalized_text=normalized,
        ):
            self._publish_response_policy(
                allow_response=False,
                reason='pause_command',
                context=context,
                action='local_pause',
            )
            self._delete_conversation_item(item_id, 'pause_command')
            self._apply_pause_state(True, publish=True)
            return

        self._decide_and_apply_transcript_policy(context, allow_diarization_wait=True)

    def _decide_and_apply_transcript_policy(
        self,
        context: dict,
        *,
        allow_diarization_wait: bool,
    ):
        item_id = str(context.get('item_id', '') or '')
        transcript = str(context.get('transcript', '') or '')
        normalized = str(context.get('normalized', '') or '')
        direct_address = bool(context.get('direct_address', False))
        control_action = context.get('control_action')
        reengagement = bool(context.get('reengagement', False))
        addressing_intent = context.get('addressing_intent')
        focus_candidate = str(context.get('focus_candidate', 'Unknown') or 'Unknown')
        multi_speaker_context = self._has_multi_speaker_context()

        allow, reason, effective_focus, effective_focus_time = decide_attention(
            session_active=self.session_active,
            conversation_paused=self.conversation_paused,
            current_speaker=self.current_speaker,
            focused_speaker=self.focused_speaker,
            last_focus_time=self.last_focus_time,
            focus_timeout_s=self.focus_timeout_s,
            allow_known_speaker_switch_without_address=(
                self.allow_known_speaker_switch_without_address
            ),
            direct_address=direct_address,
            reengagement=reengagement,
            robot_directive=False,
            control_action=control_action,
            normalized_text=normalized,
            semantic_addressing_score=(
                addressing_intent.score if addressing_intent is not None else 0.0
            ),
            semantic_side_score=(
                addressing_intent.side_score if addressing_intent is not None else 0.0
            ),
            multi_speaker_context=multi_speaker_context,
            loud_environment_mode=self.loud_environment_mode,
            indirect_address_score_threshold=self.indirect_address_score_threshold,
            loud_indirect_address_score_threshold=(
                self.loud_indirect_address_score_threshold
            ),
        )
        self.focused_speaker = effective_focus
        self.last_focus_time = effective_focus_time

        self._publish_response_policy(
            allow_response=allow,
            reason=reason,
            context=context,
            multi_speaker_context=multi_speaker_context,
        )

        if not allow:
            if allow_diarization_wait and self._should_wait_for_diarization_policy(reason):
                self._defer_for_diarization_policy(context, reason)
                return
            self._delete_conversation_item(item_id, reason)
            self.get_logger().info(
                f'Ignored realtime side conversation from speaker={self.current_speaker}: '
                f'"{transcript}" ({reason})'
            )
            return

        if self.conversation_paused:
            if control_action in ('continue', 'repeat') or reengagement:
                self._paused_transcript_pending = transcript
                self._paused_transcript_at = time.monotonic()
                self._apply_pause_state(False, publish=True)
                return
            self._delete_conversation_item(item_id, reason)
            return

        if normalized:
            self._last_accepted_user_transcript_norm = normalized
            self._last_accepted_user_transcript_at = time.monotonic()

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
                self.get_logger().info('Resume request detected for interrupted reply')
        active_language = self.language_tracker.observe(
            transcript,
            preferred_language=self.person_context.get('preferred_language', ''),
        )
        self._assistant_name_question_active = self._is_assistant_name_question(normalized)
        self._refresh_session()
        out = Transcription()
        out.text = transcript
        out.language = active_language
        out.confidence = 1.0
        self.transcription_pub.publish(out)
        if (
            self._is_name_identity_question(normalized)
            and not self._voice_correlated_preferred_name()
        ):
            wait_delay_ms = max(self.response_create_delay_ms, self.name_context_wait_ms)
            self._schedule_response_create_with_delay(
                item_id,
                reason='await_voice_name_context',
                delay_ms=wait_delay_ms,
            )
        else:
            self._schedule_response_create(item_id, reason='accepted_transcript')

    def _should_wait_for_diarization_policy(self, reason: str) -> bool:
        if self.diarization_response_wait_ms <= 0:
            return False
        if self.current_speaker != 'Unknown':
            return False
        if self.focused_speaker == 'Unknown':
            return False
        if self._active_diarization_candidate().get('speaker', 'Unknown') != 'Unknown':
            return False
        return reason == 'unknown_side_conversation'

    def _defer_for_diarization_policy(self, context: dict, reason: str):
        self._cancel_pending_diarization_policy()
        self._pending_diarization_policy = dict(context)
        delay_s = self.diarization_response_wait_ms / 1000.0
        self._pending_diarization_policy_timer = self.create_timer(
            delay_s,
            self._fire_pending_diarization_policy,
        )
        self.get_logger().debug(
            f'Deferred response policy for diarization assist in {delay_s:.2f}s ({reason})'
        )

    def _fire_pending_diarization_policy(self):
        context = self._pending_diarization_policy
        self._cancel_pending_diarization_policy()
        if context is None:
            return
        self._decide_and_apply_transcript_policy(context, allow_diarization_wait=False)

    def _cancel_pending_diarization_policy(self):
        timer = self._pending_diarization_policy_timer
        self._pending_diarization_policy_timer = None
        self._pending_diarization_policy = None
        if timer is None:
            return
        try:
            timer.cancel()
        except Exception:
            pass

    def _clear_diarization_candidate(self):
        self.diarization_candidate = {
            'speaker': 'Unknown',
            'confidence': 0.0,
            'source': '',
            'expires_at': 0.0,
        }

    def _active_diarization_candidate(self) -> dict:
        candidate = self.diarization_candidate
        if not candidate or candidate.get('speaker', 'Unknown') == 'Unknown':
            return {}
        if float(candidate.get('expires_at', 0.0) or 0.0) < time.monotonic():
            self._clear_diarization_candidate()
            return {}
        return candidate

    def _publish_response_policy(
        self,
        *,
        allow_response: bool,
        reason: str,
        context: dict,
        action: str = 'policy_decision',
        multi_speaker_context: bool | None = None,
    ):
        addressing_intent = context.get('addressing_intent')
        candidate = self._active_diarization_candidate()
        payload = {
            'action': action,
            'allow_response': bool(allow_response),
            'reason': reason,
            'speaker': self.current_speaker,
            'focused_speaker': self.focused_speaker,
            'direct_address': bool(context.get('direct_address', False)),
            'control_action': context.get('control_action'),
            'reengagement': bool(context.get('reengagement', False)),
            'semantic_addressing_score': (
                addressing_intent.score if addressing_intent is not None else 0.0
            ),
            'semantic_side_score': (
                addressing_intent.side_score if addressing_intent is not None else 0.0
            ),
            'semantic_label': (
                addressing_intent.label if addressing_intent is not None else ''
            ),
            'semantic_features': (
                list(addressing_intent.features) if addressing_intent is not None else []
            ),
            'multi_speaker_context': (
                self._has_multi_speaker_context()
                if multi_speaker_context is None
                else bool(multi_speaker_context)
            ),
            'diarization_candidate': {
                'speaker': candidate.get('speaker', 'Unknown'),
                'confidence': round(float(candidate.get('confidence', 0.0) or 0.0), 3),
                'source': candidate.get('source', ''),
            },
            'transcript_preview': str(context.get('transcript', '') or '')[:160],
            'created_at': time.time(),
        }
        msg = String()
        msg.data = json.dumps(payload, separators=(',', ':'))
        self.response_policy_pub.publish(msg)

    def _handle_function_call_event(self, event: dict):
        item = event.get('item', {}) or {}
        item_type = str(item.get('type', '') or '').strip()
        name = str(item.get('name', '') or event.get('name', '') or '').strip()
        call_id = str(item.get('call_id', '') or event.get('call_id', '') or '').strip()
        arguments = str(item.get('arguments', '') or event.get('arguments', '') or '')

        known_tools = (
            WEB_SEARCH_FUNCTION_NAME,
            WAIT_FOR_USER_FUNCTION_NAME,
            REPORT_EMOTION_FUNCTION_NAME,
            GET_SPEAKER_INFO_FUNCTION_NAME,
            REMEMBER_PERSON_FUNCTION_NAME,
            SET_PAUSE_FUNCTION_NAME,
        )
        if item_type and item_type != 'function_call':
            return
        if name not in known_tools or not call_id:
            return

        with self._tool_call_lock:
            if call_id in self._handled_tool_call_ids:
                return
            self._handled_tool_call_ids.add(call_id)
            if len(self._handled_tool_call_ids) > 256:
                self._handled_tool_call_ids.clear()
                self._handled_tool_call_ids.add(call_id)

        if name == WAIT_FOR_USER_FUNCTION_NAME:
            self._execute_wait_for_user_tool_call(call_id)
            return

        # The context tools answer synchronously — no network call, so there is
        # no reason to push them onto a worker thread.
        if name == REPORT_EMOTION_FUNCTION_NAME:
            self._execute_report_emotion_tool_call(call_id, arguments)
            return
        if name == GET_SPEAKER_INFO_FUNCTION_NAME:
            self._execute_get_speaker_info_tool_call(call_id)
            return
        if name == REMEMBER_PERSON_FUNCTION_NAME:
            self._execute_remember_person_tool_call(call_id, arguments)
            return
        if name == SET_PAUSE_FUNCTION_NAME:
            self._execute_set_pause_tool_call(call_id, arguments)
            return

        self.get_logger().info(f'OpenAI Realtime requested web search via tool call {call_id}')
        thread = threading.Thread(
            target=self._execute_web_search_tool_call,
            args=(call_id, arguments),
            daemon=True,
            name=f'web-search-{call_id[:8]}',
        )
        thread.start()

    def _execute_wait_for_user_tool_call(self, call_id: str):
        output = json.dumps({'ok': True, 'action': 'wait_for_user'}, separators=(',', ':'))
        self._send_event({
            'type': 'conversation.item.create',
            'item': {
                'type': 'function_call_output',
                'call_id': call_id,
                'output': output,
            },
        })
        self._clear_deferred_response()
        self.get_logger().debug(f'OpenAI Realtime chose to wait without response ({call_id})')

    @staticmethod
    def _parse_tool_arguments(arguments: str) -> dict:
        try:
            parsed = json.loads(arguments) if arguments else {}
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _send_tool_output(self, call_id: str, payload: dict) -> bool:
        return self._send_event({
            'type': 'conversation.item.create',
            'item': {
                'type': 'function_call_output',
                'call_id': call_id,
                'output': json.dumps(payload, separators=(',', ':')),
            },
        })

    def _execute_report_emotion_tool_call(self, call_id: str, arguments: str):
        """Publish the emotion the model heard so the rest of the robot can react."""
        args = self._parse_tool_arguments(arguments)
        emotion = normalize_emotion(str(args.get('emotion', '') or ''))
        reason = str(args.get('reason', '') or '').strip()
        speaker = self.current_speaker

        self.last_user_emotion = emotion
        msg = String()
        msg.data = json.dumps(
            {'speaker': speaker, 'emotion': emotion, 'reason': reason},
            separators=(',', ':'),
        )
        self.user_emotion_pub.publish(msg)
        self.get_logger().info(
            f"🎭 User emotion: {emotion.upper()} for speaker '{speaker}' (reason: '{reason}')"
        )
        self._send_tool_output(call_id, {'ok': True, 'emotion': emotion})

    def _execute_get_speaker_info_tool_call(self, call_id: str):
        """Answer with live speaker context.

        Reading ``self.person_context`` at call time (rather than baking it into
        the session instructions) is what lets personalization follow a speaker
        change without rebuilding the session.
        """
        context = self.person_context or {}
        preferred_name = str(context.get('preferred_name', '') or '').strip()
        payload = {
            'speaker_label': self.current_speaker,
            'known': bool(preferred_name) and self.current_speaker != 'Unknown',
            'preferred_name': preferred_name,
            'preferred_language': str(context.get('preferred_language', '') or '').strip(),
            'facts': context.get('facts', []) or [],
        }
        self.get_logger().debug(f'get_speaker_info -> {payload}')
        self._send_tool_output(call_id, payload)

    def _execute_remember_person_tool_call(self, call_id: str, arguments: str):
        """Forward a model-detected self-introduction to the enrollment path.

        The model decides *that* an introduction happened (any language or
        phrasing); person_memory_store_node still owns the audio buffer, the
        Unknown-speaker gate, and the actual voiceprint enrollment.
        """
        args = self._parse_tool_arguments(arguments)
        name = str(args.get('name', '') or '').strip()
        language = str(args.get('language', '') or '').strip()
        if not name:
            self._send_tool_output(call_id, {'ok': False, 'error': 'no name provided'})
            return

        msg = String()
        msg.data = json.dumps(
            {'preferred_name': name, 'preferred_language': language},
            separators=(',', ':'),
        )
        self.introduced_name_pub.publish(msg)
        self.get_logger().info(f"remember_person -> enrollment request for '{name}'")
        self._send_tool_output(call_id, {'ok': True, 'name': name})

    def _execute_set_pause_tool_call(self, call_id: str, arguments: str):
        """Pause/resume by model intent instead of by transcript regex.

        Resolving this semantically is what makes "hold on a sec" work in any
        language and phrasing, including ones the phrase lists never cover.
        """
        args = self._parse_tool_arguments(arguments)
        paused = bool(args.get('paused', False))

        # Acknowledge BEFORE flipping state: _apply_pause_state hard-stops
        # in-flight speech, and the tool output must still reach the session.
        self._send_tool_output(call_id, {'ok': True, 'paused': paused})
        if paused == self.conversation_paused:
            return
        self.get_logger().info(
            f'set_conversation_pause -> {"paused" if paused else "resumed"} (by model intent)'
        )
        self._apply_pause_state(paused, publish=True)

    def _execute_web_search_tool_call(self, call_id: str, arguments: str):
        query = ''
        output = ''

        try:
            parsed_arguments = json.loads(arguments) if arguments else {}
            if not isinstance(parsed_arguments, dict):
                raise ValueError('Tool arguments must be a JSON object.')
            query = str(parsed_arguments.get('query', '') or '').strip()
            if not query:
                raise ValueError('Missing required "query" argument.')

            payload = call_openai_web_search(
                self.api_key,
                query,
                model=self.web_search_model,
                search_context_size=self.web_search_context_size,
                timeout_s=self.web_search_timeout_s,
                max_output_tokens=self.web_search_max_output_tokens,
            )
            output = build_web_search_tool_output(
                query,
                payload=payload,
                max_sources=self.web_search_sources_limit,
            )
            self.get_logger().info(f'OpenAI Responses web search completed for: {query}')
        except Exception as exc:
            message = str(exc).strip() or 'Unknown web search failure.'
            self.get_logger().error(f'OpenAI web search tool failed: {message}')
            output = build_web_search_tool_output(query, error=message)

        if not self._send_event({
            'type': 'conversation.item.create',
            'item': {
                'type': 'function_call_output',
                'call_id': call_id,
                'output': output,
            },
        }):
            return

        if self.current_backend != 'openai_realtime':
            return
        if self.conversation_paused or self.waiting_for_robot_confirmation:
            return
        if self._user_speaking:
            self._remember_deferred_response('', 'tool_output_ready')
            return

        if self._request_response_create('', reason='tool_output_ready'):
            self.get_logger().debug(f'Requested follow-up realtime response after tool call {call_id}')


def main(args=None):
    rclpy.init(args=args)
    node = None

    try:
        node = OpenAIRealtimeNode()
        rclpy.spin(node)
    except RuntimeError as exc:
        print(f'Failed to start OpenAI Realtime node: {exc}')
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:
            pass


if __name__ == '__main__':
    main()
