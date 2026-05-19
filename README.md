# Voice ROS2 - Conversational Robot System

A bilingual (Romanian/English) conversational robot system built with ROS2. Features real-time voice interaction, wake word detection, ElevenLabs speech, OpenAI text reasoning, Brave Search for fresh web data, and an optional OpenAI Realtime backend for experiments.

## 🏗️ Architecture

The system uses a **client-server architecture** with ROS2 nodes:

### Server Nodes (Heavy Processing)
- **ASR Node** - Automatic Speech Recognition via ElevenLabs Scribe v2, with Faster-Whisper fallback
- **LLM Node** - OpenAI reasoning with explicit Brave Search decisions
- **TTS Node** - ElevenLabs TTS via Eleven v3, with Edge TTS fallback and audio cache
- **OpenAI Realtime Node** - Optional speech-to-speech via `gpt-realtime-mini`

### Client Nodes (Robot Hardware)
- **Audio Capture Node** - Microphone input
- **Audio Playback Node** - Speaker output
- **Wake Word Node** - OpenWakeWord detection ("Hello robot", "Hey robot")
- **VAD Node** - Voice Activity Detection (WebRTC VAD)
- **Barge-in Node** - Interrupt TTS when user speaks
- **Stop Keyword Node** - Stop command detection
- **Audio Segment Node** - Audio preprocessing
- **Speaker ID Node** - Speaker identification via voice fingerprint (SpeechBrain ECAPA-TDNN)

### Message Interfaces
- `Audio.msg` - Audio data chunks
- `Transcription.msg` - Speech transcription results
- `TextChunk.msg` - Streaming LLM responses

## 📋 Prerequisites

### System Dependencies
```bash
sudo apt update
sudo apt install -y python3-pip python3-dev portaudio19-dev ffmpeg libsndfile1
```

### ROS2
This project requires **ROS2 Humble** or newer. Install from:
https://docs.ros.org/en/humble/Installation.html

### Python Dependencies
```bash
cd /path/to/ros2_ws/src/voice_ros2
python3 -m pip install --break-system-packages -r requirements.txt
```

The runtime code currently depends on these Python modules:
- Audio and DSP: `numpy`, `scipy`, `sounddevice`, `pyaudio`, `soundfile`, `webrtcvad`
- ASR and text filtering: `elevenlabs`, `faster-whisper`, `rapidfuzz`
- LLM, search, and realtime: `python-dotenv`, `requests`, `websocket-client`
- TTS: `elevenlabs`, `edge-tts`, `num2words`
- Wake word and stop keyword detection: `openwakeword`, `onnxruntime`
- Speaker identification: `speechbrain`, `torch`, `torchaudio`, `huggingface_hub`, `torchcodec`

For the current `full_system.launch.py`, install the full list. ElevenLabs/OpenAI/Brave is now the default path; OpenAI Realtime remains available only when you launch `conversation_backend:=openai_realtime`.

## 🔧 Setup

### 1. Clone the Repository
```bash
cd /path/to/ros2_ws/src
git clone https://github.com/Delia63/voice_ros2.git
```

### 2. Configure Environment Variables
Create a `.env` file in the `voice_ros2/` directory:
```bash
cd /path/to/ros2_ws/src/voice_ros2
nano .env
```

Add your API keys:
```
ELEVEN_API_KEY=your_elevenlabs_api_key_here
OPENAI_API_KEY=your_openai_api_key_here
BRAVE_SEARCH_API_KEY=your_brave_search_api_key_here
```

> **Note:** The `.env` file is already in `.gitignore` to protect your API key.

### 3. Download Models


#### OpenWakeWord Models
The wake word models are already included in the repository:
- `conversational_client/models/hello_robot.onnx`
- `conversational_client/models/goodbye_robot.onnx`
- `conversational_client/models/stop_robot.onnx`

### 4. Build the Workspace
```bash
cd /path/to/ros2_ws
colcon build --symlink-install
source install/setup.bash
```

## 🚀 Running the System

### Full System (Server + Client)
```bash
source /path/to/ros2_ws/install/setup.bash
ros2 launch conversational_server full_system.launch.py
```

### Full System with ElevenLabs Speech + OpenAI Reasoning
```bash
source /path/to/ros2_ws/install/setup.bash
ros2 launch conversational_server full_system.launch.py \
    conversation_backend:=legacy
```

