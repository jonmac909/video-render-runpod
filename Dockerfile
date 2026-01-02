FROM runpod/pytorch:2.2.0-py3.10-cuda12.1.1-devel-ubuntu22.04

# Install build dependencies for FFmpeg
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    xz-utils \
    build-essential \
    yasm \
    nasm \
    pkg-config \
    git \
    libx264-dev \
    libx265-dev \
    libnuma-dev \
    && rm -rf /var/lib/apt/lists/*

# Install nv-codec-headers 12.1 (compatible with driver 530+, covers most RunPod configurations)
# Note: n12.2 requires driver 550+, n12.1 requires driver 530+ (more compatible)
RUN git clone --branch n12.1.14.0 --depth 1 https://github.com/FFmpeg/nv-codec-headers.git && \
    cd nv-codec-headers && \
    make install && \
    cd .. && rm -rf nv-codec-headers

# Build FFmpeg with NVENC support using compatible headers
# Note: FFmpeg 6.0 is compatible with nv-codec-headers 12.1 (FFmpeg 6.1 added new NVENC APIs)
# CRITICAL: Set PKG_CONFIG_PATH so configure finds ffnvcodec.pc installed by nv-codec-headers
RUN export PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:$PKG_CONFIG_PATH" && \
    git clone --branch n6.0 --depth 1 https://github.com/FFmpeg/FFmpeg.git && \
    cd FFmpeg && \
    ./configure \
        --enable-gpl \
        --enable-nonfree \
        --enable-nvenc \
        --enable-libx264 \
        --enable-libx265 \
        --disable-doc \
        --disable-debug && \
    make -j$(nproc) && \
    make install && \
    cd .. && rm -rf FFmpeg

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
