#!/usr/bin/env python3
"""
LLM Node - Language Model processing using Groq API (Standalone).

Subscribes to: /transcription (Transcription)
Publishes to: /llm_response (Transcription)
"""
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Transcription
import os

# Groq pentru LLM
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    print("⚠️ groq not installed. Run: pip install groq")


class LLMNode(Node):
    def __init__(self):
        super().__init__('llm_node')
        
        # Parametri configurabili
        self.declare_parameter('model', 'llama-3.1-8b-instant')
        self.declare_parameter('max_tokens', 150)
        self.declare_parameter('temperature', 0.7)
        self.declare_parameter('system_prompt', 
            'You are a friendly conversational robot assistant. '
            'Keep responses concise, natural, and helpful. '
            'Respond in the same language the user speaks.')
        
        self.model = self.get_parameter('model').value
        self.max_tokens = self.get_parameter('max_tokens').value
        self.temperature = self.get_parameter('temperature').value
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
        
        # Publisher pentru răspunsul LLM
        self.response_pub = self.create_publisher(
            Transcription,
            '/llm_response',
            10
        )
        
        self.get_logger().info('LLM Node started! Listening on /transcription')
    
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
                
                # Limitează istoricul la ultimele 10 mesaje
                if len(self.conversation_history) > 10:
                    self.conversation_history = self.conversation_history[-10:]
                
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
