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

# Install nv-codec-headers 12.2 (matches RunPod's NVENC API version)
RUN git clone --branch n12.2.72.0 --depth 1 https://github.com/FFmpeg/nv-codec-headers.git && \
    cd nv-codec-headers && \
    make install && \
    cd .. && rm -rf nv-codec-headers

# Build FFmpeg with NVENC support using compatible headers
# Note: We only enable nvenc (not cuda-nvcc/libnpp) - those require nvcc compiler setup
# NVENC encoding only needs nv-codec-headers, not full CUDA toolkit compilation
RUN git clone --branch n6.1 --depth 1 https://github.com/FFmpeg/FFmpeg.git && \
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
