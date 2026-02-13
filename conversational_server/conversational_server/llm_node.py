#!/usr/bin/env python3
"""
LLM Node - Language Model processing using Groq API with STREAMING.

FEATURES (sincronizat cu Conversational_Robot Python):
  - Web search via Groq Compound model pentru întrebări actuale
  - Keyword detection pentru știri, vreme, prețuri, alegeri, etc.

Subscribes to: /transcription (Transcription)
Publishes to: 
  - /llm_stream (TextChunk) - streaming chunks
  - /llm_response (Transcription) - complete response (for compatibility)
"""
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Transcription, TextChunk
from std_msgs.msg import String
import os
import re
import uuid
import threading
import time
from datetime import datetime
from pathlib import Path

# Încarcă variabilele din .env
try:
    from dotenv import load_dotenv
    # Caută fișierul .env în voice_ros2/ (funcționează din install/ sau src/)
    current_path = Path(__file__).resolve()
    # Mergi în sus până găsești directorul care conține conversational_server
    while current_path.name != 'voice_ros2' and current_path != current_path.parent:
        current_path = current_path.parent
    
    # Dacă nu găsim voice_ros2, încercăm să mergem 3 niveluri în sus de la fișier
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

# Groq pentru LLM
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    print("⚠️ groq not installed. Run: pip install groq")

# Regex pentru a detecta sfârșitul unei propoziții
SENTENCE_END = re.compile(r'[.!?;:]\s*$')


