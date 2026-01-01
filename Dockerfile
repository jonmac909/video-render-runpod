FROM runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel-ubuntu22.04

# Install dependencies
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Install FFmpeg with NVENC support (static build from BtbN)
# Using autobuild-2024-01-31 which is compatible with CUDA 11.8 driver's NVENC API
RUN wget -q https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2024-01-31-12-49/ffmpeg-n6.1.1-linux64-gpl-6.1.tar.xz && \
    tar -xf ffmpeg-n6.1.1-linux64-gpl-6.1.tar.xz && \
    cp ffmpeg-n6.1.1-linux64-gpl-6.1/bin/ffmpeg /usr/local/bin/ && \
    cp ffmpeg-n6.1.1-linux64-gpl-6.1/bin/ffprobe /usr/local/bin/ && \
    rm -rf ffmpeg-n6.1.1-linux64-gpl-6.1* && \
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
