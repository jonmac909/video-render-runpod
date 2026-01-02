FROM runpod/pytorch:2.2.0-py3.10-cuda12.1.1-devel-ubuntu22.04

# Install dependencies
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Install FFmpeg with NVENC support (static build from BtbN)
# CUDA 12.1 driver supports NVENC API v12.x which matches latest FFmpeg builds
RUN wget -q https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz && \
    tar -xf ffmpeg-master-latest-linux64-gpl.tar.xz && \
    cp ffmpeg-master-latest-linux64-gpl/bin/ffmpeg /usr/local/bin/ && \
    cp ffmpeg-master-latest-linux64-gpl/bin/ffprobe /usr/local/bin/ && \
    rm -rf ffmpeg-master-latest-linux64-gpl* && \
    chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe

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
