# Voice ROS2 - Conversational Robot System

A bilingual (Romanian/English) conversational robot system built with ROS2. Features real-time voice interaction, wake word detection, LLM processing with Groq, and TTS capabilities.

## 🏗️ Architecture

The system follows a **client-server architecture** using dedicated ROS2 nodes:

### Server Nodes (Heavy Processing)
- **ASR Node**: Automatic Speech Recognition using `Faster-Whisper`.
- **LLM Node**: Language Model processing via Groq API (includes streaming and speaker awareness).
- **TTS Node**: Text-to-Speech using `Piper TTS`.

### Client Nodes (Robot Hardware)
- **Audio Capture Node**: Microphone input management.
- **Audio Playback Node**: Speaker output management.
- **Wake Word Node**: `OpenWakeWord` detection ("Hello robot", "Hey robot").
- **VAD Node**: Voice Activity Detection using `WebRTC VAD`.
- **Barge-in Node**: Interrupts TTS playback when the user speaks.
- **Stop Keyword Node**: Specific command detection to halt operations.
- **Audio Segment Node**: Audio preprocessing and buffering.
- **Speaker ID Node**: Speaker identification via voice fingerprints (SpeechBrain ECAPA-TDNN).

### Message Interfaces
- `Audio.msg`: Raw audio data chunks.
- `Transcription.msg`: Speech-to-text results.
- `TextChunk.msg`: Streaming LLM response tokens.

## 📋 Prerequisites

### System Dependencies
```bash
sudo apt update
sudo apt install -y python3-pip portaudio19-dev
```

### ROS2
This project requires **ROS2 Humble** or newer. Installation guide:
[ROS2 Humble Documentation](https://docs.ros.org/en/humble/Installation.html)

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
touch .env
```

Add your Groq API key:
```
GROQ_API_KEY=your_api_key_here
```

> **Note:** The `.env` file is excluded via `.gitignore` to prevent API key exposure.

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
The wake word models are included in the repository:
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
source ~/ros2_ws/install/setup.bash
ros2 launch conversational_server full_system.launch.py
```

### Server Only
```bash
source ~/ros2_ws/install/setup.bash
ros2 launch conversational_server server_pipeline.launch.py
```

### Client Only (on robot hardware)
```bash
source ~/ros2_ws/install/setup.bash
ros2 launch conversational_client client_pipeline.launch.py
```

### 🎤 Speaker Enrollment (Voice Fingerprint)

Before using speaker identification, you must enroll each user's voice:

```bash
# Record voice (5-second sample)
python3 scripts/enroll_speaker.py
```

1. Enter the name when prompted (e.g., "Delia").
2. The script will save the voice fingerprint to `conversational_client/voices/enrollment/<name>.wav`.
3. **Privacy Note**: These `.wav` files are ignored by Git to ensure user privacy. They stay local on your machine.

Once enrolled, the `speaker_id_node` will automatically identify the speaker when the system starts.

## ⚙️ Configuration

### Launch Parameters

#### ASR Configuration
```bash
ros2 launch conversational_server full_system.launch.py asr_model_size:=medium
```
Available sizes: `tiny`, `base`, `small`, `medium`, `large`.

#### LLM Configuration
```bash
ros2 launch conversational_server full_system.launch.py \
    llm_model:=llama-3.1-8b-instant \
    llm_provider:=groq
```
Available Groq models: `llama-3.1-8b-instant`, `mixtral-8x7b-32768`.

### Wake Word Threshold
Adjust the sensitivity in `full_system.launch.py`:
```python
'threshold': 0.5,  # Range 0.0-1.0 (Lower = more sensitive)
```

## 🎯 Features

### ✅ Implemented
- **Bilingual Support**: Automatic Romanian and English detection.
- **Streaming LLM**: Real-time response generation with optimized buffering.
- **Wake Word**: Fast detection using `OpenWakeWord`.
- **Barge-in**: Immediate interruption of TTS when the user speaks.
- **Speaker ID**: Voice identification using `SpeechBrain`.
- **Web Search**: Dynamic information retrieval via Groq.

### 🔄 Future Enhancements
- Motor command integration for robot movement.
- Intent classification for complex task handling.
- Multi-turn conversation clarification.
- Emotion detection from vocal tone.

## 🐛 Troubleshooting

### "GROQ_API_KEY not set"
Ensure your `.env` file is in the root directory and contains the correct key.

### Microphone Issues
List and verify your audio devices:
```bash
arecord -l
```

## 📚 Project Structure

```
voice_ros2/
├── conversational_server/    # Server nodes (ASR, LLM, TTS)
├── conversational_client/    # Client nodes & local assets
│   ├── models/              # Wake word models
│   └── voices/              # Enrollment data (Git ignored)
├── scripts/                  # Utility and setup scripts
├── conversational_interfaces/# Custom ROS2 message definitions
├── .env                     # Private configuration (ignored)
└── .gitignore               # Repository exclusion rules
```

## 👥 Authors
- **Delia Stoica** - Server-side architecture & ASR/LLM implementation.
- **Valentina Sima** - Client-side architecture & Audio hardware integration.

## 📄 License
TBD (To Be Determined)

## 🙏 Acknowledgments
Special thanks to the developers of Faster-Whisper, Groq, Piper TTS, and OpenWakeWord.
