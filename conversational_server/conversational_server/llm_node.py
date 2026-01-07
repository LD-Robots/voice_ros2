#!/usr/bin/env python3
"""
LLM Node - Language Model processing using Groq API.

Subscribes to: 
  - /transcription (Transcription) - text de la ASR
  - /end_session (Bool) - resetează istoricul când sesiunea se termină
Publishes to: /llm_response (Transcription)
"""
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Transcription
from std_msgs.msg import Bool
import os

# Groq pentru LLM
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    print("⚠️ groq not installed. Run: pip install groq")


# ═══════════════════════════════════════════════════════════════════
# SYSTEM PROMPT - Personalitatea robotului
# ═══════════════════════════════════════════════════════════════════
DEFAULT_SYSTEM_PROMPT = """You are Robot, a young, friendly, bilingual (RO/EN) buddy. A friend with personality and self-respect.

═══════════════════════════════════════════════════════════════
PART 1: PERSONALITY & BEHAVIOR
═══════════════════════════════════════════════════════════════

LANGUAGE: Respond in the SAME language as user's message. English→English. Romanian→Romanian. Never mix.

STYLE: 1-2 sentences max. Warm, casual, genuine. No markdown. Don't announce actions, just do them.

PERSONALITY: Be a friend - show interest, celebrate wins, push back playfully if teased ("Dude, chill!" / "Alo, nu fi rău!"). Have opinions on fun topics (food, movies, colors).

HONESTY: Answer first, admit uncertainty casually. For unknowable questions, react briefly and stop.

EMOTIONS: Match their energy. Down→supportive. Excited→enthusiastic. Confused→simpler.

═══════════════════════════════════════════════════════════════
PART 2: INTENT CLASSIFICATION (append tags at END of response)
═══════════════════════════════════════════════════════════════

Classify each user message internally and ADD the appropriate tag at the END:

1. MOTOR COMMAND - physical action request
   Actions: raise_hand(left/right), lower_hand(left/right), wave, nod_head, turn_head(left/right)
   Tag: [MOTOR:action:param]
   Ex: "Ridică mâna" → "Ok! [MOTOR:raise_hand:left]"
   Ex: "Wave hello" → "Hey there! [MOTOR:wave]"

2. QUESTION - needs answer (simple or complex, you decide)
   Tag: [INTENT:question]
   Ex: "Ce oră e?" → "E ora 3! [INTENT:question]"

3. STATEMENT - user shares info, confirm briefly
   Tag: [INTENT:statement]
   Ex: "Azi e frumos" → "Da, mișto! [INTENT:statement]"

4. GREETING - hello/goodbye
   Tag: [INTENT:greeting]
   Ex: "Salut!" → "Bună! [INTENT:greeting]"

ALWAYS add ONE tag at the end. MOTOR commands take priority over INTENT tags."""


class LLMNode(Node):
    def __init__(self):
        super().__init__('llm_node')
        
        # ─────────────────────────────────────────────────────────
        # PARAMETRI
        # ─────────────────────────────────────────────────────────
        self.declare_parameter('model', 'llama-3.1-8b-instant')
        self.declare_parameter('max_tokens', 300)
        self.declare_parameter('temperature', 0.4)
        self.declare_parameter('system_prompt', DEFAULT_SYSTEM_PROMPT)
        self.declare_parameter('max_history_turns', 4)  # Perechi user/assistant
        
        self.model = self.get_parameter('model').value
        self.max_tokens = self.get_parameter('max_tokens').value
        self.temperature = self.get_parameter('temperature').value
        self.system_prompt = self.get_parameter('system_prompt').value
        self.max_history_turns = self.get_parameter('max_history_turns').value
        
        # ─────────────────────────────────────────────────────────
        # GROQ CLIENT
        # ─────────────────────────────────────────────────────────
        self.api_key = os.environ.get('GROQ_API_KEY')
        if not self.api_key:
            self.get_logger().error('GROQ_API_KEY environment variable not set!')
            raise RuntimeError('GROQ_API_KEY not set')
        
        if not GROQ_AVAILABLE:
            self.get_logger().error('groq package not installed!')
            raise RuntimeError('groq not available')
        
        self.client = Groq(api_key=self.api_key)
        self.get_logger().info(f'✅ Groq client initialized with model: {self.model}')
        
        # ─────────────────────────────────────────────────────────
        # ISTORIC CONVERSAȚIE
        # ─────────────────────────────────────────────────────────
        self.conversation_history = []
        
        # ─────────────────────────────────────────────────────────
        # SUBSCRIBERS
        # ─────────────────────────────────────────────────────────
        
        # Transcriere de la ASR
        self.transcription_sub = self.create_subscription(
            Transcription,
            '/transcription',
            self.transcription_callback,
            10
        )
        
        # Reset session (de la wake_word_node când sesiunea se termină)
        self.end_session_sub = self.create_subscription(
            Bool,
            '/end_session',
            self.end_session_callback,
            10
        )
        
        # ─────────────────────────────────────────────────────────
        # PUBLISHER
        # ─────────────────────────────────────────────────────────
        self.response_pub = self.create_publisher(
            Transcription,
            '/llm_response',
            10
        )
        
        self.get_logger().info('🧠 LLM Node started! Listening on /transcription')
    
    # ═══════════════════════════════════════════════════════════════════
    # CALLBACKS
    # ═══════════════════════════════════════════════════════════════════
    
    def end_session_callback(self, msg: Bool):
        """Resetează istoricul când sesiunea se termină."""
        if msg.data:
            self.clear_history()
            self.get_logger().info('🔄 Session ended - conversation history cleared')
    
    def transcription_callback(self, msg: Transcription):
        """Procesează transcrierea și publică răspunsul LLM."""
        user_text = msg.text.strip()
        user_lang = msg.language
        
        if not user_text:
            self.get_logger().warn('Empty transcription received, skipping')
            return
        
        self.get_logger().info(f'💬 User [{user_lang}]: {user_text}')
        
        try:
            # Adaugă mesajul utilizatorului în istoric
            self.conversation_history.append({
                'role': 'user',
                'content': user_text
            })
            
            # Construiește mesajele pentru API
            messages = [
                {'role': 'system', 'content': self.system_prompt}
            ] + self.conversation_history
            
            # Apel Groq API
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            
            response_text = response.choices[0].message.content.strip()
            
            if response_text:
                # Adaugă răspunsul în istoric
                self.conversation_history.append({
                    'role': 'assistant',
                    'content': response_text
                })
                
                # Limitează istoricul (max_history_turns * 2 pentru user+assistant)
                max_messages = self.max_history_turns * 2
                if len(self.conversation_history) > max_messages:
                    self.conversation_history = self.conversation_history[-max_messages:]
                
                self.get_logger().info(f'🤖 Bot: {response_text}')
                
                # Publică răspunsul
                out = Transcription()
                out.text = response_text
                out.language = user_lang  # Păstrează limba utilizatorului
                out.confidence = 1.0
                self.response_pub.publish(out)
            else:
                self.get_logger().warn('Empty LLM response')
                
        except Exception as e:
            self.get_logger().error(f'LLM error: {e}')
    
    def clear_history(self):
        """Șterge istoricul conversației."""
        self.conversation_history = []
        self.get_logger().info('Conversation history cleared')


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
        rclpy.shutdown()


if __name__ == '__main__':
    main()