This default path uses ElevenLabs Scribe v2 for STT, OpenAI Responses for reasoning, Brave Search when the local search detector says fresh data is needed, and ElevenLabs Eleven v3 for TTS.

### Server Only
```bash
ros2 launch conversational_server server_pipeline.launch.py
```
corect:
source ~/voice_ros2/install/setup.bash
ros2 launch conversational_server server_pipeline.launch.py

Pentru OpenAI Realtime:
```bash
source ~/voice_ros2/install/setup.bash
ros2 launch conversational_server server_pipeline.launch.py \
    conversation_backend:=openai_realtime \
    realtime_model:=gpt-realtime-mini \
    realtime_voice:=cedar \
    realtime_web_search_enabled:=true \
    realtime_web_search_model:=gpt-4.1-mini \
    realtime_web_search_context_size:=medium \
    realtime_vad_silence_duration_ms:=550 \
    realtime_response_create_delay_ms:=100 \
    realtime_continued_turn_response_delay_ms:=450 \
    realtime_capture_during_playback:=true
```

### Client Only (on robot hardware)
```bash
ros2 launch conversational_client client_pipeline.launch.py
```
corect:
source ~/voice_ros2/install/setup.bash
ros2 launch conversational_client client_pipeline.launch.py

### 🎤 Speaker Enrollment (Voice Fingerprint)

Înainte de a folosi identificarea vocală, înregistrează vocea fiecărui utilizator:

```bash
# Înregistrează vocea (5 secunde)
python3 speaker_id/enroll_speaker.py
```

Scriptul va cere numele și va salva amprenta vocală în `voices/enrollment/<nume>.wav`. Repetă pentru fiecare utilizator.
Acesta este modul recomandat pentru enrollment stabil al unei persoane noi.

Verifică baza de date:
```bash
python3 speaker_id/speaker_manager.py
```

`speaker_manager.py` doar verifică ce voci sunt încărcabile din baza de date. Nu înregistrează o voce nouă.

Enrollment automat din conversație este intenționat mai strict: pornește doar când persoana se prezintă explicit, de exemplu `my name is Vasile`, `call me Vasile`, `ma numesc Vasile`.

După enrollment, `speaker_id_node` va identifica automat vorbitorul la pornirea sistemului și va comunica numele către LLM.

## ⚙️ Configuration

### Launch Parameters

#### ASR Configuration
```bash
ros2 launch conversational_server full_system.launch.py \
    asr_model_size:=medium
```

Available model sizes: `tiny`, `base`, `small`, `medium`, `large`

#### LLM Configuration
```bash
ros2 launch conversational_server full_system.launch.py \
    llm_model:=gpt-5.5 \
    llm_provider:=openai
```

The default reasoning provider is OpenAI via the Responses API. Brave Search is called locally only when `should_use_web_search()` detects current, live, or explicitly online information. Each decision is published as JSON on `/web_search_status`.

#### ElevenLabs Speech Configuration
Main YAML parameters:
- `asr_node.provider: "elevenlabs"`
- `asr_node.eleven_model_id: "scribe_v2"`
- `asr_node.eleven_diarize: true`
- `tts_node.provider: "elevenlabs"`
- `tts_node.eleven_model_id: "eleven_v3"`
- `tts_node.eleven_voice_id_en` / `tts_node.eleven_voice_id_ro`

ElevenLabs diarization is published on `/elevenlabs_diarization` with anonymous turn-level speakers like `speaker_0`. Persistent person identity still comes from `/speaker_id` and the enrolled voice profiles.

#### OpenAI Realtime Configuration
```bash
ros2 launch conversational_server full_system.launch.py \
    conversation_backend:=openai_realtime \
    realtime_model:=gpt-realtime-mini \
    realtime_voice:=cedar \
    realtime_web_search_enabled:=true \
    realtime_web_search_model:=gpt-4.1-mini \
    realtime_web_search_context_size:=medium \
    realtime_vad_silence_duration_ms:=550 \
    realtime_response_create_delay_ms:=100 \
    realtime_continued_turn_response_delay_ms:=450 \
    realtime_capture_during_playback:=true
```

