# Voice ROS2 - Conversational Robot System

A bilingual (Romanian/English) conversational robot system built with ROS2. Features real-time voice interaction, wake word detection, LLM processing with Groq, and TTS capabilities.

## 🏗️ Architecture

The system uses a **client-server architecture** with ROS2 nodes:

### Server Nodes (Heavy Processing)
- **ASR Node** - Automatic Speech Recognition (Faster-Whisper)
- **LLM Node** - Language Model processing (Groq API with streaming)
- **TTS Node** - Text-to-Speech (Piper TTS)

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
sudo apt install -y python3-pip portaudio19-dev
```

### ROS2
This project requires **ROS2 Humble** or newer. Install from:
https://docs.ros.org/en/humble/Installation.html

### Python Dependencies
```bash
pip install --break-system-packages \
    faster-whisper \
    groq \
    piper-tts \
    openwakeword \
    webrtcvad \
    soundfile \
    python-dotenv \
    pyaudio \
    numpy \
    speechbrain \
    torch \
    torchaudio \
    torchcodec
```

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

Add your Groq API key:
```
GROQ_API_KEY=your_api_key_here
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

### Full System (Server + Client)
```bash
source /path/to/ros2_ws/install/setup.bash
ros2 launch conversational_server full_system.launch.py
```

### Server Only
```bash
ros2 launch conversational_server server_pipeline.launch.py
```
corect:
source ~/voice_ros2/install/setup.bash
ros2 launch conversational_server server_pipeline.launch.py

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

Scriptul va cere numele și va salva amprenta vocală în `conversational_client/voices/enrollment/<nume>.wav`. Repetă pentru fiecare utilizator.

Verifică baza de date:
```bash
python3 speaker_id/speaker_manager.py
```

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

### Wake Word Threshold
Edit `full_system.launch.py` and adjust:
```python
'threshold': 0.5,  # Lower = more sensitive (0.0-1.0)
```

## 🎯 Features

### ✅ Implemented
- ✅ **Bilingual** - Romanian and English automatic detection
- ✅ **Streaming LLM** - Real-time response generation
- ✅ **Web Search** - Auto-enabled for current events (news, weather, etc.)
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
