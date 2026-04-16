# Voice ROS2 - Conversational Robot System

A bilingual (Romanian/English) conversational robot system built with ROS2. Features real-time voice interaction, wake word detection, a legacy Groq text pipeline, and an OpenAI Realtime speech-to-speech backend with optional Brave Search-backed web grounding.

## 🏗️ Architecture

The system uses a **client-server architecture** with ROS2 nodes:

### Server Nodes (Heavy Processing)
- **ASR Node** - Automatic Speech Recognition (Faster-Whisper)
- **LLM Node** - Language Model processing (Groq API with streaming)
- **TTS Node** - Text-to-Speech (Piper TTS)
- **OpenAI Realtime Node** - Speech-to-speech via `gpt-realtime-mini`

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
- ASR and text filtering: `faster-whisper`, `rapidfuzz`
- LLM and realtime: `groq`, `python-dotenv`, `requests`, `websocket-client`
- TTS: `edge-tts`, `piper-tts`, `num2words`
- Wake word and stop keyword detection: `openwakeword`, `onnxruntime`
- Speaker identification: `speechbrain`, `torch`, `torchaudio`, `huggingface_hub`, `torchcodec`

For the current `full_system.launch.py`, install the full list even if you mostly use OpenAI Realtime, because the launch file still starts helper and legacy-side nodes alongside the realtime node.

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
GROQ_API_KEY=your_api_key_here
OPENAI_API_KEY=your_openai_api_key_here
BRAVE_SEARCH_API_KEY=your_brave_search_api_key_here
```

> **Note:** The `.env` file is already in `.gitignore` to protect your API key.

### 3. Download Models

#### Piper TTS Models (Romanian + English)
```bash
cd /path/to/ros2_ws/src/voice_ros2/conversational_server/models/piper

# Romanian voice
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/ro/ro_RO/mihai/medium/ro_RO-mihai-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/ro/ro_RO/mihai/medium/ro_RO-mihai-medium.onnx.json

# English voice
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json
```

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

### Quick Start

If you already built the workspace, start by sourcing it in every new terminal:

```bash
source ~/voice_ros2/install/setup.bash
```

If your workspace is somewhere else, replace `~/voice_ros2` with your actual path.

### Recommended Run Command

This is the main command for OpenAI Realtime with Brave Search enabled:

```bash
source ~/voice_ros2/install/setup.bash
ros2 launch conversational_server full_system.launch.py \
    conversation_backend:=openai_realtime \
    realtime_model:=gpt-realtime-mini \
    realtime_voice:=cedar \
    realtime_web_search_enabled:=true \
    realtime_web_search_context_size:=medium \
    realtime_vad_threshold:=0.74 \
    realtime_vad_silence_duration_ms:=550 \
    realtime_response_create_delay_ms:=100 \
    realtime_continued_turn_response_delay_ms:=450 \
    realtime_capture_during_playback:=true
```

If playback-time interruption feels too strict, start with:

```bash
ros2 launch conversational_server full_system.launch.py \
    conversation_backend:=openai_realtime \
    realtime_model:=gpt-realtime-mini \
    realtime_voice:=cedar \
    realtime_web_search_enabled:=true \
    realtime_web_search_context_size:=medium \
    realtime_vad_threshold:=0.74 \
    realtime_vad_silence_duration_ms:=550 \
    realtime_response_create_delay_ms:=100 \
    realtime_continued_turn_response_delay_ms:=450 \
    realtime_capture_during_playback:=true \
    realtime_playback_input_filter_min_rms_dbfs:=-30.0 \
    realtime_playback_input_filter_leak_margin_db:=7.0 \
    realtime_playback_input_filter_hits_required:=2 \
    realtime_playback_input_filter_hold_ms:=320
```

Required `.env` keys for this mode:
- `OPENAI_API_KEY`
- `BRAVE_SEARCH_API_KEY`

### Full System (Server + Client)

Use this if you want the default full pipeline without OpenAI Realtime:

```bash
source ~/voice_ros2/install/setup.bash
ros2 launch conversational_server full_system.launch.py
```

### Server Only

Use this when you want only the server-side nodes:

```bash
source ~/voice_ros2/install/setup.bash
ros2 launch conversational_server server_pipeline.launch.py
```

### Server Only with OpenAI Realtime + Brave Search

```bash
source ~/voice_ros2/install/setup.bash
ros2 launch conversational_server server_pipeline.launch.py \
    conversation_backend:=openai_realtime \
    realtime_model:=gpt-realtime-mini \
    realtime_voice:=cedar \
    realtime_web_search_enabled:=true \
    realtime_web_search_context_size:=medium \
    realtime_vad_threshold:=0.74 \
    realtime_vad_silence_duration_ms:=550 \
    realtime_response_create_delay_ms:=100 \
    realtime_continued_turn_response_delay_ms:=450 \
    realtime_capture_during_playback:=true
