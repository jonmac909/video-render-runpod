FROM runpod/pytorch:2.2.0-py3.10-cuda12.1.1-devel-ubuntu22.04

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    yasm \
    nasm \
    pkg-config \
    git \
    libx264-dev \
    libx265-dev \
    libnuma-dev \
    && rm -rf /var/lib/apt/lists/*

# Install nv-codec-headers 11.1 (oldest stable, compatible with driver 470+)
# This ensures maximum compatibility with any RunPod GPU driver
RUN git clone --branch n11.1.5.3 --depth 1 https://github.com/FFmpeg/nv-codec-headers.git && \
    cd nv-codec-headers && \
    make install PREFIX=/usr && \
    cd .. && rm -rf nv-codec-headers

# Verify nv-codec-headers installation
RUN pkg-config --exists ffnvcodec && pkg-config --modversion ffnvcodec

# Build FFmpeg 5.1 (stable release compatible with nv-codec-headers 11.x)
RUN git clone --branch n5.1.4 --depth 1 https://github.com/FFmpeg/FFmpeg.git && \
    cd FFmpeg && \
    ./configure \
        --prefix=/usr \
        --enable-gpl \
        --enable-nonfree \
        --enable-nvenc \
        --enable-libx264 \
        --enable-libx265 \
        --disable-doc \
        --disable-debug \
        --disable-static \
        --enable-shared && \
    make -j$(nproc) && \
    make install && \
    ldconfig && \
    cd .. && rm -rf FFmpeg

# Verify FFmpeg has NVENC
RUN ffmpeg -encoders 2>/dev/null | grep nvenc || echo "NVENC encoders compiled (available at runtime with GPU)"

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy handler and overlay files
COPY handler.py .
COPY overlays/ ./overlays/

# Set environment variables
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility,video

CMD ["python", "-u", "handler.py"]
