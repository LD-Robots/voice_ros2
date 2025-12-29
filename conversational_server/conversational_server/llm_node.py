#!/usr/bin/env python3
"""
LLM Node - Language Model processing using Groq/Ollama.

Subscribes to: /transcription (Transcription)
Publishes to: /llm_response (Transcription)
"""
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Transcription
import sys
import os

# Adaugă path-ul către proiectul existent
sys.path.insert(0, os.path.expanduser('~/Conversational_Robot/Conversational_Bot'))

from src.llm.engine import LLMLocal


class LLMNode(Node):
    def __init__(self):
        super().__init__('llm_node')
        
        # Parametri configurabili
        self.declare_parameter('provider', 'groq')
        self.declare_parameter('model', 'llama-3.1-8b-instant')
        self.declare_parameter('max_tokens', 150)
        self.declare_parameter('temperature', 0.7)
        self.declare_parameter('system_prompt', 
            'You are a friendly conversational robot assistant. '
            'Keep responses concise, natural, and helpful. '
            'Respond in the same language the user speaks.')
        
        provider = self.get_parameter('provider').value
        model = self.get_parameter('model').value
        max_tokens = self.get_parameter('max_tokens').value
        temperature = self.get_parameter('temperature').value
        system_prompt = self.get_parameter('system_prompt').value
        
        # Configurare LLM
        cfg = {
            'provider': provider,
            'model': model,
            'max_tokens': max_tokens,
            'temperature': temperature,
            'system_prompt': system_prompt,
            'history_enabled': True,
            'max_history_turns': 5,
        }
        
        # Inițializează LLM engine
        self.get_logger().info(f'Initializing LLM: provider={provider}, model={model}')
        self.engine = LLMLocal(cfg, logger=self.get_logger())
        
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
            
            # Generează răspuns folosind streaming pentru Groq
            response_text = ""
            for chunk in self.engine.generate_stream(
                user_text, 
                lang_hint=user_lang, 
                history=self.conversation_history[:-1]  # Exclude mesajul curent
            ):
                response_text += chunk
            
            response_text = response_text.strip()
            
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
    node = LLMNode()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
