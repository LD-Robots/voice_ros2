# Voice ROS2 — Conversational Robot System

> A bilingual (Romanian/English) conversational robot system built on **ROS2**.
> The system handles the full voice interaction pipeline — wake word → speaker identification → speech understanding → response generation → audio playback — with hardware-aware acoustic tuning for the ReSpeaker XVF-3000 microphone array.

---

## 🏗️ Architecture

The system follows a **client-server** split. The server runs the heavy AI models; the client runs on the robot and handles all audio I/O and hardware interaction.

<details>
<summary><b>🖥️ Server Nodes</b></summary>
<br>

| Node | Role |
|---|---|
| `backend_manager_node` | Switches between `gemini_live` and `legacy` backends at runtime |
| `gemini_live_node` | Native speech-to-speech via Gemini Live API — **primary backend** |
| `asr_node` | Faster-Whisper local ASR (legacy backend) |
| `llm_node` | Groq LLM with streaming + Google Search tool (legacy backend) |
| `tts_node` | Edge-TTS text-to-speech with audio cache (legacy backend) |

</details>

<details>
<summary><b>🤖 Client Nodes</b></summary>
<br>

| Node | Role |
|---|---|
| `audio_capture_node` | Microphone input (ReSpeaker or generic USB audio) |
| `echo_canceller_node` | Software AEC: adaptive filter + WebRTC NS + optional DeepFilterNet |
| `vad_node` | Voice Activity Detection (WebRTC VAD) |
| `wake_word_node` | OpenWakeWord detection (`hello_robot`, `goodbye_robot`) |
| `barge_in_node` | Interrupt TTS when the user speaks (legacy backend only) |
| `speaker_id_node` | Speaker identification via SpeechBrain ECAPA-TDNN voiceprints |
| `attention_manager_node` | Tracks and maintains focus on the active speaker |
| `conversation_control_node` | Session orchestration and speaker routing |
| `person_memory_store_node` | Automatic speaker enrollment from conversation |
| `acoustic_monitor_node` | Acoustic environment monitoring (quiet / moderate / noisy) |
| `xmos_hardware_node` | Exclusive USB control of ReSpeaker XVF-3000 DSP registers |
| `audio_playback_node` | Speaker output with dynamic volume adaptation |
| `audio_segment_node` | Audio buffering and segmentation |
| `session_manager_node` | Conversation session lifecycle |
| `voice_command_node` | Robot movement command extraction from transcription |
| `robot_command_gate_node` | Safety gate for robot movement commands |

</details>

<details>
<summary><b>📨 ROS2 Interfaces</b></summary>
<br>

**Messages** (`conversational_interfaces/msg`):

| Message | Description |
|---|---|
| `Audio` | Raw audio chunks |
| `Transcription` | Speech transcription results |
| `TextChunk` | Streaming LLM response tokens |
| `WakeWord` | Wake word detection events |
| `RobotCommand` | Robot movement commands |

**Services** (`conversational_interfaces/srv`):

| Service | Description |
|---|---|
| `SetXmosParam` | Set a DSP register on the XMOS chip at runtime |

</details>

---

## 📋 Prerequisites

**System packages:**
```bash
sudo apt install -y python3-pip python3-dev portaudio19-dev ffmpeg libsndfile1
```

**ROS2 Humble+:** https://docs.ros.org/en/humble/Installation.html

**Python dependencies:**
```bash
pip install --break-system-packages -r requirements.txt
```

---

## 🔧 Setup

### 1. Clone
```bash
cd ~/ros2_ws/src
git clone https://github.com/LD-Robots/voice_ros2.git
```

### 2. API Keys
Create a `.env` file in the workspace root:
```env
GEMINI_API_KEY=your_gemini_key_here
GROQ_API_KEY=your_groq_key_here
```

> `.env` is already in `.gitignore`.

### 3. Build
```bash
cd ~/ros2_ws
colcon build --symlink-install
source install/setup.bash
```

> **DDS Domain:** all nodes run on `ROS_DOMAIN_ID=11` by default.
> Any separate terminal running `ros2` CLI tools must export the same value first:
> ```bash
> export ROS_DOMAIN_ID=11
> ```

### 4. Speaker Enrollment

Record a voice fingerprint for each person before first use:
```bash
python3 speaker_id/enroll_speaker.py
```

The system also supports **automatic enrollment from conversation** — it activates when someone introduces themselves (e.g. *"my name is Alex"*, *"mă numesc Alex"*).

---

## 🚀 Running

