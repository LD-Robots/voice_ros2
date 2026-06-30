#!/bin/bash
# Apply optimal parameters on ReSpeaker at each startup
# NOTE: We now have two distinct profiles depending on physical setup:
#   - Inside the head (Production):  run ./apply_tuning_in_head.sh
#   - Outside the head (Development): run ./apply_tuning_outside_head.sh

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[ReSpeaker Tuning] Applying default stable parameters (In-Head mode)..."
echo "  -> If testing outside the head, please run: ./apply_tuning_outside_head.sh"

# We removed NLAEC_MODE because it was causing USB timeouts on this laptop
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
