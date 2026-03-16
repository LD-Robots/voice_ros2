#!/bin/bash

# Identificăm sink-urile pentru Laptop și ReSpeaker
LAPTOP_SINK="alsa_output.pci-0000_00_1f.3-platform-skl_hda_dsp_generic.HiFi__hw_sofhdadsp__sink"
RESPEAKER_SINK="alsa_output.usb-SEEED_ReSpeaker_4_Mic_Array__UAC1.0_-00.analog-stereo"

echo "🔄 Se verifică prezența Combined_Sink..."

if pactl list sinks short | grep -q "Combined_Sink"; then
    echo "✅ Combined_Sink există deja."
else
    echo "🏗️ Se creează Combined_Sink..."
    pactl load-module module-combine-sink \
        sink_name=combined \
        slaves=$LAPTOP_SINK,$RESPEAKER_SINK \
        sink_properties=device.description=Combined_Sink
    
    if [ $? -eq 0 ]; then
        echo "✅ Combined_Sink a fost creat cu succes!"
    else
        echo "❌ Eroare la crearea Combined_Sink. Verifică dacă ReSpeaker este conectat."
    fi
fi

echo "ℹ️ Nu uita să selectezi 'Combined_Sink' în pavucontrol (tab-ul Playback) pentru robot!"
