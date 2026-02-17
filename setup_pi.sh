#!/bin/bash
# setup_pi.sh
# Configurare automată a sistemului Raspberry Pi pentru Voice Robot (ROS2)

set -e  # Oprire la prima eroare

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
# ZRAM este crucial pentru RPi 5 cu 4/8GB RAM rulând LLM-uri
echo "💾 Configuring ZRAM (60% of RAM)..."

# Asigurăm încărcarea modulului zram la boot
if ! grep -q "zram" /etc/modules; then
    echo "zram" | sudo tee -a /etc/modules
    echo "   ✅ Added zram to /etc/modules"
fi

# Configurare zram-tools
# Modificăm /etc/default/zramswap pentru a aloca 60% din RAM
# Comentăm linia veche și adăugăm cea nouă sau înlocuim valoarea
if grep -q "^#PERCENT=" /etc/default/zramswap; then
    # Dacă e comentată, o decomentăm și setăm 60
    sudo sed -i 's/^#PERCENT=.*/PERCENT=60/' /etc/default/zramswap
elif grep -q "^PERCENT=" /etc/default/zramswap; then
    # Dacă există, o actualizăm la 60
    sudo sed -i 's/^PERCENT=.*/PERCENT=60/' /etc/default/zramswap
else
    # Dacă nu există, o adăugăm
    echo "PERCENT=60" | sudo tee -a /etc/default/zramswap
fi

# Setăm algoritmul de compresie la zstd (balans bun CPU/viteză)
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
    # --system-site-packages este CRITIC pentru accesul la pachetele ROS2 (rclpy instalat via apt)
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
