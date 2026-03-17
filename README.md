# Voice ROS2 - Conversational Robot System (Microphone Branch)

A bilingual (Romanian/English) conversational robot system built with ROS2. Features real-time voice interaction, wake word detection, LLM processing with Groq, and TTS capabilities.

> [!NOTE]
> **This Branch**: This branch includes specialized configurations for the ReSpeaker 4-Mic Array, including AEC (Acoustic Echo Cancellation) reference setup and diagnostic tools.

## 🏗️ Architecture

The system follows a **client-server architecture** using dedicated ROS2 nodes:

### Server Nodes (Heavy Processing)
- **ASR Node**: Automatic Speech Recognition using `Faster-Whisper`.
- **LLM Node**: Language Model processing via Groq API.
- **TTS Node**: Text-to-Speech using `Piper TTS`.

### Client Nodes (Robot Hardware)
- **Audio Capture Node**: Microphone input management (optimized for ReSpeaker).
- **Audio Playback Node**: Speaker output management.
- **Wake Word Node**: `OpenWakeWord` detection.
- **VAD Node**: Voice Activity Detection.
- **Barge-in Node**: Interrupts TTS playback with PyTorch-based stop detection.
- **Speaker ID Node**: Speaker identification via voice fingerprints.

## 📋 Prerequisites

### System Dependencies
```bash
sudo apt update
sudo apt install -y python3-pip portaudio19-dev pavucontrol
```

### Python Dependencies
```bash
pip install --break-system-packages \
    faster-whisper groq piper-tts openwakeword webrtcvad \
    soundfile python-dotenv pyaudio numpy speechbrain torch \
    torchaudio torchcodec sounddevice
```

## 🔧 Setup

### 1. Hardware Configuration (AEC & Audio)
To enable the robot to hear correctly while speaking, we use a "Combined Sink" to route audio to both the physical speaker and the AEC reference.

```bash
# Run the setup script
bash scripts/setup_audio.sh
```
*Open `pavucontrol`, go to the "Playback" tab, and ensure the robot's output is set to **Combined_Sink**.*

### 2. Environment Variables
Create a `.env` file in the root directory:
```bash
echo "GROQ_API_KEY=your_key_here" > .env
```

### 3. Build & Source
```bash
colcon build --symlink-install
source install/setup.bash
```

## 🚀 Running the System

### Full Pipeline
```bash
ros2 launch conversational_server full_system.launch.py
```

### Diagnostic Tools
Use these scripts to verify microphone and detection performance:
```bash
# Test wake word and stop keyword detection levels
python3 scripts/test_keywords_detection.py
```

### 🎤 Speaker Enrollment
Before using speaker identification, enroll each user's voice:
```bash
# Record a 5-second sample
python3 scripts/enroll_speaker.py
```
*Files are saved to `conversational_client/voices/enrollment/` and are ignored by Git for privacy.*

## ⚙️ Configuration
- **Wake Word Threshold**: Adjust in `client_pipeline.launch.py` under `wake_word_node`.
- **Barge-in Sensitivity**: Adjusted via `stop_prob_threshold` in the launch file.

## 📚 Project Structure
```
voice_ros2/
├── conversational_server/    # Server nodes
├── conversational_client/    # Client nodes & local assets
│   ├── models/              # Detection models
│   └── voices/              # Enrollment data (Git ignored)
├── scripts/                  # Relay scripts & hardware setup
├── .env                     # Private keys
└── .gitignore               # Repo rules
```

## 👥 Authors
- **Delia Stoica** - Server architecture & AI logic.
- **Valentina Sima** - Client architecture & Hardware integration.

## 📄 License
TBD
