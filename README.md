# Voice ROS2 - Conversational Robot System

A bilingual (Romanian/English) conversational robot system built with ROS2. Features real-time voice interaction, wake word detection, a legacy Groq text pipeline, and a Gemini Live speech-to-speech backend.

## 🏗️ Architecture

The system uses a **client-server architecture** with ROS2 nodes:

### Server Nodes (Heavy Processing)
- **ASR Node** - Automatic Speech Recognition (Deepgram Streaming STT / Faster-Whisper fallback)
- **LLM Node** - Language Model processing (Groq API with streaming & web search tool)
- **TTS Node** - Text-to-Speech (Edge TTS + Audio Cache)
- **Gemini Live Node** - Speech-to-speech via Gemini Live API

### Client Nodes (Robot Hardware)
- **Audio Capture Node** - Microphone input
- **Audio Playback Node** - Speaker output
- **Wake Word Node** - OpenWakeWord detection ("Hello robot", "Hey robot", "Goodbye robot")
- **VAD Node** - Voice Activity Detection (WebRTC VAD)
- **Barge-in Node** - Interrupt TTS when user speaks (augmented with WebRTC VAD noise filtering and PyTorch stop detector)
- **Stop Keyword Node** - Stop command detection
- **Audio Segment Node** - Audio preprocessing
- **Speaker ID Node** - Speaker identification via voice fingerprint (SpeechBrain ECAPA-TDNN)
- **Echo Canceller Node** - Software Acoustic Echo Cancellation (AEC) with standard adaptive filters and DeepFilterNet support
- **Attention Manager Node** - Spatial sound source validation and speaker focus using ReSpeaker DOA
- **ReSpeaker DOA Node** - sound Direction of Arrival (DOA) tracking from the mic array

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
- TTS: `edge-tts`, `num2words`
- Wake word and stop keyword detection: `openwakeword`, `onnxruntime`
- Speaker identification: `speechbrain`, `torch`, `torchaudio`, `huggingface_hub`, `torchcodec`

For the current `full_system.launch.py`, install the full list even if you mostly use Gemini Live, because the launch file still starts helper and legacy-side nodes alongside the realtime node.

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
GEMINI_API_KEY=your_gemini_api_key_here
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

> **DDS domain:** the launch files put every node on `ROS_DOMAIN_ID=11` by default.
> For CLI tools in a separate terminal (`ros2 topic echo`, `ros2 node list`, …) to
> see those nodes, export the same value first: `export ROS_DOMAIN_ID=11`.

### 5. ReSpeaker Microphone Tuning (Optional)
If you are using the ReSpeaker USB 4-Mic Array, apply the optimized hardware parameters (such as noise reduction and VAD registers) before starting the system:
```bash
sudo ./tools/usb_4_mic_array/apply_tuning.sh
```

## 🚀 Running the System

### Full System (Server + Client)
```bash
source /path/to/ros2_ws/install/setup.bash
 
```

### Full System with Gemini Live
```bash
source /path/to/ros2_ws/install/setup.bash
ros2 launch conversational_server full_system.launch.py \
    conversation_backend:=gemini_live
```

### Server Only
```bash
ros2 launch conversational_server server_pipeline.launch.py
```
Correct command:
source ~/voice_ros2/install/setup.bash
ros2 launch conversational_server server_pipeline.launch.py

For Gemini Live:
```bash
source ~/voice_ros2/install/setup.bash
ros2 launch conversational_server server_pipeline.launch.py \
    conversation_backend:=gemini_live
```

### Client Only (on robot hardware)
```bash
ros2 launch conversational_client client_pipeline.launch.py
```
Correct command:
source ~/voice_ros2/install/setup.bash
ros2 launch conversational_client client_pipeline.launch.py

### 🎤 Speaker Enrollment (Voice Fingerprint)

Before using voice identification, record the voice of each user:

```bash
# Record the voice (5 seconds)
python3 speaker_id/enroll_speaker.py
```

The script will ask for the name and save the voice fingerprint in `voices/enrollment/<name>.wav`. Repeat for each user.
This is the recommended way for stable enrollment of a new person.

Verify the database:
```bash
python3 speaker_id/speaker_manager.py
```

`speaker_manager.py` only checks which voices are loadable from the database. It does not record a new voice.

Automatic enrollment from conversation is intentionally stricter: it starts only when the person explicitly introduces themselves, for example: `my name is Vasile`, `call me Vasile`, `ma numesc Vasile`.

After enrollment, `speaker_id_node` will automatically identify the speaker when the system starts and communicate the name to the LLM.

## ⚙️ Configuration

### Launch Parameters