class LLMNode(Node):
    def __init__(self):
        super().__init__('llm_node')
        
        # Parametri configurabili
        self.declare_parameter('model', 'llama-3.1-8b-instant')
        self.declare_parameter('max_tokens', 150)
        self.declare_parameter('temperature', 0.7)
        self.declare_parameter('min_chunk_chars', 40)  # Min caractere per chunk
        
        # System prompt - can be overridden via parameter
        # Default: Enhanced prompt with personality, language rules, teasing, opinions
        default_system_prompt = '''You are Robot, a young, friendly, bilingual (RO/EN) buddy. Not a servant - a friend with personality and self-respect.

LANGUAGE (CRITICAL): Always respond in the SAME language as user's latest message. English→100% English. Romanian→100% Romanian. Never mix. No RO slang in EN, no EN slang in RO.

STYLE: 1-2 sentences max. No markdown. Be warm, casual, genuine. Don't announce actions ("Let me try..."), just do them. Don't end with questions constantly. "Mișto!" or "That's cool" is enough sometimes.

FRIENDSHIP: Show interest, remember context, celebrate wins, be supportive. Don't offer alternatives or ask "Want me to...?" - just continue naturally.

TEASING: If criticized, push back playfully (EN: "Dude, chill!", "That's mean!" / RO: "Alo, nu fi rău!", "Hei, calm!"). Be sassy: "Hai tu cu una mai bună!" / "Fine, you try!" Don't apologize or over-explain.

OPINIONS: Neutral on ethics/politics/religion. BUT for fun topics (food, movies, music, colors) - MUST have preferences! Never say "I'm a robot without preferences." Examples: "Pizza Margherita!", "I love action movies!"

HONESTY: Answer first, admit uncertainty casually ("Nu sunt sigur, dar..." / "Don't quote me on that"). For unknowable questions, react briefly in THE SAME LANGUAGE and STOP. EN: "Dude, nobody knows!" / RO: "Habar n-am, nimeni nu știe!" No estimates unless they insist.

EMOTIONS: Match their energy. If down→supportive. If excited→enthusiastic. If confused→simpler.'''
        
        self.declare_parameter('system_prompt', default_system_prompt)
        
        # Web search parameters
        self.declare_parameter('websearch_enabled', True)
        self.declare_parameter('websearch_model', 'compound-beta')  # Groq compound model
        self.declare_parameter('websearch_max_tokens', 300)
        
        self.model = self.get_parameter('model').value
        self.max_tokens = self.get_parameter('max_tokens').value
        self.temperature = self.get_parameter('temperature').value
        self.min_chunk_chars = self.get_parameter('min_chunk_chars').value
        self.system_prompt = self.get_parameter('system_prompt').value
        
        # Web search
        self.websearch_enabled = self.get_parameter('websearch_enabled').value
        self.websearch_model = self.get_parameter('websearch_model').value
        self.websearch_max_tokens = self.get_parameter('websearch_max_tokens').value
        
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
        
        # Verifică API key
        self.api_key = os.environ.get('GROQ_API_KEY')
        if not self.api_key:
            self.get_logger().error('GROQ_API_KEY environment variable not set!')
            raise RuntimeError('GROQ_API_KEY not set')
        
        if not GROQ_AVAILABLE:
            self.get_logger().error('groq package not installed!')
            raise RuntimeError('groq not available')
        
        # Inițializează client Groq
        self.client = Groq(api_key=self.api_key)
        self.get_logger().debug(f'✅ Groq client initialized with model: {self.model}')
        
        # Istoricul conversației
        self.conversation_history = []
        
        # Speaker identification — cine vorbește acum
        self.current_speaker = "Unknown"
        self.speaker_sub = self.create_subscription(
            String,
            '/speaker_id',
            self._speaker_id_callback,
            10
        )
        
        # Subscriber pentru transcriere
        self.transcription_sub = self.create_subscription(
            Transcription,
            '/transcription',
            self.transcription_callback,
            10
        )
        
        # Publisher pentru streaming chunks
        self.stream_pub = self.create_publisher(
            TextChunk,
            '/llm_stream',
            10
        )
        
        # Publisher pentru răspunsul complet (compatibilitate)
        self.response_pub = self.create_publisher(
            Transcription,
            '/llm_response',
            10
        )
        
        # Publisher pentru TTS command (backchannel)
        self.tts_cmd_pub = self.create_publisher(
            String,
            '/tts_command',
            10
        )
        
        self.get_logger().debug(f'LLM Node started with STREAMING + BACKCHANNEL! websearch={self.websearch_enabled}')
    
    def _get_system_prompt_with_date(self) -> str:
        """Returnează system prompt cu data curentă injectată."""
        date_str = datetime.now().strftime("%A, %B %d, %Y")
        return f"Today is {date_str}.\n\n{self.system_prompt}"
    
    def _get_fallback(self, key: str, lang: str) -> str:
        """Returnează mesajul de fallback pentru cheie și limbă."""
        suffix = '_ro' if str(lang).lower().startswith('ro') else '_en'
        return self.fallback.get(f"{key}{suffix}", "")
    
    def _needs_websearch(self, text: str) -> bool:
        """Detectează dacă întrebarea necesită informații actuale de pe web."""
        if not self.websearch_enabled:
            return False
        
        text_lower = text.lower()
        
        # Cuvinte cheie care indică nevoie de info actuală
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
    
    def transcription_callback(self, msg: Transcription):
        """Procesează transcrierea și publică răspunsul LLM în streaming."""
        user_text = msg.text.strip()
        user_lang = msg.language
        
        if not user_text:
            self.get_logger().warn('Empty transcription received, skipping')
            return
        
        self.get_logger().info(f'💬 User [{user_lang}]: {user_text}')
        
        # Procesează în thread separat pentru a nu bloca ROS2
        thread = threading.Thread(
            target=self._process_streaming,
            args=(user_text, user_lang),
            daemon=True
        )
        thread.start()
    
    def _speaker_id_callback(self, msg: String):
        """Actualizează vorbitorul curent pe baza amprentei vocale."""
        if msg.data and msg.data != self.current_speaker:
            self.current_speaker = msg.data
            self.get_logger().info(f'🗣️ Speaker activ: {self.current_speaker}')
    
    def _process_streaming(self, user_text: str, user_lang: str):
        """Procesează răspunsul LLM cu streaming."""
        session_id = str(uuid.uuid4())[:8]
        
        try:
            # Adaugă mesajul utilizatorului în istoric (cu instrucțiune de limbă)
            # Aceasta forțează modelul să răspundă în limba corectă
            lang_instruction = "[RESPOND IN ENGLISH]" if not user_lang.startswith('ro') else "[RĂSPUNDE ÎN ROMÂNĂ]"
            
            # Adaugă numele vorbitorului dacă e cunoscut
            if self.current_speaker and self.current_speaker != "Unknown":
                speaker_info = f"[Speaker: {self.current_speaker}] "
            else:
                speaker_info = ""
            
            user_message_with_lang = f"{lang_instruction} {speaker_info}{user_text}"
            
            self.conversation_history.append({
                'role': 'user',
                'content': user_message_with_lang
            })
            
            # Construiește mesajele pentru API
            messages = [
                {'role': 'system', 'content': self._get_system_prompt_with_date()}
            ] + self.conversation_history
            
            # Detectează dacă întrebarea necesită web search
            needs_websearch = self._needs_websearch(user_text)
            
            # Selectează model și max_tokens în funcție de web search
            if needs_websearch:
                model_to_use = self.websearch_model
                max_tokens_to_use = self.websearch_max_tokens
                self.get_logger().debug(f'🌐 Using web search model: {model_to_use}')
            else:
                model_to_use = self.model
                max_tokens_to_use = self.max_tokens
            
            # Apel Groq API cu STREAMING
            stream = self.client.chat.completions.create(
                model=model_to_use,
                messages=messages,
                max_tokens=max_tokens_to_use,
                temperature=self.temperature,
                stream=True  # STREAMING!
            )
            
            # Buffer și state pentru stream shaper
            buffer = ""
            full_response = ""
            chunk_count = 0
            first_token_time = None
            start_time = time.time()
            backchannel_sent = False
            
            # Generator pentru tokeni cu backchannel
            def token_generator():
                nonlocal first_token_time, backchannel_sent
                for chunk in stream:
                    if chunk.choices[0].delta.content:
                        token = chunk.choices[0].delta.content
                        
                        # Backchannel: dacă prima token întârzie > delay_ms
                        if first_token_time is None:
                            first_token_time = time.time()
                            ttft_ms = (first_token_time - start_time) * 1000
                            self.get_logger().debug(f'⏱️ Time to first token: {ttft_ms:.0f}ms')
                            
                            # Trimite backchannel dacă a durat prea mult
                            if self.backchannel_enabled and ttft_ms > self.backchannel_delay_ms and not backchannel_sent:
                                backchannel_sent = True
                                phrase = self.backchannel_phrase_ro if user_lang.startswith('ro') else self.backchannel_phrase_en
                                self.get_logger().debug(f'⌛ Backchannel: TTFT > {self.backchannel_delay_ms}ms, sending "{phrase}"')
                                cmd = String()
                                cmd.data = 'filler_ro' if user_lang.startswith('ro') else 'filler_en'
                                self.tts_cmd_pub.publish(cmd)
                        
                        yield token
            
            # Procesează tokenii cu stream shaper logic
            from .stream_shaper import shape_stream
            shaped_tokens = shape_stream(
                token_generator(),
                prebuffer_chars=self.prebuffer_chars,
                min_chunk_chars=self.min_chunk_chars,
                soft_max_chars=self.soft_max_chars,
                max_idle_ms=self.max_idle_ms
            )
            
            # Publică chunk-urile netezite
            for shaped_chunk in shaped_tokens:
                full_response += shaped_chunk
                self._publish_chunk(shaped_chunk.strip(), user_lang, False, session_id)
                chunk_count += 1
            
            # Trimite final marker
            self._publish_chunk("", user_lang, True, session_id)
            
            # Actualizează istoricul
            if full_response:
                self.conversation_history.append({
                    'role': 'assistant',
                    'content': full_response
                })
                
                # Limitează istoricul
                if len(self.conversation_history) > 10:
                    self.conversation_history = self.conversation_history[-10:]
                
                self.get_logger().info(f'🤖 Bot ({chunk_count} chunks): {full_response}')
                
                # Publică și răspunsul complet pentru compatibilitate
                out = Transcription()
                out.text = full_response
                out.language = user_lang
                out.confidence = 1.0
                self.response_pub.publish(out)
            
        except Exception as e:
            self.get_logger().error(f'LLM streaming error: {e}')
            
            # Publică fallback response pentru eroare
            error_type = 'timeout' if 'timeout' in str(e).lower() else 'error'
            fallback_msg = self._get_fallback(error_type, user_lang)
            
            if fallback_msg:
                self.get_logger().debug(f'📢 Sending fallback response: {fallback_msg}')
                self._publish_chunk(fallback_msg, user_lang, True, session_id)
    
    def _publish_chunk(self, text: str, language: str, is_final: bool, session_id: str):
        """Publică un chunk de text."""
        chunk = TextChunk()
        chunk.text = text
        chunk.language = language
        chunk.is_final = is_final
        chunk.session_id = session_id
        self.stream_pub.publish(chunk)
        
        if text:
            self.get_logger().debug(f'📤 Chunk: "{text[:30]}..." (final={is_final})')
    
    def clear_history(self):
        """Șterge istoricul conversației."""
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
