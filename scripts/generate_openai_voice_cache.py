#!/usr/bin/env python3
import os
import json
import base64
import time
import websocket
import wave
from pathlib import Path
from dotenv import load_dotenv

# Phrases to generate
PHRASES = {
    "ack_en": "Hello. I am here and listening.",
    "goodbye_en": "Goodbye. I will be here when you need me again.",
    "filler_en": "One moment please...",
}

# Output directory
OUTPUT_DIR = Path("conversational_server/resources/static_audio")
VOICE = "cedar"
MODEL = "gpt-realtime-mini"

def generate_phrase(phrase_key, text):
    output_file = OUTPUT_DIR / f"{phrase_key}.wav"
    print(f"\n🔄 Generating '{phrase_key}': \"{text}\"")
    
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("❌ Error: OPENAI_API_KEY not found in .env")
        return
        
    url = f"wss://api.openai.com/v1/realtime?model={MODEL}"
    headers = [
        f"Authorization: Bearer {api_key}",
        "OpenAI-Beta: realtime=v1",
    ]
    
    audio_data = []
    done = False
    
    def on_open(ws):
        print("  Connected to OpenAI Realtime...")
        # 1. Update session for voice
        ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "voice": VOICE,
                "input_audio_transcription": None
            }
        }))
        
        # 2. Request the specific text
        ws.send(json.dumps({
            "type": "response.create",
            "response": {
                "instructions": f"Repeat EXACTLY this phrase and then stop: {text}"
            }
        }))
        
    def on_message(ws, message):
        nonlocal done
        event = json.loads(message)
        etype = event.get("type")
        
        if etype == "response.audio.delta":
            delta = base64.b64decode(event["delta"])
            audio_data.append(delta)
        elif etype == "response.done":
            print(f"  ✅ Response finished for {phrase_key}")
            ws.close()
            done = True
        elif etype == "error":
            print(f"  ❌ API Error: {event.get('error')}")
            ws.close()
            done = True
            
    ws = websocket.WebSocketApp(
        url,
        header=headers,
        on_open=on_open,
        on_message=on_message
    )
    
    ws.run_forever()
    
    if audio_data:
        full_audio = b"".join(audio_data)
        # OpenAI Realtime outputs 24kHz Mono 16-bit PCM
        with wave.open(str(output_file), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2) # 16-bit
            wav.setframerate(24000)
            wav.writeframes(full_audio)
        print(f"  💾 Saved to {output_file} (size: {len(full_audio)} bytes)")
    else:
        print(f"  ⚠️ No audio received for {phrase_key}")

def main():
    # Load .env from workspace root
    load_dotenv()
    
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    for key, text in PHRASES.items():
        generate_phrase(key, text)
        time.sleep(1) # Small delay between requests

if __name__ == "__main__":
    main()
