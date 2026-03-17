#!/bin/bash

# Identificam sink-urile
LAPTOP_SINK="alsa_output.pci-0000_00_1f.3-platform-skl_hda_dsp_generic.HiFi__hw_sofhdadsp_3__sink"
RESPEAKER_SINK="alsa_output.usb-SEEED_ReSpeaker_4_Mic_Array__UAC1.0_-00.analog-stereo"

echo "🔧 Configurăm Pipewire pentru AEC Hardware..."

# 1. Ne asigurăm că ReSpeaker are profilul Duplex (In/Out)
pactl set-card-profile alsa_card.usb-SEEED_ReSpeaker_4_Mic_Array__UAC1.0_-00 output:analog-stereo+input:analog-surround-51

# 2. Creăm un Combined Sink (o ieșire virtuală care trimite sunetul în ambele părți)
# Ștergem dacă există deja
pactl unload-module module-combine-sink 2>/dev/null

echo "🔗 Creăm Combined Sink (Laptop + ReSpeaker)..."
pactl load-module module-combine-sink \
    sink_name=robot_combined \
    slaves=$LAPTOP_SINK,$RESPEAKER_SINK \
    sink_properties=device.description="Robot_Voice_AEC_Ready"

# 3. Setăm acest sink ca fiind cel implicit
pactl set-default-sink robot_combined

echo "✅ Configurare completă!"
echo "🔊 Acum sunetul robotului va merge spre laptop (ca să-l auzi) ȘI spre ReSpeaker (pentru referință AEC)."
echo "🚀 Încearcă acum să spui 'stop' în timp ce robotul vorbește!"
