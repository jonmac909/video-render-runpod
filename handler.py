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
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

print("=== Video Render Worker Starting ===")

# Overlay files bundled with the worker
SMOKE_OVERLAY = "/app/overlays/smoke_gray.mp4"
EMBERS_OVERLAY = "/app/overlays/embers.mp4"

# FFmpeg settings
FFMPEG_PRESET = "p2"  # NVENC preset (p1=fastest, p7=slowest/best quality) - p2 is fast with good quality
FFMPEG_CQ = "24"  # Constant quality (lower = better, 18-28 typical) - 24 is good balance

# Global flag for encoder selection
USE_NVENC = True

# Check FFmpeg NVENC support on startup
def check_nvenc():
    """Verify FFmpeg has NVENC support."""
    global USE_NVENC
    try:
        result = subprocess.run(
            ['ffmpeg', '-hide_banner', '-encoders'],
            capture_output=True, text=True, timeout=10
        )
        if 'h264_nvenc' in result.stdout:
            print("✓ FFmpeg NVENC support confirmed")
            USE_NVENC = True
            return True
        else:
            print("✗ WARNING: h264_nvenc not found, falling back to libx264 (slower)")
            print("Available h264 encoders:", [l for l in result.stdout.split('\n') if 'h264' in l.lower()])
            USE_NVENC = False
            return False
    except Exception as e:
        print(f"✗ FFmpeg check failed: {e}, falling back to libx264")
        USE_NVENC = False
        return False

# Check overlay files exist
def check_overlays():
    """Verify overlay files are present."""
    smoke_ok = os.path.exists(SMOKE_OVERLAY)
    embers_ok = os.path.exists(EMBERS_OVERLAY)
    print(f"Overlay smoke_gray.mp4: {'✓' if smoke_ok else '✗'} {SMOKE_OVERLAY}")
    print(f"Overlay embers.mp4: {'✓' if embers_ok else '✗'} {EMBERS_OVERLAY}")
    if smoke_ok:
        smoke_size = os.path.getsize(SMOKE_OVERLAY)
        print(f"  smoke_gray.mp4 size: {smoke_size / 1024:.1f} KB")
    if embers_ok:
        embers_size = os.path.getsize(EMBERS_OVERLAY)
        print(f"  embers.mp4 size: {embers_size / 1024:.1f} KB")
    return smoke_ok and embers_ok

# Run startup checks
check_nvenc()
check_overlays()
print(f"Encoder: {'h264_nvenc (GPU)' if USE_NVENC else 'libx264 (CPU fallback)'}")
print("=== Worker Ready ===")
print()


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
    """Upload file to Supabase storage using streaming (memory efficient)."""
    try:
        file_size = os.path.getsize(file_path)
        print(f"Uploading to Supabase: {storage_path} ({file_size / 1024 / 1024:.1f} MB)")

        # Supabase storage upload endpoint
        upload_url = f"{supabase_url}/storage/v1/object/{bucket}/{storage_path}"

        headers = {
            'Authorization': f'Bearer {supabase_key}',
            'Content-Type': 'video/mp4',
            'x-upsert': 'true',
            'Content-Length': str(file_size)
        }

        # Stream upload to avoid loading entire file into memory
        with open(file_path, 'rb') as f:
            response = requests.post(upload_url, headers=headers, data=f, timeout=600)

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


def update_render_job(supabase_url: str, supabase_key: str, job_id: str,
                      status: str, progress: int, message: str,
                      video_url: str = None, error: str = None) -> bool:
    """Update render job status in Supabase database."""
    try:
        if not job_id:
            print("No render_job_id provided, skipping status update")
            return False

        print(f"Updating render job {job_id}: {status} ({progress}%)")

        update_data = {
            "status": status,
            "progress": progress,
            "message": message,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        }

        if video_url:
            update_data["video_url"] = video_url
        if error:
            update_data["error"] = error

        url = f"{supabase_url}/rest/v1/render_jobs?id=eq.{job_id}"
        headers = {
            'Authorization': f'Bearer {supabase_key}',
            'apikey': supabase_key,
            'Content-Type': 'application/json',
            'Prefer': 'return=minimal'
        }

        response = requests.patch(url, headers=headers, json=update_data, timeout=30)

        if response.status_code not in [200, 204]:
            print(f"Failed to update job status: {response.status_code} - {response.text}")
            return False

        print(f"Job {job_id} updated successfully")
        return True

    except Exception as e:
        print(f"Error updating job status: {e}")
        return False


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


