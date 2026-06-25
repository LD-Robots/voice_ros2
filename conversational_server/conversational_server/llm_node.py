#!/usr/bin/env python3
"""
LLM Node - Language Model processing using OpenAI reasoning + Brave Search.

FEATURES (synced with Conversational_Robot Python):
  - Web search via Brave Search for current questions
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
from datetime import datetime
from pathlib import Path
import requests
from conversational_client.robot_command_utils import looks_like_robot_command
from conversational_client.session_text_utils import detect_goodbye_keyword
from .openai_web_search import (
    DEFAULT_BRAVE_SEARCH_COUNT,
    DEFAULT_BRAVE_SEARCH_COUNTRY,
    DEFAULT_BRAVE_SEARCH_LANG,
    DEFAULT_BRAVE_LLM_CONTEXT_COUNT,
    DEFAULT_BRAVE_LLM_CONTEXT_TOKENS,
    build_brave_llm_context_tool_output,
    build_brave_web_search_tool_output,
    call_brave_llm_context,
    call_brave_web_search,
    extract_response_text,
    should_use_web_search,
)
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

# Regex to detect the end of a sentence
SENTENCE_END = re.compile(r'[.!?;:]\s*$')


class LLMNode(Node):
    def __init__(self):
        super().__init__('llm_node')
        
        # Configurable parameters
        self.declare_parameter('provider', 'openai')
        self.declare_parameter('model', 'gpt-5.5')
        self.declare_parameter('fast_model_enabled', True)
        self.declare_parameter('fast_model', 'gpt-5-mini')
        self.declare_parameter('fast_reasoning_effort', 'minimal')
        self.declare_parameter('max_tokens', 150)
        self.declare_parameter('temperature', 0.7)
        self.declare_parameter('reasoning_effort', 'low')
        self.declare_parameter('openai_api_key_env', 'OPENAI_API_KEY')
        self.declare_parameter('openai_timeout_s', 45.0)
        self.declare_parameter('openai_api_base', 'https://api.openai.com/v1')
        self.declare_parameter('min_chunk_chars', 40)  # Min chars per chunk
        self.declare_parameter('transcription_topic', '/attended_transcription')
        
        default_system_prompt = str(load_prompt_defaults().get('llm_system_prompt', ''))
        
        self.declare_parameter('system_prompt', default_system_prompt)
        
        # Web search parameters
        self.declare_parameter('websearch_enabled', True)
        self.declare_parameter('websearch_provider', 'brave')
        self.declare_parameter('websearch_max_tokens', 300)
        self.declare_parameter('search_router_enabled', True)
        self.declare_parameter('search_router_model', 'gpt-5-mini')
        self.declare_parameter('search_router_reasoning_effort', 'minimal')
        self.declare_parameter('search_router_timeout_s', 8.0)
        self.declare_parameter('brave_search_country', DEFAULT_BRAVE_SEARCH_COUNTRY)
        self.declare_parameter('brave_search_lang', DEFAULT_BRAVE_SEARCH_LANG)
        self.declare_parameter('brave_search_count', DEFAULT_BRAVE_SEARCH_COUNT)
        self.declare_parameter('brave_search_timeout_s', 12.0)
        self.declare_parameter('brave_llm_context_enabled', True)
        self.declare_parameter('brave_llm_context_count', DEFAULT_BRAVE_LLM_CONTEXT_COUNT)
        self.declare_parameter('brave_llm_context_max_tokens', DEFAULT_BRAVE_LLM_CONTEXT_TOKENS)
        
        self.provider = str(self.get_parameter('provider').value).lower()
        self.model = self.get_parameter('model').value
        self.fast_model_enabled = bool(self.get_parameter('fast_model_enabled').value)
        self.fast_model = str(self.get_parameter('fast_model').value)
        self.fast_reasoning_effort = str(
            self.get_parameter('fast_reasoning_effort').value or ''
        ).strip()
        self.max_tokens = self.get_parameter('max_tokens').value
        self.temperature = self.get_parameter('temperature').value
        self.reasoning_effort = str(self.get_parameter('reasoning_effort').value or '').strip()
        self.openai_api_key_env = str(self.get_parameter('openai_api_key_env').value)
        self.openai_timeout_s = float(self.get_parameter('openai_timeout_s').value)
        self.min_chunk_chars = self.get_parameter('min_chunk_chars').value
        self.system_prompt = self.get_parameter('system_prompt').value
        self.transcription_topic = str(self.get_parameter('transcription_topic').value)

        self.openai_api_base = str(self.get_parameter('openai_api_base').value or 'https://api.openai.com/v1').strip()
        if self.provider == 'ollama' and self.openai_api_base == 'https://api.openai.com/v1':
            self.openai_api_base = 'http://localhost:11434/v1'

        if self.provider not in ('openai', 'ollama'):
            raise RuntimeError(
                f"Unsupported llm provider '{self.provider}'. Only 'openai' and 'ollama' are implemented."
            )
        
        # Web search
        self.websearch_enabled = self.get_parameter('websearch_enabled').value
        self.websearch_provider = str(self.get_parameter('websearch_provider').value).lower()
        self.websearch_max_tokens = self.get_parameter('websearch_max_tokens').value
        self.search_router_enabled = bool(
            self.get_parameter('search_router_enabled').value
        )
        self.search_router_model = str(self.get_parameter('search_router_model').value)
        self.search_router_reasoning_effort = str(
            self.get_parameter('search_router_reasoning_effort').value or ''
        ).strip()
        self.search_router_timeout_s = float(
            self.get_parameter('search_router_timeout_s').value
        )
        self.brave_search_country = str(self.get_parameter('brave_search_country').value)
        self.brave_search_lang = str(self.get_parameter('brave_search_lang').value)
        self.brave_search_count = int(self.get_parameter('brave_search_count').value)
        self.brave_search_timeout_s = float(self.get_parameter('brave_search_timeout_s').value)
        self.brave_llm_context_enabled = bool(
            self.get_parameter('brave_llm_context_enabled').value
        )
        self.brave_llm_context_count = int(
            self.get_parameter('brave_llm_context_count').value
        )
        self.brave_llm_context_max_tokens = int(
            self.get_parameter('brave_llm_context_max_tokens').value
        )
        
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
        
        # Check API keys
        self.api_key = os.environ.get(self.openai_api_key_env)
        if not self.api_key:
            if self.provider == 'ollama':
                self.api_key = 'ollama'
            else:
                self.get_logger().error(f'{self.openai_api_key_env} environment variable not set!')
                raise RuntimeError(f'{self.openai_api_key_env} not set')
        self.brave_search_api_key = os.environ.get('BRAVE_SEARCH_API_KEY', '')
        if self.websearch_enabled and self.websearch_provider == 'brave' and not self.brave_search_api_key:
            self.get_logger().warn(
                'BRAVE_SEARCH_API_KEY not set; online search decisions will be logged but skipped.'
            )

        self.get_logger().debug(f'✅ OpenAI reasoning initialized with model: {self.model}')
        
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
        self.latest_diarization = {}
        self.stop_epoch = 0
        
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
        self.diarization_sub = self.create_subscription(
            String,
            '/elevenlabs_diarization',
            self._diarization_callback,
            10,
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
        self.stop_sub = self.create_subscription(
            Bool,
            '/stop_playback',
            self._stop_playback_callback,
            10,
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
        self.web_search_status_pub = self.create_publisher(
            String,
            '/web_search_status',
            10,
        )
        
        self.get_logger().debug(
            f'LLM Node started with OpenAI reasoning + Brave Search! '
            f'websearch={self.websearch_enabled}'
        )
        
        # Warm up Ollama model
        self._warmup_ollama()
    
    def _warmup_ollama(self):
        """Asynchronously warm up the Ollama model on startup to prevent first-call latency."""
        if self.provider != 'ollama':
            return
        
        def run_warmup():
            self.get_logger().info("🔥 Warming up Ollama model to prevent first-call latency...")
            try:
                url = f"{self.openai_api_base}/chat/completions"
                body = {
                    "model": self.model,
                    "messages": [{"role": "user", "content": "hello"}],
                    "max_tokens": 1
                }
                response = requests.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json=body,
                    timeout=15.0
                )
                if response.ok:
                    self.get_logger().info("✅ Ollama model warmed up and loaded in memory!")
                else:
                    self.get_logger().warn(f"⚠️ Ollama warmup request returned status {response.status_code}")
            except Exception as e:
                self.get_logger().warn(f"⚠️ Ollama warmup failed: {e}")

        import threading
        threading.Thread(target=run_warmup, daemon=True).start()

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

        needs_search, reason = should_use_web_search(text)
        if needs_search:
            self.get_logger().debug(f'🔍 Web search recommended: {reason}')
        return needs_search

    def _websearch_decision(self, text: str) -> tuple[bool, str, str]:
        if not self.websearch_enabled:
            return False, 'web search disabled', text

        rule_search, rule_reason = should_use_web_search(text)
        if rule_search:
            if self.search_router_enabled:
                routed = self._route_web_search_with_model(
                    text,
                    force_search=True,
                    force_reason=rule_reason,
                )
                if routed is not None:
                    return True, routed[1] or rule_reason, routed[2] or text
            return True, rule_reason or 'rule matched current-information need', text
        if rule_reason == 'social conversation does not need web search':
            return False, rule_reason, text

        local_reason = self._local_no_search_reason(text)
        if local_reason:
            return False, local_reason, text

        if self.search_router_enabled:
            routed = self._route_web_search_with_model(text)
            if routed is not None:
                return routed

        return False, rule_reason or 'no current-information signal', text

    @staticmethod
    def _local_no_search_reason(text: str) -> str:
        normalized = str(text or '').strip().lower()
        if not normalized:
            return ''
        no_search_patterns = (
            (r'\b(tell me|say|give me)\b.*\b(joke|story|poem|riddle)\b', 'creative request does not need web search'),
            (r'\b(make it|say it|explain it|tell it)\b.*\b(shorter|simpler|funnier|clearer|for a child|like a child)\b', 'style rewrite of existing context does not need web search'),
            (r'\b(that\'s good|that is good|good one|i like it|i don\'t like it|not like that|try again)\b', 'conversation feedback does not need web search'),
            (r'\b(thank you|thanks|ok|okay|understood|got it|yes|no)\b[.! ]*$', 'short acknowledgement does not need web search'),
            (r'\b(repeat that|say that again|continue|go on)\b', 'conversation control does not need web search'),
            (r'\b(explain|what is|what are|how does|why does)\b.*\b(work|mean|concept|idea|neural network|bitcoin|tesla)\b', 'stable explanation does not need web search'),
        )
        for pattern, reason in no_search_patterns:
            if re.search(pattern, normalized):
                return reason
        return ''

    def _route_web_search_with_model(
        self,
        text: str,
        *,
        force_search: bool = False,
        force_reason: str = '',
    ) -> tuple[bool, str, str] | None:
        recent_messages = self.conversation_history[-8:]
        router_input = {
            'current_user_text': text,
            'recent_conversation': recent_messages,
            'current_date': datetime.now().strftime('%Y-%m-%d'),
            'force_search': force_search,
            'force_reason': force_reason,
            'local_region_hint': 'Romania / Europe',
        }
        instructions = (
            'You are a search-routing classifier for a voice robot. Decide if the current user turn '
            'needs fresh online information. Use search for news, politics, wars, weather, sports, '
            'prices, laws, products, elections, public/company leaders, recent events, and follow-up '
            'questions that inherit a current-news topic from the recent conversation. Do not search '
            'for greetings, emotional support, stable explanations, math, personal memory, or robot '
            'movement commands. Return only compact JSON with keys: search (boolean), reason (string), '
            'query (string). If search is false, query should be empty. If search is true, rewrite a '
            'short web query in English or the user language. Resolve pronouns like "that team", '
            '"this week", "current table", or "point results" using recent conversation. Include the '
            'main entity, league/country, season/year, and today/current/latest terms when useful.\n\n'
            'Examples:\n'
            'User: "How are you today?" -> {"search":false,"reason":"social greeting","query":""}\n'
            'User: "Tell me a story" -> {"search":false,"reason":"creative request","query":""}\n'
            'User: "Do you know the recent news from Romania?" -> {"search":true,"reason":"recent news request","query":"recent Romania political news"}\n'
            'Previous topic: recent news from Romania. User: "But about Ukraine war" -> {"search":true,"reason":"current-event follow-up about war","query":"latest Ukraine war news Romania impact"}\n'
            'User: "Who is the CEO of OpenAI?" -> {"search":true,"reason":"company leadership can change","query":"current CEO of OpenAI"}\n'
            'User: "Explain what NATO is" -> {"search":false,"reason":"stable explanation","query":""}\n'
            'User: "What did NATO say this week?" -> {"search":true,"reason":"time-sensitive NATO news","query":"NATO statements this week"}\n'
            'Previous topic: Romanian SuperLiga standings 2025/2026. User: "give me the latest point results" -> {"search":true,"reason":"current sports standings follow-up","query":"Romanian SuperLiga 2025-2026 current standings table points today"}\n'
            'Previous topic: Romanian SuperLiga champion. User: "how many points has that team?" -> {"search":true,"reason":"current sports points follow-up","query":"Romanian SuperLiga 2025-2026 champion current points standings"}'
        )
        if force_search:
            instructions += (
                '\n\nA deterministic rule has already decided that search is required. '
                'Return search=true and focus on rewriting the best possible query.'
            )
        is_reasoning_model = str(self.search_router_model).startswith(('gpt-5', 'o'))
        if is_reasoning_model:
            url = f'{self.openai_api_base}/responses'
            body = {
                'model': self.search_router_model,
                'instructions': instructions,
                'input': [
                    {
                        'role': 'user',
                        'content': json.dumps(router_input, ensure_ascii=False),
                    }
                ],
                'max_output_tokens': 160,
                'store': False,
                'text': {
                    'format': {
                        'type': 'json_schema',
                        'name': 'search_router_decision',
                        'strict': True,
                        'schema': {
                            'type': 'object',
                            'properties': {
                                'search': {'type': 'boolean'},
                                'reason': {'type': 'string'},
                                'query': {'type': 'string'},
                            },
                            'required': ['search', 'reason', 'query'],
                            'additionalProperties': False,
                        },
                    },
                },
            }
            if self.search_router_reasoning_effort:
                body['reasoning'] = {'effort': self.search_router_reasoning_effort}
        else:
            url = f'{self.openai_api_base}/chat/completions'
            body = {
                'model': self.search_router_model,
                'messages': [
                    {
                        'role': 'system',
                        'content': instructions,
                    },
                    {
                        'role': 'user',
                        'content': json.dumps(router_input, ensure_ascii=False),
                    }
                ],
                'max_tokens': 160,
                'response_format': {
                    'type': 'json_schema',
                    'json_schema': {
                        'name': 'search_router_decision',
                        'strict': True,
                        'schema': {
                            'type': 'object',
                            'properties': {
                                'search': {'type': 'boolean'},
                                'reason': {'type': 'string'},
                                'query': {'type': 'string'},
                            },
                            'required': ['search', 'reason', 'query'],
                            'additionalProperties': False,
                        },
                    },
                },
            }

        try:
            response = requests.post(
                url,
                headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json',
                },
                json=body,
                timeout=max(1.0, self.search_router_timeout_s),
            )
            if not response.ok:
                raise RuntimeError(f'HTTP {response.status_code}: {response.text[:200]}')
            raw_text = extract_response_text(response.json()).strip()
            payload = self._parse_router_json(raw_text)
            should_search = bool(payload.get('search', False))
            if force_search:
                should_search = True
            reason = str(payload.get('reason', '') or '').strip()
            query = str(payload.get('query', '') or '').strip()
            should_search, reason, query = self._sanitize_search_router_decision(
                should_search,
                reason,
                query,
                text,
                force_search=force_search,
            )
            if should_search and not query:
                query = text
            if not reason:
                reason = 'search router decision' if should_search else 'search router skipped'
            self.get_logger().info(
                f'🧭 Search router: search={should_search}, reason={reason}, query="{query}"'
            )
            return should_search, reason, query
        except Exception as exc:
            self.get_logger().warn(f'Search router failed; falling back to rules: {exc}')
            return None

    @staticmethod
    def _sanitize_search_router_decision(
        should_search: bool,
        reason: str,
        query: str,
        original_text: str,
        *,
        force_search: bool = False,
    ) -> tuple[bool, str, str]:
        reason_text = str(reason or '').strip()
        query_text = str(query or '').strip()
        original = str(original_text or '').strip()
        reason_norm = reason_text.lower()

        no_search_reason_markers = (
            'no need for fresh',
            'does not need web search',
            'do not search',
            'not need web search',
            'no request for fresh',
            'no current-information',
            'not time-sensitive',
            'stable explanation',
            'social conversation',
            'conversation feedback',
        )
        if should_search and not force_search and any(marker in reason_norm for marker in no_search_reason_markers):
            return False, reason_text or 'router reason indicates no web search', ''

        if not should_search:
            return False, reason_text, ''

        if not query_text:
            query_text = original
        words = query_text.split()
        if len(words) > 45:
            query_text = ' '.join(words[:45])
            reason_text = (reason_text + '; query truncated for search API').strip('; ')
        return True, reason_text, query_text

    @staticmethod
    def _parse_router_json(raw_text: str) -> dict:
        text = (raw_text or '').strip()
        if text.startswith('```'):
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)
        return json.loads(text)
    
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

    def _diarization_callback(self, msg: String):
        try:
            payload = json.loads(msg.data)
        except Exception:
            return
        self.latest_diarization = payload if isinstance(payload, dict) else {}

    def _backend_callback(self, msg: String):
        backend = msg.data.strip() or 'legacy'
        self.current_backend = backend

    def _control_callback(self, msg: String):
        if self.current_backend != 'legacy':
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

    def _stop_playback_callback(self, msg: Bool):
        if msg.data and self.current_backend == 'legacy':
            self.stop_epoch += 1
            self.get_logger().debug(f'⏹️ LLM output cancelled (epoch now {self.stop_epoch})')

    def transcription_callback(self, msg: Transcription):
        """Process transcription and publish the LLM response in streaming."""
        if self.current_backend != 'legacy':
            return

        user_text = msg.text.strip()
        user_lang = msg.language
        
        if not user_text:
            self.get_logger().warn('Empty transcription received, skipping')
            return
            
        if self._is_robot_command(user_text):
            self.get_logger().info(f'🔇 Ignored robot command: {user_text}')
            return

        if detect_goodbye_keyword(user_text):
            self.get_logger().info(f'🔇 Ignored goodbye command to prevent LLM chatter: {user_text}')
            return

        # If we are waiting for a safety confirmation (e.g. they said "yes" or "no")
        # we want the LLM to ignore it completely so it doesn't chat.
        if self.waiting_for_robot_confirmation:
            self.get_logger().info(f'🔇 Ignored text during robot confirmation: {user_text}')
            # We don't reset the flag here, we let the status topic do it
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
        extras = []
        if speaker != 'Unknown':
            extras.append(f'Current stored speaker profile label: {speaker}.')
        preferred_name = self.person_context.get('preferred_name', '')
        if preferred_name:
            extras.append(f'Preferred name: {preferred_name}.')
        preferred_language = self.person_context.get('preferred_language', '')
        if preferred_language:
            extras.append(f'Preferred language: {preferred_language}.')
        facts = self.person_context.get('facts', []) or []
        if facts:
            extras.append('Known personal facts: ' + '; '.join(str(fact) for fact in facts[:8]) + '.')
        diarization_prompt = self._get_diarization_prompt()
        if diarization_prompt:
            extras.append(diarization_prompt)
        return ' '.join(extras)

    def _get_diarization_prompt(self) -> str:
        payload = self.latest_diarization if isinstance(self.latest_diarization, dict) else {}
        counts = payload.get('speaker_word_counts', {}) or {}
        if not counts:
            return ''
        dominant = str(payload.get('dominant_speaker_id', '') or 'unknown')
        speakers = ', '.join(
            f'{speaker}:{count}'
            for speaker, count in sorted(counts.items())
        )
        if len(counts) <= 1:
            return (
                f'Latest ElevenLabs STT diarization detected one anonymous speaker '
                f'({dominant}).'
            )
        return (
            'Latest ElevenLabs STT diarization detected multiple anonymous speakers '
            f'({speakers}); dominant speaker is {dominant}. '
            'Treat these as turn-level diarization labels, not persistent person identities. '
            'Use the enrolled speaker profile, if available, for memory and names.'
        )

    def _process_streaming(self, user_text: str, user_lang: str):
        """Process the LLM response with streaming."""
        session_id = str(uuid.uuid4())[:8]
        start_epoch = self.stop_epoch
        
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
            
            lang_system_instruction = (
                "CRITICAL: The user is speaking in English. You MUST respond entirely in English."
                if not user_lang.startswith('ro') else
                "CRITIC: Utilizatorul vorbește în română. Trebuie să răspunzi obligatoriu în limba română."
            )
            
            system_instructions = ' '.join(filter(None, [
                self._get_system_prompt_with_date(),
                self._get_person_context_prompt(),
                (
                    'When fresh web results are included in the user input, answer from those results. '
                    'Do not claim you searched online unless web results were provided. '
                    'For stable knowledge, answer directly without web search.'
                ),
                lang_system_instruction,
            ]))
            
            # Detect if the question needs web search
            needs_websearch, websearch_reason, websearch_query = self._websearch_decision(user_text)
            web_context = ''
            if needs_websearch:
                web_context = self._run_brave_web_search(websearch_query, websearch_reason)
            else:
                self._publish_web_search_status(False, websearch_reason, websearch_query)
                self.get_logger().info(f'🌐 Web search skipped: {websearch_reason}')

            messages = list(self.conversation_history)
            if web_context:
                messages[-1] = {
                    'role': 'user',
                    'content': (
                        f'{user_message_with_lang}\n\n'
                        'Fresh Brave Search results for this turn:\n'
                        f'{web_context}'
                    ),
                }
            max_tokens_to_use = self.websearch_max_tokens if web_context else self.max_tokens
            model_to_use = self.model
            reasoning_to_use = self.reasoning_effort
            if not web_context and self.fast_model_enabled and self.fast_model:
                model_to_use = self.fast_model
                reasoning_to_use = self.fast_reasoning_effort
            
            # OpenAI Responses API call.
            start_time = time.time()
            token_stream = self._call_openai_response(
                instructions=system_instructions,
                messages=messages,
                max_tokens=max_tokens_to_use,
                model=model_to_use,
                reasoning_effort=reasoning_to_use,
                stream=True,
            )
            
            # Process tokens with stream shaper logic
            from .stream_shaper import shape_stream
            shaped_tokens = shape_stream(
                token_stream,
                prebuffer_chars=self.prebuffer_chars,
                min_chunk_chars=self.min_chunk_chars,
                soft_max_chars=self.soft_max_chars,
                max_idle_ms=self.max_idle_ms
            )
            
            # Publish smoothed chunks
            published_response = ""
            chunk_count = 0
            first_token_time = None
            for shaped_chunk in shaped_tokens:
                if first_token_time is None:
                    first_token_time = time.time()
                    ttft_ms = (first_token_time - start_time) * 1000
                    self.get_logger().debug(f'⏱️ OpenAI first chunk time: {ttft_ms:.0f}ms')
                    
                    if self.backchannel_enabled and ttft_ms > self.backchannel_delay_ms:
                        cmd = String()
                        cmd.data = 'filler_ro' if user_lang.startswith('ro') else 'filler_en'
                        self.tts_cmd_pub.publish(cmd)

                if start_epoch != self.stop_epoch:
                    self.get_logger().info('⏹️ LLM stream stopped because the user interrupted')
                    return
                if published_response and not published_response.endswith((" ", "\n")) and not shaped_chunk.startswith(" "):
                    published_response += " "
                published_response += shaped_chunk
                self._publish_chunk(shaped_chunk.strip(), user_lang, False, session_id)
                chunk_count += 1
            
            # Send final marker
            self._publish_chunk("", user_lang, True, session_id)
            
            # Update history
            if published_response:
                self.conversation_history.append({
                    'role': 'assistant',
                    'content': published_response
                })
                self.last_assistant_response = published_response
                
                # Limit history
                if len(self.conversation_history) > 10:
                    self.conversation_history = self.conversation_history[-10:]
                
                self.get_logger().info(f'🤖 Bot ({chunk_count} chunks): {published_response}')
                
                # Also publish the full response for compatibility
                out = Transcription()
                out.text = published_response
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

    def _call_openai_response(
        self,
        *,
        instructions: str,
        messages: list[dict],
        max_tokens: int,
        model: str | None = None,
        reasoning_effort: str | None = None,
        stream: bool = False,
    ):
        model = model or self.model
        reasoning_effort = self.reasoning_effort if reasoning_effort is None else reasoning_effort
        
        formatted_messages = []
        if instructions:
            formatted_messages.append({
                'role': 'system',
                'content': instructions,
            })
        formatted_messages.extend(messages)

        body = {
            'model': model,
            'messages': formatted_messages,
        }
        if str(model).startswith(('gpt-5', 'o')):
            body['max_completion_tokens'] = int(max_tokens)
            if reasoning_effort:
                body['reasoning_effort'] = reasoning_effort
        else:
            body['max_tokens'] = int(max_tokens)
            body['temperature'] = float(self.temperature)

        if stream:
            body['stream'] = True

        response = requests.post(
            f'{self.openai_api_base}/chat/completions',
            headers={
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json',
            },
            json=body,
            timeout=max(1.0, self.openai_timeout_s),
            stream=stream,
        )
        if not response.ok:
            try:
                payload = response.json()
            except ValueError:
                payload = response.text.strip()
            raise RuntimeError(f'OpenAI Chat Completions HTTP {response.status_code}: {payload}')

        if stream:
            def token_generator():
                for line in response.iter_lines():
                    if not line:
                        continue
                    line_str = line.decode('utf-8').strip()
                    if line_str.startswith('data: '):
                        data_content = line_str[6:]
                        if data_content == '[DONE]':
                            break
                        try:
                            chunk_data = json.loads(data_content)
                            delta = chunk_data.get('choices', [{}])[0].get('delta', {})
                            if 'content' in delta:
                                yield delta['content']
                        except Exception:
                            pass
            return token_generator()

        payload = response.json()
        text = extract_response_text(payload).strip()
        if not text:
            raise RuntimeError(f'OpenAI response did not contain output text. Payload: {payload}')
        return text

    def _run_brave_web_search(self, query: str, reason: str) -> str:
        self._publish_web_search_status(True, reason, query)
        self.get_logger().info(f'🌐 Brave Search triggered: {reason}; query="{query}"')
        if not self.brave_search_api_key:
            self.get_logger().warn('Brave Search skipped because BRAVE_SEARCH_API_KEY is missing')
            return ''
        if self.brave_llm_context_enabled:
            try:
                payload = call_brave_llm_context(
                    self.brave_search_api_key,
                    query,
                    country=self.brave_search_country,
                    search_lang=self.brave_search_lang,
                    count=self.brave_llm_context_count,
                    max_tokens=self.brave_llm_context_max_tokens,
                    timeout_s=self.brave_search_timeout_s,
                )
                output = build_brave_llm_context_tool_output(query, payload=payload)
                if self._tool_output_ok(output):
                    self.get_logger().info('🌐 Brave LLM Context returned grounding')
                    return output
                self.get_logger().warn('Brave LLM Context returned no grounding; falling back to web snippets')
            except Exception as exc:
                self.get_logger().warn(f'Brave LLM Context failed; falling back to web snippets: {exc}')
        try:
            payload = call_brave_web_search(
                self.brave_search_api_key,
                query,
                country=self.brave_search_country,
                search_lang=self.brave_search_lang,
                count=self.brave_search_count,
                timeout_s=self.brave_search_timeout_s,
            )
            return build_brave_web_search_tool_output(query, payload=payload)
        except Exception as exc:
            self.get_logger().error(f'Brave Search failed: {exc}')
            return build_brave_web_search_tool_output(query, error=str(exc))

    @staticmethod
    def _tool_output_ok(output: str) -> bool:
        try:
            payload = json.loads(output)
        except Exception:
            return False
        return bool(payload.get('ok') and str(payload.get('summary', '')).strip())

    def _publish_web_search_status(self, used: bool, reason: str, query: str):
        msg = String()
        msg.data = json.dumps({
            'used': bool(used),
            'provider': 'brave' if used else '',
            'reason': reason,
            'query': query,
        }, ensure_ascii=False, separators=(',', ':'))
        self.web_search_status_pub.publish(msg)

    @staticmethod
    def _word_token_generator(text: str):
        for part in re.split(r'(\s+)', text or ''):
            if part:
                yield part
    
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