### Full System — Gemini Live *(recommended)*
```bash
source ~/ros2_ws/install/setup.bash

ros2 launch conversational_server full_system.launch.py \
    config:=laptop \
    conversation_backend:=gemini_live
```

> Use `config:=raspberry` on the robot.

### Full System — Legacy backend *(Whisper + Groq + TTS)*
```bash
ros2 launch conversational_server full_system.launch.py \
    config:=laptop \
    conversation_backend:=legacy
```

### Server / Client separately
```bash
ros2 launch conversational_server server_pipeline.launch.py
ros2 launch conversational_client client_pipeline.launch.py
```

---

## ⚙️ Configuration

### Config Profiles

All node parameters are defined in YAML profiles under `conversational_server/config/`:

| File | Target |
|---|---|
| `params_laptop.yaml` | Development machine — DeepFilterNet active, generic USB audio |
| `params_raspberry.yaml` | Robot (Raspberry Pi) — ReSpeaker 6-Mic, DeepFilterNet disabled |

```bash
ros2 launch conversational_server full_system.launch.py config:=raspberry
```

### Launch Arguments

| Argument | Default | Description |
|---|---|---|
| `config` | `raspberry` | Config profile (`raspberry` / `laptop`) |
| `conversation_backend` | `gemini_live` | Active backend (`gemini_live` / `legacy`) |
| `namespace` | `voice` | ROS namespace — all topics live at `/voice/<topic>` |
| `ros_domain_id` | `11` | DDS domain isolation |
| `asr_model_size` | `medium` | Whisper model size override |

---

## 🔊 XMOS Hardware Node

`xmos_hardware_node` holds **exclusive USB access** to the XMOS DSP chip on the ReSpeaker XVF-3000:

- **Publishes** in real time: `hardware_doa` (Direction of Arrival angle), `hardware_vad`, `hardware_rt60` (room reverberation estimate)
- **Exposes** the `set_xmos_param` ROS service — any node can tune a DSP register without dealing with USB directly
- **Automatically applies** a baseline tuning on startup (AGC, noise suppression, VAD thresholds) — replaces the old `apply_tuning.sh` script
- **Auto-reconnects** if the USB device disconnects and comes back

`acoustic_monitor_node` subscribes to `hardware_rt60` and dynamically adjusts noise suppression parameters via the service based on the detected acoustic environment.

---

## 📁 Project Structure

```
voice_ros2/
├── conversational_server/
│   ├── conversational_server/      # Server-side nodes
│   ├── config/
│   │   ├── params_laptop.yaml      # Dev profile
│   │   └── params_raspberry.yaml   # Robot profile
│   └── launch/
├── conversational_client/
│   ├── conversational_client/      # Client-side nodes
│   ├── models/                     # Wake word ONNX models
│   └── launch/
├── conversational_interfaces/
│   ├── msg/                        # ROS2 message definitions
│   └── srv/                        # ROS2 service definitions
├── speaker_id/                     # Speaker enrollment scripts
├── voices/
│   ├── enrollment/                 # Voiceprint .wav files
│   └── fillers/                    # Audio filler files
├── tools/                          # Hardware utilities
├── ROBOT_COMMAND_CONTRACT.md       # Robot command integration spec
├── requirements.txt
└── .env                            # API keys (not in git)
```

---

## 🐛 Troubleshooting

| Problem | Solution |
|---|---|
| `GEMINI_API_KEY not set` | Check that `.env` exists in the workspace root |
| Microphone not detected | Run `arecord -l`, then set `device_index` in the config YAML |
| Wake word not triggering | Lower the threshold: `model_thresholds: "hello_robot:0.15,..."` |
| Build errors | `rm -rf build/ install/ log/ && colcon build --symlink-install` |
| `ros2 topic echo` sees no topics | Run `export ROS_DOMAIN_ID=11` in your terminal first |
| XMOS node not connecting | Run `lsusb \| grep 2886` — device must be present before launch |

---

## 👥 Authors

- **Delia Stoica**
- **Valentina Sima**
- **Vasile Belmega**
- **Mario Cercel**

## 🙏 Acknowledgments

Google Gemini &nbsp;·&nbsp; Groq &nbsp;·&nbsp; OpenWakeWord &nbsp;·&nbsp; SpeechBrain &nbsp;·&nbsp; Faster-Whisper &nbsp;·&nbsp; Edge-TTS &nbsp;·&nbsp; DeepFilterNet &nbsp;·&nbsp; WebRTC
