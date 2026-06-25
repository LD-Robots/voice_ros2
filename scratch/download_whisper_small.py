import os
from huggingface_hub import snapshot_download

model_id = "OpenVINO/whisper-small-int8-ov"
local_dir = "/home/gabi/ros2_ws/src/voice_ros2/voices/whisper-small-int8-ov"

print(f"⏳ Downloading {model_id} from Hugging Face Hub...")
snapshot_download(repo_id=model_id, local_dir=local_dir)
print(f"✅ Model downloaded successfully to {local_dir}!")
