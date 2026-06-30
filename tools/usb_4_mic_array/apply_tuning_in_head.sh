#!/bin/bash
# Apply optimal parameters on ReSpeaker when microphone is INSIDE the head (production mode)
# Optimized during the ROS2 calibration session for isolated head acoustics.

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[ReSpeaker Tuning] Applying IN-HEAD (Production) parameters..."

python3 "$DIR/tuning.py" \
    AGCGAIN 1.0 \
    NLATTENONOFF 0 \
    GAMMAVAD_SR 5.0 \
    GAMMA_E 3.0 \
    GAMMA_ETAIL 3.0 \
    GAMMA_ENL 5.0 \
    AGCONOFF 0 \
    HPFONOFF 1

echo "[ReSpeaker Tuning] Done."
