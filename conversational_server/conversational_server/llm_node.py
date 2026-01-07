#!/usr/bin/env python3
"""
LLM Node - Language Model processing using Groq API with STREAMING.

Subscribes to: /transcription (Transcription)
Publishes to: 
  - /llm_stream (TextChunk) - streaming chunks
  - /llm_response (Transcription) - complete response (for compatibility)
"""
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Transcription, TextChunk
import os
import re
import uuid
import threading

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
        self.declare_parameter('system_prompt', 
            'You are a friendly conversational robot assistant. '
            'Keep responses concise, natural, and helpful. '
            'Respond in the same language the user speaks.')
        
        self.model = self.get_parameter('model').value
        self.max_tokens = self.get_parameter('max_tokens').value
        self.temperature = self.get_parameter('temperature').value
        self.min_chunk_chars = self.get_parameter('min_chunk_chars').value
        self.system_prompt = self.get_parameter('system_prompt').value
        
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
        self.get_logger().info(f'✅ Groq client initialized with model: {self.model}')
        
        # Istoricul conversației
        self.conversation_history = []
        
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
        
        self.get_logger().info('LLM Node started with STREAMING! Listening on /transcription')
    
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
    
    def _process_streaming(self, user_text: str, user_lang: str):
        """Procesează răspunsul LLM cu streaming."""
        session_id = str(uuid.uuid4())[:8]
        
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
            
            # Apel Groq API cu STREAMING
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                stream=True  # STREAMING!
            )
            
            # Buffer pentru acumulare tokeni
            buffer = ""
            full_response = ""
            chunk_count = 0
            
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    token = chunk.choices[0].delta.content
                    buffer += token
                    full_response += token
                    
                    # Publică chunk când avem o propoziție completă sau suficiente caractere
                    if (SENTENCE_END.search(buffer) and len(buffer) >= self.min_chunk_chars) or \
                       len(buffer) >= self.min_chunk_chars * 2:
                        self._publish_chunk(buffer.strip(), user_lang, False, session_id)
                        chunk_count += 1
                        buffer = ""
            
            # Publică ultimul chunk (is_final=True)
            if buffer.strip():
                self._publish_chunk(buffer.strip(), user_lang, True, session_id)
                chunk_count += 1
            else:
                # Trimite un chunk gol cu is_final=True pentru a semnala sfârșitul
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
                
                self.get_logger().info(f'🤖 Bot ({chunk_count} chunks): {full_response[:80]}...')
                
                # Publică și răspunsul complet pentru compatibilitate
                out = Transcription()
                out.text = full_response
                out.language = user_lang
                out.confidence = 1.0
                self.response_pub.publish(out)
            
        except Exception as e:
            self.get_logger().error(f'LLM streaming error: {e}')
    
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