```

### Client Only (Robot Hardware)

Use this on the robot when you want only the client-side nodes:

```bash
source ~/voice_ros2/install/setup.bash
ros2 launch conversational_client client_pipeline.launch.py
```

### Rebuild After Code Changes

If you changed the code, rebuild before launching:

```bash
cd ~/voice_ros2
colcon build --symlink-install
source install/setup.bash
```

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
    llm_model:=llama-3.1-8b-instant \
    llm_provider:=groq
```

Available Groq models:
- `llama-3.1-8b-instant` (default, fast)
- `compound-beta` (web search enabled)
- `mixtral-8x7b-32768`

#### OpenAI Realtime Configuration
```bash
ros2 launch conversational_server full_system.launch.py \
    conversation_backend:=openai_realtime \
    realtime_model:=gpt-realtime-mini \
    realtime_voice:=cedar \
    realtime_web_search_enabled:=true \
    realtime_web_search_context_size:=medium \
    realtime_vad_threshold:=0.74 \
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
- `realtime_web_search_context_size:=medium`
- `realtime_vad_threshold:=0.74`
- `realtime_vad_silence_duration_ms:=550`
- `realtime_response_create_delay_ms:=100`
- `realtime_continued_turn_response_delay_ms:=450`
- `realtime_capture_during_playback:=true`
- `realtime_playback_input_filter_min_rms_dbfs:=-30.0`
- `realtime_playback_input_filter_leak_margin_db:=7.0`
- `realtime_playback_input_filter_hits_required:=2`
- `realtime_playback_input_filter_hold_ms:=320`

When `conversation_backend:=openai_realtime`, the Realtime model can call a local `web_search` function tool. That tool now uses Brave Search LLM Context to fetch fresh grounding snippets and source URLs, then returns them back into the same voice turn for the Realtime model to answer naturally.

Brave Search tuning:
- `realtime_web_search_context_size:=low` is the fastest, most conservative option.
- `realtime_web_search_context_size:=medium` is the default and gives broader coverage with a bit more latency.
- `realtime_web_search_context_size:=high` pulls broader grounding and is the slowest of the three.
- `realtime_web_search_model` is still accepted for launch compatibility, but it is ignored by the Brave Search path.

Playback-time capture tuning:
- `realtime_capture_during_playback:=true` keeps the raw microphone path open for OpenAI Realtime while the robot is speaking.
- `realtime_vad_threshold` controls how easily OpenAI Realtime decides that speech has started. Lower values usually make it react faster to new user speech, but they can also make it more sensitive to noise.
- `realtime_playback_input_filter_enabled:=true` keeps the playback-time anti-echo gate active. This should normally stay enabled.
- `realtime_playback_input_filter_min_rms_dbfs` controls how loud playback-time speech must be before it is forwarded. Lower values are more sensitive.
- `realtime_playback_input_filter_leak_margin_db` controls how much stronger the user's voice must be than the detected speaker leak. Lower values are more permissive.
- `realtime_playback_input_filter_hits_required` controls how many consecutive playback-time speech-like chunks are required before forwarding. Lower values open the gate faster.
- `realtime_playback_input_filter_hold_ms` keeps the playback-time gate open briefly after detected human speech so a question is not cut into fragments.

### Wake Word Threshold
Edit `full_system.launch.py` and adjust:
```python
'threshold': 0.5,  # Lower = more sensitive (0.0-1.0)
```

## 🎯 Features

### ✅ Implemented
- ✅ **Bilingual** - Romanian and English automatic detection
- ✅ **Streaming LLM** - Real-time response generation
- ✅ **Web Search** - OpenAI Realtime can trigger Brave Search grounding for current events and live facts
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

### "GROQ_API_KEY not set" Error
Make sure `.env` file exists and contains your API key:
```bash
cat /path/to/ros2_ws/src/voice_ros2/.env
```

### "OPENAI_API_KEY not set" Error
Make sure `.env` contains:
```bash
OPENAI_API_KEY=your_openai_api_key_here
```

### "BRAVE_SEARCH_API_KEY not set" Error
Make sure `.env` contains:
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
│   ├── models/piper/              # TTS models
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

- Faster-Whisper for ASR
- Groq for LLM inference
- Piper TTS for voice synthesis
- OpenWakeWord for wake word detection
