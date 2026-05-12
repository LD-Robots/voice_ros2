#!/bin/bash
# Aplica parametrii optimi pe ReSpeaker la fiecare pornire (Consolidated version)
# Scris in sesiunea de calibrare ROS2

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[ReSpeaker Tuning] Applying stable parameters in a single session..."

# We removed NLAEC_MODE because it was causing USB timeouts on this laptop
python3 "$DIR/tuning.py" \
    AGCGAIN 1.0 \
    NLATTENONOFF 1 \
    GAMMAVAD_SR 5.0 \
    GAMMA_E 3.0 \
    GAMMA_ETAIL 3.0 \
    GAMMA_ENL 5.0 \
    AGCONOFF 0

echo "[ReSpeaker Tuning] Done."
