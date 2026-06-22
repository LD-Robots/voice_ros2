#!/bin/bash

# Identify sinks for Laptop and ReSpeaker
LAPTOP_SINK="alsa_output.pci-0000_00_1f.3-platform-skl_hda_dsp_generic.HiFi__hw_sofhdadsp__sink"
RESPEAKER_SINK="alsa_output.usb-SEEED_ReSpeaker_4_Mic_Array__UAC1.0_-00.analog-stereo"

echo "🔄 Checking for Combined_Sink..."

if pactl list sinks short | grep -q "Combined_Sink"; then
    echo "✅ Combined_Sink already exists."
else
    echo "🏗️ Creating Combined_Sink..."
    pactl load-module module-combine-sink \
        sink_name=combined \
        slaves=$LAPTOP_SINK,$RESPEAKER_SINK \
        sink_properties=device.description=Combined_Sink
    
    if [ $? -eq 0 ]; then
        echo "✅ Combined_Sink successfully created!"
    else
        echo "❌ Error creating Combined_Sink. Check if ReSpeaker is connected."
    fi
fi

echo "ℹ️ Do not forget to select 'Combined_Sink' in pavucontrol (Playback tab) for the robot!"