Recommended first test:
- `conversation_backend:=openai_realtime`
- `realtime_model:=gpt-realtime-mini`
- `realtime_voice:=cedar`
- `realtime_web_search_enabled:=true`
- `realtime_web_search_model:=gpt-4.1-mini`
- `realtime_web_search_context_size:=medium`
- `realtime_vad_silence_duration_ms:=550`
- `realtime_response_create_delay_ms:=100`
- `realtime_continued_turn_response_delay_ms:=450`
- `realtime_capture_during_playback:=true`

When `conversation_backend:=openai_realtime`, the optional Realtime node can still call the local `web_search` function tool. For the default `legacy` backend, `llm_node` calls Brave Search directly and injects the results into the OpenAI reasoning request.

### Wake Word Threshold
Edit `full_system.launch.py` and adjust:
```python
'threshold': 0.5,  # Lower = more sensitive (0.0-1.0)
```

## 🎯 Features

### ✅ Implemented
- ✅ **Bilingual** - Romanian and English automatic detection
- ✅ **Streaming LLM** - Real-time response generation
- ✅ **Web Search** - Brave Search is triggered explicitly for current events and live facts
- ✅ **Wake Word** - "Hello robot" / "Hey robot" detection
- ✅ **Barge-in** - Interrupt TTS when user speaks
- ✅ **Voice Activity Detection** - Automatic speech end detection
- ✅ **Conversation History** - Context-aware responses
- ✅ **Backchannel** - "One moment..." for slow responses
- ✅ **Fallback Responses** - Error handling
- ✅ **Speaker Identification** - Voice fingerprint via SpeechBrain ECAPA-TDNN

### 🔄 Future Enhancements
- Motor commands integration
- Intent classification
- Multi-turn clarification
- Emotion detection
- Custom wake words

## 🐛 Troubleshooting

### "ELEVEN_API_KEY not set" Error
Make sure `.env` file exists and contains your ElevenLabs key:
```bash
ELEVEN_API_KEY=your_elevenlabs_api_key_here
```

### "OPENAI_API_KEY not set" Error
Make sure `.env` contains:
```bash
OPENAI_API_KEY=your_openai_api_key_here
```

### "BRAVE_SEARCH_API_KEY not set" Warning
The robot can still answer stable questions, but online search will be skipped:
```bash
BRAVE_SEARCH_API_KEY=your_brave_search_api_key_here
```

### Microphone Not Working
Check audio devices:
```bash
arecord -l
pavucontrol
```

### Build Errors
Clean and rebuild:
```bash
cd /path/to/ros2_ws
rm -rf build/ install/ log/
colcon build --symlink-install
```

### No Wake Word Detection
Lower the threshold in `full_system.launch.py`:
```python
'threshold': 0.3,  # More sensitive
```

## 📚 Project Structure

```
voice_ros2/
├── conversational_server/          # Server-side nodes
│   ├── conversational_server/
│   │   ├── asr_node.py            # Speech recognition
│   │   ├── llm_node.py            # Language model (+ speaker awareness)
│   │   ├── tts_node.py            # Text-to-speech
│   │   └── stream_shaper.py       # LLM streaming optimizer
│   └── launch/                    # Launch files
├── conversational_client/          # Client-side nodes
│   ├── conversational_client/
│   │   ├── audio_capture_node.py
│   │   ├── audio_playback_node.py
│   │   ├── wake_word_node.py
│   │   ├── vad_node.py
│   │   ├── barge_in_node.py
│   │   ├── stop_keyword_node.py
│   │   └── speaker_id_node.py     # Speaker identification
│   ├── models/                    # Wake word models
│   └── voices/                    # Stop keyword model + enrollment data
├── speaker_id/                     # Speaker fingerprint system
│   ├── speaker_manager.py         # Voice database (SpeechBrain ECAPA)
│   └── enroll_speaker.py          # Enrollment script
├── conversational_interfaces/      # ROS2 message definitions
│   └── msg/
│       ├── Audio.msg
│       ├── Transcription.msg
│       └── TextChunk.msg
├── .env                           # API keys (not in git)
└── .gitignore
```

## 👥 Authors

- **Delia Stoica** - Server-side implementation
- **Valentina Sima** - Client-side implementation

## 📄 License

TODO: Add license

## 🙏 Acknowledgments

- ElevenLabs for speech-to-text and text-to-speech
- OpenAI for reasoning
- Brave Search for online search
- Faster-Whisper and Edge-TTS for fallback speech paths
- OpenWakeWord for wake word detection
