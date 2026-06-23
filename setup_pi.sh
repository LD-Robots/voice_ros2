#!/bin/bash
# setup_pi.sh
# Automatic configuration of the Raspberry Pi system for Voice Robot (ROS2)

set -e  # Stop on first error

echo "🚀 Starting System Optimization for Raspberry Pi..."

# 1. Update & Install System Dependencies
echo "📦 Installing system dependencies..."
sudo apt-get update
sudo apt-get install -y \
    python3-pip \
    python3-venv \
    portaudio19-dev \
    libasound2-dev \
    zram-tools \
    htop \
    git

# 2. Configure ZRAM (Memory Compression)
# ZRAM is crucial for RPi 5 with 4/8GB RAM running LLMs
echo "💾 Configuring ZRAM (60% of RAM)..."

# Ensure zram module is loaded at boot
if ! grep -q "zram" /etc/modules; then
    echo "zram" | sudo tee -a /etc/modules
    echo "   ✅ Added zram to /etc/modules"
fi

# Configure zram-tools
# Modify /etc/default/zramswap to allocate 60% of RAM
# Comment out the old line and add the new one or replace the value
if grep -q "^#PERCENT=" /etc/default/zramswap; then
    # If commented out, uncomment it and set to 60
    sudo sed -i 's/^#PERCENT=.*/PERCENT=60/' /etc/default/zramswap
elif grep -q "^PERCENT=" /etc/default/zramswap; then
    # If it exists, update it to 60
    sudo sed -i 's/^PERCENT=.*/PERCENT=60/' /etc/default/zramswap
else
    # If it does not exist, add it
    echo "PERCENT=60" | sudo tee -a /etc/default/zramswap
fi

# Set the compression algorithm to zstd (good CPU/speed balance)
if grep -q "^#ALGO=" /etc/default/zramswap; then
    sudo sed -i 's/^#ALGO=.*/ALGO=zstd/' /etc/default/zramswap
elif grep -q "^ALGO=" /etc/default/zramswap; then
    sudo sed -i 's/^ALGO=.*/ALGO=zstd/' /etc/default/zramswap
else
    echo "ALGO=zstd" | sudo tee -a /etc/default/zramswap
fi


# Restart zram service to apply changes
echo "🔄 Restarting ZRAM service..."
sudo service zramswap reload || sudo service zramswap restart

# 3. Create Python Virtual Environment
echo "🐍 Setting up Python Virtual Environment..."
if [ ! -d "venv" ]; then
    # --system-site-packages is CRITICAL for access to ROS2 packages (rclpy installed via apt)
    python3 -m venv --system-site-packages venv
    echo "   ✅ Virtual Environment created in ./venv"
else
    echo "   ⚠️ Virtual Environment already exists in ./venv"
fi

# 4. Final Instructions
echo ""
echo "✅ Setup Complete!"
echo "--------------------------------------------------------"
echo "👉 To activate the environment: source venv/bin/activate"
echo "👉 To install dependencies: pip install -r requirements.txt"
echo "👉 Check memory usage with: htop"
echo "--------------------------------------------------------"
