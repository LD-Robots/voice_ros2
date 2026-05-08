#!/usr/bin/env python3
"""
LLM Node - Language Model processing with streaming output.

FEATURES (synced with Conversational_Robot Python):
  - Provider options: Groq or Mistral
  - Web search via Groq Compound model for current questions
  - Keyword detection for news, weather, prices, elections, etc.

Subscribes to: /transcription (Transcription)
Publishes to: 
  - /llm_stream (TextChunk) - streaming chunks
  - /llm_response (Transcription) - complete response (for compatibility)
"""
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Transcription, TextChunk
from std_msgs.msg import Bool, String
import json
import os
import re
import uuid
import threading
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest
from conversational_client.conversation_utils import detect_control_action, normalize_text
from conversational_client.robot_command_utils import looks_like_robot_command
from .prompt_config import load_prompt_defaults

# Load variables from .env
try:
    from dotenv import load_dotenv
    # Search for the .env file in voice_ros2/ (works from install/ or src/)
    current_path = Path(__file__).resolve()
    # Walk up until we find the voice_ros2 directory
    while current_path.name != 'voice_ros2' and current_path != current_path.parent:
        current_path = current_path.parent
    
    # If we don't find voice_ros2, try going 3 levels up from this file
    if current_path.name != 'voice_ros2':
        current_path = Path(__file__).resolve().parents[3]
    
    env_path = current_path / '.env'
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        print(f"✅ Loaded .env from: {env_path}")
    else:
        print(f"⚠️ .env not found at: {env_path}")
except ImportError:
    print("⚠️ python-dotenv not installed. Run: pip install python-dotenv")

# Groq for LLM
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    print("⚠️ groq not installed. Run: pip install groq")

# Regex to detect the end of a sentence
SENTENCE_END = re.compile(r'[.!?;:]\s*$')


