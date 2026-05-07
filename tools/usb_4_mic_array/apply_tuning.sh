#!/bin/bash
# Aplica parametrii optimi pe ReSpeaker la fiecare pornire
# Scris in sesiunea de calibrare ROS2

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[ReSpeaker Tuning] Applying AEC and VAD parameters..."

# NLAEC_MODE: Non-Linear AEC training mode. 1 = ON
python3 "$DIR/tuning.py" NLAEC_MODE 1

# NLATTENONOFF: Non-Linear echo attenuation. 1 = ON
python3 "$DIR/tuning.py" NLATTENONOFF 1

# GAMMAVAD_SR: Voice Activity Detection Threshold. 5.0 = sensibilitate buna
python3 "$DIR/tuning.py" GAMMAVAD_SR 5.0

# GAMMA_E / GAMMA_ENL: Agresivitate maxima a scaderii ecoului si distorsiunilor
python3 "$DIR/tuning.py" GAMMA_E 3.0
python3 "$DIR/tuning.py" GAMMA_ETAIL 3.0
python3 "$DIR/tuning.py" GAMMA_ENL 5.0

echo "[ReSpeaker Tuning] Done."