def get_encoder_args():
    """Get encoder-specific FFmpeg arguments."""
    if USE_NVENC:
        return ['-c:v', 'h264_nvenc', '-preset', FFMPEG_PRESET, '-cq', FFMPEG_CQ]
    else:
        # CPU fallback with libx264
        return ['-c:v', 'libx264', '-preset', 'fast', '-crf', FFMPEG_CQ]


def render_video_gpu(
    image_paths: list,
    timings: list,
    audio_path: str,
    output_path: str,
    apply_effects: bool = True
) -> bool:
    """
    Render video using GPU acceleration (NVENC) with CPU fallback.

    Args:
        image_paths: List of local image file paths
        timings: List of {startSeconds, endSeconds} for each image
        audio_path: Local path to audio file
        output_path: Output video path
        apply_effects: Whether to apply smoke + embers overlay

    Returns:
        True if successful
    """
    encoder_args = get_encoder_args()
    encoder_name = "NVENC" if USE_NVENC else "libx264"

    try:
        # Create concat file for images
        concat_file = create_concat_file(image_paths, timings)

        if apply_effects:
            # Two-pass approach: render raw, then apply effects
            # This is more memory efficient than single complex filter

            # Pass 1: Images to raw video
            raw_video = tempfile.mktemp(suffix='_raw.mp4')

            cmd_raw = [
                'ffmpeg', '-y',
                '-f', 'concat', '-safe', '0', '-i', concat_file,
                '-vf', 'scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=24',
                *encoder_args,
                '-pix_fmt', 'yuv420p',
                raw_video
            ]

            print(f"Pass 1: Rendering raw video ({encoder_name})...")
            result = subprocess.run(cmd_raw, capture_output=True, text=True, timeout=3600)  # 60 min timeout
            if result.returncode != 0:
                print(f"Pass 1 failed: {result.stderr}")
                raise Exception(f"Raw render failed: {result.stderr[-500:]}")

            # Pass 2: Apply smoke + embers overlay
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
                *encoder_args,
                '-pix_fmt', 'yuv420p',
                '-c:a', 'aac',
                '-b:a', '192k',
                '-shortest',
                output_path
            ]

            print(f"Pass 2: Applying effects ({encoder_name})...")
            result = subprocess.run(cmd_effects, capture_output=True, text=True, timeout=3600)  # 60 min timeout

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
                *encoder_args,
                '-pix_fmt', 'yuv420p',
                '-c:a', 'aac',
                '-b:a', '192k',
                '-shortest',
                output_path
            ]

            print(f"Rendering video without effects ({encoder_name})...")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)  # 60 min timeout

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
        raise Exception("Render timed out after 60 minutes")
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
    render_job_id = job_input.get("render_job_id")  # Railway job ID for status updates

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

    # Early validation: check overlays if effects requested
    if apply_effects:
        if not os.path.exists(SMOKE_OVERLAY) or not os.path.exists(EMBERS_OVERLAY):
            return {"error": "Overlay files missing - cannot apply effects"}

    print(f"Starting video render: {len(image_urls)} images, effects={apply_effects}")
    start_time = time.time()

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            # Download all images in parallel (20 concurrent downloads)
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
            with ThreadPoolExecutor(max_workers=20) as executor:
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

            # Download audio (larger file, keep sequential, longer timeout)
            audio_path = os.path.join(temp_dir, "audio.wav")
            if not download_file(audio_url, audio_path, timeout=300):
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

            # Update job status in Supabase (so frontend knows it's done even if Railway crashed)
            update_render_job(
                supabase_url, supabase_key, render_job_id,
                status="complete",
                progress=100,
                message=f"Video rendered successfully (GPU: {elapsed:.1f}s)",
                video_url=video_url
            )

            return {
                "video_url": video_url,
                "render_time_seconds": elapsed
            }

    except Exception as e:
        print(f"Handler error: {e}")
        # Update job status with error
        update_render_job(
            supabase_url, supabase_key, render_job_id,
            status="failed",
            progress=0,
            message="GPU render failed",
            error=str(e)
        )
        return {"error": str(e)}


# Start the RunPod serverless worker
runpod.serverless.start({"handler": handler})