class LLMNode(Node):
    def __init__(self):
        super().__init__('llm_node')
        
        # Configurable parameters
        self.declare_parameter('provider', 'groq')
        self.declare_parameter('model', 'llama-3.1-8b-instant')
        self.declare_parameter('max_tokens', 150)
        self.declare_parameter('temperature', 0.7)
        self.declare_parameter('min_chunk_chars', 40)  # Min chars per chunk
        self.declare_parameter('transcription_topic', '/attended_transcription')
        
        default_system_prompt = str(load_prompt_defaults().get('llm_system_prompt', ''))
        
        self.declare_parameter('system_prompt', default_system_prompt)
        
        # Web search parameters
        self.declare_parameter('websearch_enabled', True)
        self.declare_parameter('websearch_model', 'compound-beta')  # Groq compound model
        self.declare_parameter('websearch_max_tokens', 300)
        self.declare_parameter('legacy_playback_guard_enabled', True)
        self.declare_parameter('legacy_playback_guard_post_ms', 900)
        self.declare_parameter('mistral_api_url', 'https://api.mistral.ai/v1/chat/completions')
        self.declare_parameter('mistral_api_key_env', 'VOXTRAL_API_KEY')
        self.declare_parameter('mistral_prompt_mode', 'reasoning')
        self.declare_parameter('mistral_reasoning_effort', 'none')
        self.declare_parameter('mistral_timeout_s', 45.0)
        self.declare_parameter('mistral_include_thinking_chunks', False)
        self.declare_parameter('assistant_echo_filter_enabled', True)
        self.declare_parameter('assistant_echo_similarity_threshold', 84.0)
        self.declare_parameter('assistant_echo_min_chars', 10)
        
        self.provider = str(self.get_parameter('provider').value).lower()
        self.model = self.get_parameter('model').value
        self.max_tokens = self.get_parameter('max_tokens').value
        self.temperature = self.get_parameter('temperature').value
        self.min_chunk_chars = self.get_parameter('min_chunk_chars').value
        self.system_prompt = self.get_parameter('system_prompt').value
        self.transcription_topic = str(self.get_parameter('transcription_topic').value)
        self.mistral_api_url = str(self.get_parameter('mistral_api_url').value).strip()
        self.mistral_api_key_env = str(
            self.get_parameter('mistral_api_key_env').value
        ).strip() or 'VOXTRAL_API_KEY'
        self.mistral_prompt_mode = str(
            self.get_parameter('mistral_prompt_mode').value
        ).strip().lower()
        self.mistral_reasoning_effort = str(
            self.get_parameter('mistral_reasoning_effort').value
        ).strip().lower()
        self.mistral_timeout_s = max(
            1.0,
            float(self.get_parameter('mistral_timeout_s').value),
        )
        self.mistral_include_thinking_chunks = bool(
            self.get_parameter('mistral_include_thinking_chunks').value
        )
        self.assistant_echo_filter_enabled = bool(
            self.get_parameter('assistant_echo_filter_enabled').value
        )
        self.assistant_echo_similarity_threshold = float(
            self.get_parameter('assistant_echo_similarity_threshold').value
        )
        self.assistant_echo_min_chars = max(
            1, int(self.get_parameter('assistant_echo_min_chars').value)
        )

        # Web search
        self.websearch_enabled = self.get_parameter('websearch_enabled').value
        self.websearch_model = self.get_parameter('websearch_model').value
        self.websearch_max_tokens = self.get_parameter('websearch_max_tokens').value
        self.legacy_playback_guard_enabled = bool(
            self.get_parameter('legacy_playback_guard_enabled').value
        )
        self.legacy_playback_guard_post_ms = max(
            0,
            int(self.get_parameter('legacy_playback_guard_post_ms').value),
        )
        self.websearch_supported = self.provider == 'groq'
        if self.websearch_enabled and not self.websearch_supported:
            self.get_logger().warn(
                f'Websearch disabled for provider={self.provider}; supported only on groq pipeline'
            )
            self.websearch_enabled = False
        
        # Stream shaper parameters
        self.declare_parameter('prebuffer_chars', 120)
        self.declare_parameter('soft_max_chars', 140)
        self.declare_parameter('max_idle_ms', 250)
        
        self.prebuffer_chars = self.get_parameter('prebuffer_chars').value
        self.soft_max_chars = self.get_parameter('soft_max_chars').value
        self.max_idle_ms = self.get_parameter('max_idle_ms').value
        
        # Backchannel parameters
        self.declare_parameter('backchannel_enabled', True)
        self.declare_parameter('backchannel_delay_ms', 2000)  # Delay before "One moment..."
        self.declare_parameter('backchannel_phrase_en', 'One moment please...')
        self.declare_parameter('backchannel_phrase_ro', 'Un moment...')
        
        self.backchannel_enabled = self.get_parameter('backchannel_enabled').value
        self.backchannel_delay_ms = self.get_parameter('backchannel_delay_ms').value
        self.backchannel_phrase_en = self.get_parameter('backchannel_phrase_en').value
        self.backchannel_phrase_ro = self.get_parameter('backchannel_phrase_ro').value
        
        # Fallback responses for error handling
        self.declare_parameter('fallback_timeout_en', "I'm taking longer than usual. Please try again.")
        self.declare_parameter('fallback_timeout_ro', "Îmi ia mai mult decât de obicei. Te rog încearcă din nou.")
        self.declare_parameter('fallback_error_en', "I had a technical issue. Please try again.")
        self.declare_parameter('fallback_error_ro', "Am avut o problemă tehnică. Te rog încearcă din nou.")
        self.declare_parameter('fallback_unknown_en', "That's outside my current knowledge.")
        self.declare_parameter('fallback_unknown_ro', "Nu am răspunsul încă, dar întrebări ca asta mă ajută să devin mai bun.")
        
        self.fallback = {
            'timeout_en': self.get_parameter('fallback_timeout_en').value,
            'timeout_ro': self.get_parameter('fallback_timeout_ro').value,
            'error_en': self.get_parameter('fallback_error_en').value,
            'error_ro': self.get_parameter('fallback_error_ro').value,
            'unknown_en': self.get_parameter('fallback_unknown_en').value,
            'unknown_ro': self.get_parameter('fallback_unknown_ro').value,
        }
        
        self.client = None
        self.api_key = ''
        if self.provider == 'groq':
            self.api_key = os.environ.get('GROQ_API_KEY', '')
            if not self.api_key:
                self.get_logger().error('GROQ_API_KEY environment variable not set!')
                raise RuntimeError('GROQ_API_KEY not set')
            if not GROQ_AVAILABLE:
                self.get_logger().error('groq package not installed!')
                raise RuntimeError('groq not available')
            self.client = Groq(api_key=self.api_key)
            self.get_logger().debug(f'✅ Groq client initialized with model: {self.model}')
        elif self.provider == 'mistral':
            self.api_key = os.environ.get(self.mistral_api_key_env, '')
            if not self.api_key:
                self.get_logger().error(f'{self.mistral_api_key_env} environment variable not set!')
                raise RuntimeError(f'{self.mistral_api_key_env} not set')
            self.get_logger().info(
                '✅ Mistral LLM initialized '
                f'(model={self.model}, prompt_mode={self.mistral_prompt_mode})'
            )
        else:
            raise RuntimeError(
                f"Unsupported llm provider '{self.provider}'. Use 'groq' or 'mistral'."
            )
        
        # Conversation history
        self.conversation_history = []
        self.last_assistant_response = ''
        self.current_backend = 'legacy'
        self.person_context = {
            'speaker': 'Unknown',
            'preferred_name': '',
            'preferred_language': '',
            'facts': [],
        }
        self.robot_speaking = False
        self.last_robot_speaking_end_at = 0.0
        
        # Speaker identification — who is speaking now
        self.current_speaker = "Unknown"
        self.speaker_sub = self.create_subscription(
            String,
            '/speaker_id',
            self._speaker_id_callback,
            10
        )
        
        # Track robot command confirmations
        self.waiting_for_robot_confirmation = False
        self.robot_status_sub = self.create_subscription(
            String,
            '/robot_command_status',
            self._robot_status_callback,
            10
        )
        self.person_context_sub = self.create_subscription(
            String,
            '/person_context',
            self._person_context_callback,
            10
        )
        self.backend_sub = self.create_subscription(
            String,
            '/conversation_backend',
            self._backend_callback,
            10
        )
        self.control_sub = self.create_subscription(
            String,
            '/conversation_control',
            self._control_callback,
            10
        )
        self.speaking_sub = self.create_subscription(
            Bool,
            '/is_speaking',
            self._speaking_callback,
            10
        )
        
        # Subscriber for transcription
        self.transcription_sub = self.create_subscription(
            Transcription,
            self.transcription_topic,
            self.transcription_callback,
            10
        )
        
        # Publisher for streaming chunks
        self.stream_pub = self.create_publisher(
            TextChunk,
            '/llm_stream',
            10
        )
        
        # Publisher for full response (compatibility)
        self.response_pub = self.create_publisher(
            Transcription,
            '/llm_response',
            10
        )
        
        # Publisher for TTS command (backchannel)
        self.tts_cmd_pub = self.create_publisher(
            String,
            '/tts_command',
            10
        )
        
        self.get_logger().debug(f'LLM Node started with STREAMING + BACKCHANNEL! websearch={self.websearch_enabled}')
    
    def _get_system_prompt_with_date(self) -> str:
        """Return system prompt with the current date injected."""
        date_str = datetime.now().strftime("%A, %B %d, %Y")
        return f"Today is {date_str}.\n\n{self.system_prompt}"
    
    def _get_fallback(self, key: str, lang: str) -> str:
        """Return the fallback message for key and language."""
        suffix = '_ro' if str(lang).lower().startswith('ro') else '_en'
        return self.fallback.get(f"{key}{suffix}", "")
    
    def _needs_websearch(self, text: str) -> bool:
        """Detect whether the question needs up-to-date web info."""
        if not self.websearch_enabled:
            return False
        
        text_lower = text.lower()
        
        # Keywords that indicate a need for current info
        current_info_keywords = [
            # English - time-sensitive
            "news", "today", "latest", "current", "recent", "now",
            "weather", "price", "stock", "score", "result",
            "who won", "what happened", "breaking",
            # English - factual questions that benefit from search
            "who is the", "who is", "president", "prime minister",
            "ceo of", "founder of", "how much does", "how much is",
            # English - elections & politics
            "election", "elected", "candidate", "vote", "voting",
            "parliament", "congress", "senator", "governor",
            # English - sports
            "match", "game", "championship", "tournament", "league",
            "world cup", "olympics", "fifa", "nba", "nfl",
            # English - entertainment
            "movie", "film", "actor", "actress", "oscar", "grammy",
            "album", "song", "concert", "tour", "netflix", "spotify",
            # English - tech & business
            "iphone", "android", "google", "apple", "microsoft", "tesla",
            "chatgpt", "openai", "cryptocurrency", "bitcoin", "gpt-4", "gpt-5",
            # Romanian - time-sensitive
            "știri", "stiri", "azi", "acum", "recent", "ultima",
            "vreme", "preț", "pret", "scor", "rezultat",
            "cine a câștigat", "cine a castigat", "ce s-a întâmplat",
            "cine este", "președinte", "presedinte", "prim-ministru",
            # Romanian - elections & politics
            "alegeri", "ales", "candidat", "vot", "votat", "votare",
            "parlament", "senator", "deputat", "partid", "guvern",
            "tur", "turul doi", "turul întâi", "campanie",
            # Romanian - sports
            "meci", "joc", "campionat", "liga", "fotbal", "nationala",
            "steaua", "dinamo", "cfr", "fcsb", "simona halep",
            # Romanian - entertainment
            "film", "actor", "actriță", "actrita", "serial", "netflix",
            "muzică", "muzica", "concert", "album", "cântăreț", "cantaret",
            # Romanian - tech & business
            "telefon", "aplicație", "aplicatie", "emag", "olx"
        ]
        
        for keyword in current_info_keywords:
            if keyword in text_lower:
                self.get_logger().debug(f'🔍 Web search triggered by keyword: "{keyword}"')
                return True
        
        return False

    def _extract_mistral_text(self, content) -> str:
        """Extract visible answer text from Mistral content chunks."""
        if isinstance(content, str):
            return content.strip()

        if not isinstance(content, list):
            return ''

        parts = []
        for chunk in content:
            if isinstance(chunk, str):
                if chunk.strip():
                    parts.append(chunk.strip())
                continue

            if not isinstance(chunk, dict):
                continue

            chunk_type = str(chunk.get('type', '')).lower()
            if chunk_type == 'text':
                text = str(chunk.get('text', '') or '').strip()
                if text:
                    parts.append(text)
                continue

            if chunk_type == 'thinking' and self.mistral_include_thinking_chunks:
                thinking_items = chunk.get('thinking') or []
                if isinstance(thinking_items, list):
                    for item in thinking_items:
                        if isinstance(item, dict) and str(item.get('type', '')).lower() == 'text':
                            text = str(item.get('text', '') or '').strip()
                            if text:
                                parts.append(text)

        return '\n'.join(parts).strip()

    def _mistral_chat_complete(self, messages, model: str, max_tokens: int) -> str:
        """Call Mistral Chat Completions (non-streaming) and return final answer text."""
        payload = {
            'model': model,
            'messages': messages,
            'max_tokens': int(max_tokens),
            'temperature': float(self.temperature),
            'stream': False,
        }
        if self.mistral_prompt_mode == 'reasoning':
            payload['prompt_mode'] = 'reasoning'
        if self.mistral_reasoning_effort in {'none', 'low', 'medium', 'high'}:
            payload['reasoning_effort'] = self.mistral_reasoning_effort

        candidate_payloads = [payload]
        relaxed_payload = dict(payload)
        relaxed_payload.pop('prompt_mode', None)
        relaxed_payload.pop('reasoning_effort', None)
        if relaxed_payload != payload:
            candidate_payloads.append(relaxed_payload)

        last_error = None
        for idx, body in enumerate(candidate_payloads):
            req = urlrequest.Request(
                self.mistral_api_url,
                data=json.dumps(body).encode('utf-8'),
                headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json',
                },
                method='POST',
            )
            try:
                with urlrequest.urlopen(req, timeout=self.mistral_timeout_s) as resp:
                    raw = resp.read().decode('utf-8')
                response = json.loads(raw)
                choices = response.get('choices') or []
                if not choices:
                    raise RuntimeError('Mistral response missing choices')
                message = choices[0].get('message') or {}
                text = self._extract_mistral_text(message.get('content'))
                if not text:
                    text = str(message.get('content', '') or '').strip()
                if not text:
                    raise RuntimeError('Mistral response did not include answer text')
                return text
            except urlerror.HTTPError as exc:
                detail = exc.read().decode('utf-8', errors='ignore')
                last_error = RuntimeError(f'Mistral HTTP {exc.code}: {detail[:240]}')
                if idx + 1 < len(candidate_payloads):
                    continue
                raise last_error from exc
            except urlerror.URLError as exc:
                raise RuntimeError(f'Mistral connection error: {exc}') from exc
            except Exception as exc:
                last_error = exc
                if idx + 1 < len(candidate_payloads):
                    continue
                raise

        raise RuntimeError(f'Mistral request failed: {last_error}')

    @staticmethod
    def _text_to_token_stream(text: str):
        """Yield pseudo-stream tokens from complete text."""
        for token in re.findall(r'\S+\s*', text):
            yield token

    def _is_robot_command(self, text: str) -> bool:
        """Check if the text is likely a robot command, to avoid LLM chatter."""
        return looks_like_robot_command(text, require_direct_robot_address=True)

    def _robot_status_callback(self, msg: String):
        """Monitor the robot command executor status to mute the LLM during confirmations."""
        status = msg.data
        if status == 'confirmation_required':
            self.waiting_for_robot_confirmation = True
            self.get_logger().info('🤐 Robot needs confirmation - suspending LLM for the next transcription')
        elif status in ['confirmation_accepted', 'confirmation_rejected', 'confirmation_timeout', 'canceled', 'completed', 'executing']:
            if self.waiting_for_robot_confirmation:
                self.waiting_for_robot_confirmation = False
                self.get_logger().info(f'🔊 Robot status {status} - LLM resumed')

    def _person_context_callback(self, msg: String):
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

    def _backend_callback(self, msg: String):
        backend = msg.data.strip() or 'legacy'
        self.current_backend = backend

    def _speaking_callback(self, msg: Bool):
        was_speaking = self.robot_speaking
        self.robot_speaking = bool(msg.data)
        if was_speaking and not self.robot_speaking:
            self.last_robot_speaking_end_at = time.monotonic()

    def _is_playback_guard_active(self) -> bool:
        if not self.legacy_playback_guard_enabled:
            return False
        if self.robot_speaking:
            return True
        if self.last_robot_speaking_end_at <= 0.0:
            return False
        elapsed_ms = (time.monotonic() - self.last_robot_speaking_end_at) * 1000.0
        return elapsed_ms <= float(self.legacy_playback_guard_post_ms)

    @staticmethod
    def _is_control_transcript(text: str) -> bool:
        normalized = normalize_text(text)
        return bool(detect_control_action(normalized))

    @staticmethod
    def _normalize_for_echo(text: str) -> str:
        if not text:
            return ''
        text = text.lower()
        text = unicodedata.normalize('NFD', text)
        text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
        text = re.sub(r'[^a-z0-9\s]', ' ', text)
        return ' '.join(text.split()).strip()

    def _looks_like_assistant_echo(self, user_text: str) -> bool:
        if not self.assistant_echo_filter_enabled:
            return False
        if not self.last_assistant_response:
            return False

        user_norm = self._normalize_for_echo(user_text)
        assistant_norm = self._normalize_for_echo(self.last_assistant_response)
        if (
            len(user_norm) < self.assistant_echo_min_chars
            or len(assistant_norm) < self.assistant_echo_min_chars
        ):
            return False

        if user_norm in assistant_norm:
            return True

        user_words = set(user_norm.split())
        assistant_words = set(assistant_norm.split())
        if len(user_words) >= 3:
            overlap_ratio = len(user_words.intersection(assistant_words)) / max(1, len(user_words))
            if overlap_ratio >= (self.assistant_echo_similarity_threshold / 100.0):
                return True

        return False

    def _control_callback(self, msg: String):
        if self.current_backend not in ('legacy', 'mistral_realtime'):
            return

        try:
            payload = json.loads(msg.data)
        except Exception:
            return

        action = str(payload.get('action', '') or '')
        language = str(payload.get('language', '') or '')
        if action == 'repeat' and self.last_assistant_response:
            self.get_logger().info('Repeating last assistant response from local memory')
            self._publish_immediate_response(self.last_assistant_response, language or 'en')
        elif action == 'continue' and self.last_assistant_response:
            user_text = 'Continue your last answer from where you stopped. Do not restart from the beginning.'
            if (language or '').startswith('ro'):
                user_text = 'Continuă ultimul răspuns exact de unde te-ai oprit, fără să îl reiei de la început.'
            thread = threading.Thread(
                target=self._process_streaming,
                args=(user_text, language or 'en'),
                daemon=True,
            )
            thread.start()

    def transcription_callback(self, msg: Transcription):
        """Process transcription and publish the LLM response in streaming."""
        if self.current_backend not in ('legacy', 'mistral_realtime'):
            return

        user_text = msg.text.strip()
        user_lang = msg.language
        
        if not user_text:
            self.get_logger().warn('Empty transcription received, skipping')
            return
            
        if self._is_robot_command(user_text):
            self.get_logger().info(f'🔇 Ignored robot command: {user_text}')
            return

        # If we are waiting for a safety confirmation (e.g. they said "yes" or "no")
        # we want the LLM to ignore it completely so it doesn't chat.
        if self.waiting_for_robot_confirmation:
            self.get_logger().info(f'🔇 Ignored text during robot confirmation: {user_text}')
            # We don't reset the flag here, we let the status topic do it
            return

        # Ignore regular user queries while robot playback is active
        # to avoid self-triggering on assistant voice leakage.
        # Control phrases (stop/continue/repeat/hold_on) still pass through.
        if self._is_playback_guard_active():
            if self._is_control_transcript(user_text):
                self.get_logger().debug('🎚️ Allowing control phrase during playback guard')
            else:
                self.get_logger().info(f'🔇 Ignored transcript during robot playback: {user_text}')
                return

        if self._looks_like_assistant_echo(user_text):
            self.get_logger().info(f'🔇 Ignored transcript similar to assistant reply: {user_text}')
            return
        
        self.get_logger().info(f'💬 User [{user_lang}]: {user_text}')
        
        # Process in a separate thread to avoid blocking ROS2
        thread = threading.Thread(
            target=self._process_streaming,
            args=(user_text, user_lang),
            daemon=True
        )
        thread.start()
    
    def _speaker_id_callback(self, msg: String):
        """
        Update the current speaker based on voice fingerprint.
        Uses "Sticky Speaker" logic to avoid immediately forgetting who is speaking
        when short "Unknown" segments appear.
        """
        new_speaker = msg.data
        if not new_speaker:
            return

        # Current time
        now = time.time()
        
        # Initialize timestamp for the last known speaker if not present
        if not hasattr(self, 'last_known_speaker_time'):
            self.last_known_speaker_time = 0
            
        # STICKY SPEAKER LOGIC:
        # 1. If it's a KNOWN speaker (not Unknown), update immediately
        if new_speaker != "Unknown":
            if new_speaker != self.current_speaker:
                self.get_logger().info(f'🗣️ Speaker changed: {self.current_speaker} -> {new_speaker}')
                self.current_speaker = new_speaker
            
            # Update timestamp of last positive identification
            self.last_known_speaker_time = now
            
        # 2. If UNKNOWN:
        else:
            # If we don't know anyone yet, keep Unknown
            if self.current_speaker == "Unknown":
                pass
                
            # If we know someone, check how much time has passed
            else:
                # If less than 60 seconds have passed since the last identification,
                # IGNORE "Unknown" and assume it's still the previous person.
                time_since_last = now - self.last_known_speaker_time
                if time_since_last < 60.0:
                    self.get_logger().debug(f'Ignoring "Unknown" - keeping {self.current_speaker} ({time_since_last:.1f}s)')
                else:
                    # Too much time has passed, reset to Unknown
                    self.get_logger().info(f'Speaker timeout - resetting to Unknown ({time_since_last:.1f}s elapsed)')
                    self.current_speaker = "Unknown"
    
    def _get_person_context_prompt(self) -> str:
        speaker = self.person_context.get('speaker', 'Unknown') or 'Unknown'
        if speaker == 'Unknown':
            return ''

        extras = [f'Current stored speaker profile label: {speaker}.']
        preferred_name = self.person_context.get('preferred_name', '')
        if preferred_name:
            extras.append(f'Preferred name: {preferred_name}.')
        preferred_language = self.person_context.get('preferred_language', '')
        if preferred_language:
            extras.append(f'Preferred language: {preferred_language}.')
        facts = self.person_context.get('facts', []) or []
        if facts:
            extras.append('Known personal facts: ' + '; '.join(str(fact) for fact in facts[:8]) + '.')
        return ' '.join(extras)

    def _process_streaming(self, user_text: str, user_lang: str):
        """Process the LLM response with streaming."""
        session_id = str(uuid.uuid4())[:8]
        
        try:
            # Add the user's message to history (with language instruction)
            # This forces the model to respond in the correct language
            lang_instruction = "[RESPOND IN ENGLISH]" if not user_lang.startswith('ro') else "[RĂSPUNDE ÎN ROMÂNĂ]"
            
            preferred_name = self.person_context.get('preferred_name', '').strip()
            display_speaker = preferred_name or self.current_speaker

            # Add speaker name if known
            if display_speaker and display_speaker != "Unknown":
                speaker_info = f"[Speaker: {display_speaker}] "
            else:
                speaker_info = ""
            
            user_message_with_lang = f"{lang_instruction} {speaker_info}{user_text}"
            
            self.conversation_history.append({
                'role': 'user',
                'content': user_message_with_lang
            })
            
            # Build messages for the API
            messages = [
                {
                    'role': 'system',
                    'content': ' '.join(filter(None, [
                        self._get_system_prompt_with_date(),
                        self._get_person_context_prompt(),
                    ])),
                }
            ] + self.conversation_history
            
            # Detect if the question needs web search
            needs_websearch = self._needs_websearch(user_text)
            
            # Select model and max_tokens based on web search
            if needs_websearch:
                model_to_use = self.websearch_model
                max_tokens_to_use = self.websearch_max_tokens
                self.get_logger().debug(f'🌐 Using web search model: {model_to_use}')
            else:
                model_to_use = self.model
                max_tokens_to_use = self.max_tokens
            
            # Buffer and state for stream shaper
            full_response = ""
            chunk_count = 0
            first_token_time = None
            start_time = time.time()
            backchannel_sent = False
            
            if self.provider == 'groq':
                stream = self.client.chat.completions.create(
                    model=model_to_use,
                    messages=messages,
                    max_tokens=max_tokens_to_use,
                    temperature=self.temperature,
                    stream=True
                )

                # Token generator with backchannel
                def token_generator():
                    nonlocal first_token_time, backchannel_sent
                    for chunk in stream:
                        if chunk.choices[0].delta.content:
                            token = chunk.choices[0].delta.content

                            # Backchannel: if first token is delayed > delay_ms
                            if first_token_time is None:
                                first_token_time = time.time()
                                ttft_ms = (first_token_time - start_time) * 1000
                                self.get_logger().debug(f'⏱️ Time to first token: {ttft_ms:.0f}ms')

                                # Send backchannel if it took too long
                                if self.backchannel_enabled and ttft_ms > self.backchannel_delay_ms and not backchannel_sent:
                                    backchannel_sent = True
                                    phrase = self.backchannel_phrase_ro if user_lang.startswith('ro') else self.backchannel_phrase_en
                                    self.get_logger().debug(
                                        f'⌛ Backchannel: TTFT > {self.backchannel_delay_ms}ms, sending "{phrase}"'
                                    )
                                    cmd = String()
                                    cmd.data = 'filler_ro' if user_lang.startswith('ro') else 'filler_en'
                                    self.tts_cmd_pub.publish(cmd)

                            yield token
            else:
                response_text = self._mistral_chat_complete(
                    messages=messages,
                    model=model_to_use,
                    max_tokens=max_tokens_to_use,
                )
                first_token_time = time.time()
                ttft_ms = (first_token_time - start_time) * 1000
                self.get_logger().debug(
                    f'⏱️ Mistral completion latency before first output token: {ttft_ms:.0f}ms'
                )

                def token_generator():
                    for token in self._text_to_token_stream(response_text):
                        yield token
            
            # Process tokens with stream shaper logic
            from .stream_shaper import shape_stream
            shaped_tokens = shape_stream(
                token_generator(),
                prebuffer_chars=self.prebuffer_chars,
                min_chunk_chars=self.min_chunk_chars,
                soft_max_chars=self.soft_max_chars,
                max_idle_ms=self.max_idle_ms
            )
            
            # Publish smoothed chunks
            for shaped_chunk in shaped_tokens:
                full_response += shaped_chunk
                self._publish_chunk(shaped_chunk.strip(), user_lang, False, session_id)
                chunk_count += 1
            
            # Send final marker
            self._publish_chunk("", user_lang, True, session_id)
            
            # Update history
            if full_response:
                self.conversation_history.append({
                    'role': 'assistant',
                    'content': full_response
                })
                self.last_assistant_response = full_response
                
                # Limit history
                if len(self.conversation_history) > 10:
                    self.conversation_history = self.conversation_history[-10:]
                
                self.get_logger().info(f'🤖 Bot ({chunk_count} chunks): {full_response}')
                
                # Also publish the full response for compatibility
                out = Transcription()
                out.text = full_response
                out.language = user_lang
                out.confidence = 1.0
                self.response_pub.publish(out)
            
        except Exception as e:
            self.get_logger().error(f'LLM streaming error: {e}')
            
            # Publish fallback response for error
            error_type = 'timeout' if 'timeout' in str(e).lower() else 'error'
            fallback_msg = self._get_fallback(error_type, user_lang)
            
            if fallback_msg:
                self.get_logger().debug(f'📢 Sending fallback response: {fallback_msg}')
                self._publish_chunk(fallback_msg, user_lang, True, session_id)
    
    def _publish_chunk(self, text: str, language: str, is_final: bool, session_id: str):
        """Publish a text chunk."""
        chunk = TextChunk()
        chunk.text = text
        chunk.language = language
        chunk.is_final = is_final
        chunk.session_id = session_id
        self.stream_pub.publish(chunk)
        
        if text:
            self.get_logger().debug(f'📤 Chunk: "{text[:30]}..." (final={is_final})')

    def _publish_immediate_response(self, text: str, language: str):
        session_id = str(uuid.uuid4())[:8]
        self._publish_chunk(text, language, False, session_id)
        self._publish_chunk('', language, True, session_id)

        out = Transcription()
        out.text = text
        out.language = language
        out.confidence = 1.0
        self.response_pub.publish(out)
    
    def clear_history(self):
        """Clear the conversation history."""
        self.conversation_history = []
        self.get_logger().debug('Conversation history cleared')


def main(args=None):
    rclpy.init(args=args)
    
    try:
        node = LLMNode()
        rclpy.spin(node)
    except RuntimeError as e:
        print(f'Failed to start LLM node: {e}')
    except KeyboardInterrupt:
        pass
    finally:
        try:
            rclpy.shutdown()
        except Exception:
            pass


if __name__ == '__main__':
    main()
