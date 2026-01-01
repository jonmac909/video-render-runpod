# Video Render RunPod Worker

GPU-accelerated video rendering worker for HistoryGen AI. Uses NVIDIA NVENC for fast H.264 encoding.

## Features

- GPU-accelerated video encoding (NVENC)
- Smoke + embers overlay effects
- Automatic Supabase upload
- ~3x faster than CPU rendering

## Input Schema

```json
{
  "image_urls": ["https://...", "https://..."],
  "timings": [
    {"startSeconds": 0, "endSeconds": 5},
    {"startSeconds": 5, "endSeconds": 10}
  ],
  "audio_url": "https://...",
  "project_id": "abc123",
  "apply_effects": true,
  "supabase_url": "https://xxx.supabase.co",
  "supabase_key": "service_role_key"
}
```

## Output Schema

```json
{
  "video_url": "https://xxx.supabase.co/storage/v1/object/public/generated-assets/abc123/video.mp4",
  "render_time_seconds": 45.2
}
```

## Deployment

1. Create new RunPod Serverless Endpoint
2. Connect to this GitHub repo
3. Set GPU type: RTX A4000 or better
4. Set max workers: 2-4
5. Set idle timeout: 5 minutes

## Local Testing

```bash
docker build -t video-render .
docker run --gpus all -p 8000:8000 video-render
```
