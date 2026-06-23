#!/bin/bash
# Apply optimal parameters on ReSpeaker when microphone is OUTSIDE the head (development mode)
# Optimized to handle louder direct echo and reflections.

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[ReSpeaker Tuning] Applying OUTSIDE-HEAD (Development) parameters..."

python3 "$DIR/tuning.py" \
    AGCGAIN 1.0 \
    NLATTENONOFF 1 \
    GAMMAVAD_SR 5.0 \
    GAMMA_E 4.0 \
    GAMMA_ETAIL 4.0 \
    GAMMA_ENL 5.0 \
    AGCONOFF 0 \
    HPFONOFF 3

echo "[ReSpeaker Tuning] Done."
