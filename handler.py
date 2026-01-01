"""
RunPod Video Render Worker with GPU acceleration (NVENC)
Renders videos from images with smoke + embers overlay effects.
"""

import runpod
import subprocess
import os
import tempfile
import requests
import time
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Overlay files bundled with the worker
SMOKE_OVERLAY = "/app/overlays/smoke_gray.mp4"
EMBERS_OVERLAY = "/app/overlays/embers.mp4"

# FFmpeg settings
FFMPEG_PRESET = "p4"  # NVENC preset (p1=fastest, p7=slowest/best quality)
FFMPEG_CQ = "23"  # Constant quality (lower = better, 18-28 typical)


def download_file(url: str, dest_path: str, timeout: int = 60) -> bool:
    """Download a file from URL to local path."""
    try:
        print(f"Downloading: {url}")
        response = requests.get(url, timeout=timeout, stream=True)
        response.raise_for_status()

        with open(dest_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        size = os.path.getsize(dest_path)
        print(f"Downloaded: {dest_path} ({size} bytes)")
        return True
    except Exception as e:
        print(f"Download failed: {url} - {e}")
        return False


def upload_to_supabase(file_path: str, bucket: str, storage_path: str,
                       supabase_url: str, supabase_key: str) -> str:
    """Upload file to Supabase storage and return public URL."""
    try:
        print(f"Uploading to Supabase: {storage_path}")

        with open(file_path, 'rb') as f:
            file_data = f.read()

        # Supabase storage upload endpoint
        upload_url = f"{supabase_url}/storage/v1/object/{bucket}/{storage_path}"

        headers = {
            'Authorization': f'Bearer {supabase_key}',
            'Content-Type': 'video/mp4',
            'x-upsert': 'true'
        }

        response = requests.post(upload_url, headers=headers, data=file_data, timeout=300)

        if response.status_code not in [200, 201]:
            print(f"Upload failed: {response.status_code} - {response.text}")
            raise Exception(f"Upload failed: {response.status_code}")

        # Get public URL
        public_url = f"{supabase_url}/storage/v1/object/public/{bucket}/{storage_path}"
        print(f"Uploaded: {public_url}")
        return public_url

    except Exception as e:
        print(f"Upload error: {e}")
        raise


def create_concat_file(image_paths: list, timings: list, fps: int = 24) -> str:
    """Create FFmpeg concat demuxer file from images with timings."""
    concat_path = tempfile.mktemp(suffix='.txt')

    with open(concat_path, 'w') as f:
        for i, (img_path, timing) in enumerate(zip(image_paths, timings)):
            duration = timing['endSeconds'] - timing['startSeconds']
            f.write(f"file '{img_path}'\n")
            f.write(f"duration {duration}\n")

        # Add last image again (FFmpeg concat demuxer quirk)
        if image_paths:
            f.write(f"file '{image_paths[-1]}'\n")

    return concat_path


def render_video_gpu(
    image_paths: list,
    timings: list,
    audio_path: str,
    output_path: str,
    apply_effects: bool = True
) -> bool:
    """
    Render video using GPU acceleration (NVENC).

    Args:
        image_paths: List of local image file paths
        timings: List of {startSeconds, endSeconds} for each image
        audio_path: Local path to audio file
        output_path: Output video path
        apply_effects: Whether to apply smoke + embers overlay

    Returns:
        True if successful
    """
    try:
        # Create concat file for images
        concat_file = create_concat_file(image_paths, timings)

        if apply_effects:
            # Two-pass approach: render raw, then apply effects
            # This is more memory efficient than single complex filter

            # Pass 1: Images to raw video (GPU accelerated)
            raw_video = tempfile.mktemp(suffix='_raw.mp4')

            cmd_raw = [
                'ffmpeg', '-y',
                '-f', 'concat', '-safe', '0', '-i', concat_file,
                '-vf', 'scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=24',
                '-c:v', 'h264_nvenc',
                '-preset', FFMPEG_PRESET,
                '-cq', FFMPEG_CQ,
                '-pix_fmt', 'yuv420p',
                raw_video
            ]

            print(f"Pass 1: Rendering raw video...")
            result = subprocess.run(cmd_raw, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                print(f"Pass 1 failed: {result.stderr}")
                raise Exception(f"Raw render failed: {result.stderr[-500:]}")

            # Pass 2: Apply smoke + embers overlay (GPU accelerated)
            cmd_effects = [
                'ffmpeg', '-y',
                '-i', raw_video,
                '-stream_loop', '-1', '-i', SMOKE_OVERLAY,
                '-stream_loop', '-1', '-i', EMBERS_OVERLAY,
                '-i', audio_path,
                '-filter_complex',
                '[1:v]colorchannelmixer=.3:.4:.3:0:.3:.4:.3:0:.3:.4:.3:0[smoke];'
                '[0:v][smoke]blend=all_mode=multiply[with_smoke];'
                '[2:v]colorkey=0x000000:0.2:0.2[embers];'
                '[with_smoke][embers]overlay=shortest=1[out]',
                '-map', '[out]',
                '-map', '3:a',
                '-c:v', 'h264_nvenc',
                '-preset', FFMPEG_PRESET,
                '-cq', FFMPEG_CQ,
                '-pix_fmt', 'yuv420p',
                '-c:a', 'aac',
                '-b:a', '192k',
                '-shortest',
                output_path
            ]

            print(f"Pass 2: Applying effects...")
            result = subprocess.run(cmd_effects, capture_output=True, text=True, timeout=600)

            # Cleanup raw video
            if os.path.exists(raw_video):
                os.remove(raw_video)

            if result.returncode != 0:
                print(f"Pass 2 failed: {result.stderr}")
                raise Exception(f"Effects render failed: {result.stderr[-500:]}")

        else:
            # No effects - single pass with audio
            cmd = [
                'ffmpeg', '-y',
                '-f', 'concat', '-safe', '0', '-i', concat_file,
                '-i', audio_path,
                '-vf', 'scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=24',
                '-c:v', 'h264_nvenc',
                '-preset', FFMPEG_PRESET,
                '-cq', FFMPEG_CQ,
                '-pix_fmt', 'yuv420p',
                '-c:a', 'aac',
                '-b:a', '192k',
                '-shortest',
                output_path
            ]

            print(f"Rendering video without effects...")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

            if result.returncode != 0:
                print(f"Render failed: {result.stderr}")
                raise Exception(f"Render failed: {result.stderr[-500:]}")

        # Cleanup concat file
        if os.path.exists(concat_file):
            os.remove(concat_file)

        output_size = os.path.getsize(output_path)
        print(f"Render complete: {output_path} ({output_size / 1024 / 1024:.1f} MB)")
        return True

    except subprocess.TimeoutExpired:
        print("FFmpeg timeout")
        raise Exception("Render timed out after 10 minutes")
    except Exception as e:
        print(f"Render error: {e}")
        raise


def handler(job):
    """
    RunPod handler for video rendering.

    Input:
    {
        "image_urls": ["url1", "url2", ...],
        "timings": [{"startSeconds": 0, "endSeconds": 5}, ...],
        "audio_url": "https://...",
        "project_id": "abc123",
        "apply_effects": true,
        "supabase_url": "https://xxx.supabase.co",
        "supabase_key": "service_role_key"
    }

    Output:
    {
        "video_url": "https://xxx.supabase.co/storage/v1/object/public/..."
    }
    """
    job_input = job["input"]

    image_urls = job_input.get("image_urls", [])
    timings = job_input.get("timings", [])
    audio_url = job_input.get("audio_url")
    project_id = job_input.get("project_id")
    apply_effects = job_input.get("apply_effects", True)
    supabase_url = job_input.get("supabase_url")
    supabase_key = job_input.get("supabase_key")

    if not image_urls:
        return {"error": "No image URLs provided"}
    if not audio_url:
        return {"error": "No audio URL provided"}
    if not project_id:
        return {"error": "No project ID provided"}
    if not supabase_url or not supabase_key:
        return {"error": "Supabase credentials required"}
    if len(image_urls) != len(timings):
        return {"error": "Image URLs and timings count mismatch"}

    print(f"Starting video render: {len(image_urls)} images, effects={apply_effects}")
    start_time = time.time()

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            # Download all images in parallel (10 concurrent downloads)
            download_start = time.time()
            image_paths = [None] * len(image_urls)
            failed_downloads = []

            def download_image(args):
                i, url = args
                ext = '.png' if '.png' in url.lower() else '.jpg'
                img_path = os.path.join(temp_dir, f"image_{i:03d}{ext}")
                success = download_file(url, img_path)
                return i, img_path, success

            # Download images in parallel
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {executor.submit(download_image, (i, url)): i
                          for i, url in enumerate(image_urls)}

                for future in as_completed(futures):
                    i, img_path, success = future.result()
                    if success:
                        image_paths[i] = img_path
                    else:
                        failed_downloads.append(i)

            if failed_downloads:
                return {"error": f"Failed to download images: {failed_downloads[:5]}"}

            download_elapsed = time.time() - download_start
            print(f"Downloaded {len(image_paths)} images in {download_elapsed:.1f}s")

            # Download audio (larger file, keep sequential)
            audio_path = os.path.join(temp_dir, "audio.wav")
            if not download_file(audio_url, audio_path, timeout=180):
                return {"error": "Failed to download audio"}

            # Render video
            output_path = os.path.join(temp_dir, "output.mp4")
            render_video_gpu(image_paths, timings, audio_path, output_path, apply_effects)

            # Upload to Supabase
            storage_path = f"{project_id}/video.mp4"
            video_url = upload_to_supabase(
                output_path,
                "generated-assets",
                storage_path,
                supabase_url,
                supabase_key
            )

            elapsed = time.time() - start_time
            print(f"Total time: {elapsed:.1f}s")

            return {
                "video_url": video_url,
                "render_time_seconds": elapsed
            }

    except Exception as e:
        print(f"Handler error: {e}")
        return {"error": str(e)}


# Start the RunPod serverless worker
runpod.serverless.start({"handler": handler})