#### Network Domain (ROS_DOMAIN_ID)
All launch files isolate the system on a DDS domain, defaulting to **11**. The default
respects an already-exported `ROS_DOMAIN_ID`, and can be overridden per launch:
```bash
ros2 launch conversational_server full_system.launch.py \
    ros_domain_id:=11
```
Use the same id (`export ROS_DOMAIN_ID=11`) in any terminal running `ros2` CLI tools.

#### ROS Namespace
All nodes run under the **`voice`** namespace by default. All topics therefore live
at `/voice/<topic>` (e.g. `/voice/robot_commands`, `/voice/transcription`).

To use a different namespace:
```bash
ros2 launch conversational_server full_system.launch.py \
    namespace:=my_robot
```

CLI tools in a separate terminal also need the namespace:
```bash
export ROS_DOMAIN_ID=11
ros2 topic echo /voice/robot_commands
ros2 node list   # shows /voice/robot_command_gate_node etc.
```

The motion team must subscribe to `/voice/robot_commands` and `/voice/robot_command_status`.
See `ROBOT_COMMAND_CONTRACT.md` for the full integration spec.

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

#### Gemini Live Configuration
```bash
ros2 launch conversational_server full_system.launch.py \
    conversation_backend:=gemini_live
```

Recommended first test:
- `conversation_backend:=gemini_live`

### Wake Word Threshold
Edit `full_system.launch.py` and adjust:
```python
'threshold': 0.5,  # Lower = more sensitive (0.0-1.0)
```

## 🎯 Features

### ✅ Implemented
- ✅ **Bilingual** - Romanian and English automatic detection
- ✅ **Streaming LLM** - Real-time response generation
- ✅ **Web Search** - Gemini Live can trigger Google Search for current events and live facts
- ✅ **Wake Word** - "Hello robot" / "Hey robot" detection
- ✅ **Barge-in** - Interrupt TTS when user speaks
- ✅ **Voice Activity Detection** - Automatic speech end detection
- ✅ **Conversation History** - Context-aware responses
- ✅ **Backchannel** - "One moment..." for slow responses
- ✅ **Fallback Responses** - Error handling
- ✅ **Speaker Identification** - Voice fingerprint via SpeechBrain ECAPA-TDNN
- ✅ **Deepgram Streaming STT** - WebSocket-based, sub-100ms transcription latency (replaces Faster-Whisper)
- ✅ **Spatial DOA Filtering** - ReSpeaker sound source tracking (Direction of Arrival) to ignore side conversations
- ✅ **Software AEC & DeepFilterNet** - Advanced echo cancellation and deep noise suppression

### 🚀 New in this Branch (`feature/multi-party-deepgram`)

This branch introduces several high-performance enhancements for robust multi-party conversational interaction, spatial filtering, and noise-resilient barge-in:

#### 1. 🎙️ Deepgram Streaming STT Integration
- Replaced the slower local Faster-Whisper engine with a high-performance **Deepgram WebSocket-based streaming STT** client.
- Offers sub-100ms transcription latency and superior endpointing by injecting synthetic silence padding upon VAD-off to resolve WebSocket buffering issues.

#### 2. 🧭 Spatial DOA Attention Filtering
- Integrated ReSpeaker microphone array **Direction of Arrival (DOA)** tracking (`/doa_angle`).
- Added an **Attention Manager Node** that uses circular-mean math to compute the speaker's angular position.
- Filters out side-conversations by comparing incoming sound angles with the active speaker focus angle (using a configurable `doa_focus_margin`, default `45.0` degrees), unless explicitly addressed with a wake word.

#### 3. 🔊 WebRTC VAD for Intelligent Barge-in
- Integrated **WebRTC VAD** in the barge-in detection node (`barge_in_node.py`).
- Splitting the incoming audio frame into 10ms, 20ms, or 30ms chunks to run accurate voice/non-voice speech classification.
- Drastically reduces false barge-ins triggered by physical noises (keyboard clicks, chair squeaks, object drops) during TTS playback, with full fallback to ZCR (Zero-Crossing Rate) if WebRTC VAD is disabled.

#### 4. 🔕 Acoustic Echo Cancellation (AEC) & DeepFilterNet
- Added the **Echo Canceller Node** that performs software-based Acoustic Echo Cancellation (AEC) on the raw microphone stream using standard adaptive filtering or high-quality **DeepFilterNet** deep noise suppression.

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

### "GEMINI_API_KEY not set" Error
Make sure `.env` contains:
```bash
GEMINI_API_KEY=your_gemini_api_key_here
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

- Faster-Whisper for ASR
- Groq for LLM inference
- Edge-TTS for voice synthesis
- OpenWakeWord for wake word detection
