#!/usr/bin/env python3
"""
Sprite Animation Pipeline
Usage: python sprite_pipeline.py <input_video> [--output-dir ./output] [--greenscreen] [--fuzz 10]

source /mnt/SATA_SSD/Projects/BDSP_Mods/ShowdownAnimProject/venv/bin/activate.fish

Stages:
    1. Extract frames from video
    2. Find loop point (two identical/similar frames)
    3. Remove green screen (optional, after loop detection for better frame matching)
    4. Crop to content bounds
    5. Convert to GIF
"""

import argparse
import subprocess
import sys
from pathlib import Path
from PIL import Image
import numpy as np
import shutil
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
import os

# ─── CONFIG ──────────────────────────────────────────────────────────────────

GREEN_COLOR = "0x00FF00"   # Adjust to your actual green screen hex
CHROMA_SIMILARITY = 0.15   # ffmpeg chromakey similarity (0.0–1.0) — unused, PIL path is active
CHROMA_BLEND = 0.05        # ffmpeg chromakey blend — unused, PIL path is active
GIF_FPS = 33.33            # Output GIF framerate

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def run(cmd, log: list, check=True):
    log.append(f"  $ {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def extract_frames(video_path: Path, out_dir: Path, log: list, fps=None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    fps_filter = f"fps={fps}," if fps else ""
    run([
        "ffmpeg", "-i", str(video_path),
        "-vf", f"{fps_filter}scale=iw:ih",
        str(out_dir / "frame_%04d.png"),
        "-y"
    ], log)
    return sorted(out_dir.glob("frame_*.png"))


def find_loop_point(frames: list[Path], threshold: float, log: list, min_loop_frames: int = 20) -> tuple[int, int] | None:
    log.append(f"  Scanning {len(frames)} frames for loop point (min_loop={min_loop_frames}, threshold={threshold})...")
    # Pre-load all frames into memory as numpy arrays to avoid repeated disk I/O
    arrays = [np.array(Image.open(f).convert("RGB"), dtype=np.float32) for f in frames]
    n = len(arrays)

    # ── Strategy 1: anchor start=0, find best-matching end frame ──────────────
    # The clip should start at the loop start. Scan all candidate end frames
    # (beyond min_loop_frames) and pick the one closest to frame 0.
    best_end = None
    best_diff = float("inf")
    for end in range(min_loop_frames, n):
        diff = float(np.mean(np.abs(arrays[end] - arrays[0])))
        if diff < best_diff:
            best_diff = diff
            best_end = end

    if best_end is not None and best_diff <= threshold:
        loop_len = best_end  # start=0, so length = end - 0
        log.append(f"  Loop point found: frame 0 -> frame {best_end} ({loop_len} frames, diff={best_diff:.3f})")
        return 0, best_end

    # ── Strategy 2: full pair search, pick the pair with lowest diff ──────────
    # Used when the clip doesn't start exactly at the loop start (e.g. there are
    # a few lead-in frames). Find the globally best-matching pair.
    log.append(f"  Frame 0 best match diff={best_diff:.3f} exceeds threshold. Trying full pair search...")
    best_start, best_end = None, None
    best_diff = float("inf")
    for end in range(min_loop_frames, n):
        for start in range(0, end - min_loop_frames + 1):
            diff = float(np.mean(np.abs(arrays[end] - arrays[start])))
            if diff < best_diff:
                best_diff = diff
                best_start, best_end = start, end

    if best_start is not None and best_diff <= threshold:
        loop_len = best_end - best_start
        log.append(f"  Loop point found: frame {best_start} -> frame {best_end} ({loop_len} frames, diff={best_diff:.3f})")
        return best_start, best_end

    # ── No loop found ──────────────────────────────────────────────────────────
    if best_start is not None:
        log.append(
            f"  WARNING: Best match was frame {best_start}→{best_end} with diff={best_diff:.3f}, "
            f"exceeds threshold={threshold}. Use --loop-threshold {best_diff:.1f} to accept it."
        )
    else:
        log.append("  WARNING: No loop point found. Using full video.")
    return None


def _remove_greenscreen_single(args):
    frame, out_dir, fuzz = args
    img = Image.open(frame).convert("RGBA")
    data = np.array(img)
    r, g, b = data[:,:,0].astype(int), data[:,:,1].astype(int), data[:,:,2].astype(int)
    mask = (g - r > fuzz) & (g - b > fuzz)
    data[mask, 3] = 0
    out = out_dir / (frame.stem + ".png")
    Image.fromarray(data).save(out)
    return out


def remove_greenscreen_pil(frames: list[Path], out_dir: Path, log: list, fuzz=30) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    worker_count = min(os.cpu_count() or 4, len(frames))
    args = [(f, out_dir, fuzz) for f in frames]
    with ThreadPoolExecutor(max_workers=worker_count) as ex:
        results = list(ex.map(_remove_greenscreen_single, args))
    return results


def _get_frame_bbox(frame: Path):
    img = Image.open(frame).convert("RGBA")
    return img.split()[3].getbbox()


def crop_to_content(frames: list[Path], out_dir: Path, log: list) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    worker_count = min(os.cpu_count() or 4, len(frames))

    with ThreadPoolExecutor(max_workers=worker_count) as ex:
        bboxes = list(ex.map(_get_frame_bbox, frames))

    valid = [b for b in bboxes if b is not None]
    if not valid:
        log.append("  WARNING: No content found for crop, using full frame.")
        return frames

    min_x = min(b[0] for b in valid)
    min_y = min(b[1] for b in valid)
    max_x = max(b[2] for b in valid)
    max_y = max(b[3] for b in valid)
    bbox = (min_x, min_y, max_x, max_y)
    log.append(f"  Crop box: {bbox}")

    def crop_one(frame):
        img = Image.open(frame).convert("RGBA")
        out = out_dir / frame.name
        img.crop(bbox).save(out)
        return out

    with ThreadPoolExecutor(max_workers=worker_count) as ex:
        results = list(ex.map(crop_one, frames))
    return results


def to_palette_transparent(img: Image.Image) -> Image.Image:
    """Quantize RGBA image to palette mode, preserving transparency at index 0.

    Strategy:
      - Replace transparent pixels with a neutral grey before quantizing so
        the palette is not polluted by the original green-screen colour.
      - Quantize to 255 colours (leaving index 0 free for transparency).
      - Shift all palette indices up by 1 and force transparent pixels to 0.
    """
    alpha = np.array(img.split()[3])
    opaque_mask = alpha >= 128

    # Composite transparent pixels onto mid-grey so they don't skew the palette
    matte = Image.new("RGBA", img.size, (128, 128, 128, 255))
    matte.paste(img, mask=img.split()[3])
    rgb = matte.convert("RGB")

    # Quantize to 255 colours — indices will be 0..254
    quantized = rgb.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette()  # 768 ints (R,G,B × 256 entries)

    # Reserve index 0 for transparency: shift every index up by 1
    # Use int16 to avoid uint8 overflow before clipping
    pix = np.array(quantized, dtype=np.int16) + 1
    pix[~opaque_mask] = 0          # transparent pixels → index 0
    pix = np.clip(pix, 0, 255).astype(np.uint8)

    # Build new palette: entry 0 = black (will be transparent), entries 1..255 = quantised colours
    new_palette = [0, 0, 0] + palette[: 255 * 3]

    result = Image.fromarray(pix, mode="P")
    result.putpalette(new_palette)
    return result


def frames_to_gif(frames: list[Path], out_path: Path, log: list, fps=GIF_FPS):
    delay_cs = round(100 / fps)  # 100/33.33 = 3cs = 33.33fps
    duration_ms = delay_cs * 10
    log.append(f"  GIF delay: {delay_cs}cs per frame ({100/delay_cs:.2f} fps effective)")

    images = [Image.open(f).convert("RGBA") for f in frames]

    worker_count = min(os.cpu_count() or 4, len(images))
    with ThreadPoolExecutor(max_workers=worker_count) as ex:
        frames_p = list(ex.map(to_palette_transparent, images))

    tmp_gif = out_path.with_suffix(".tmp.gif")
    frames_p[0].save(
        tmp_gif,
        save_all=True,
        append_images=frames_p[1:],
        loop=0,
        duration=duration_ms,
        optimize=False,
        transparency=0,
        disposal=2,
    )

    if shutil.which("gifsicle"):
        run(["gifsicle", "--optimize=3", "--loop", f"--delay={delay_cs}", str(tmp_gif), "-o", str(out_path)], log)
        tmp_gif.unlink()
    else:
        tmp_gif.rename(out_path)
        log.append("  gifsicle not found, skipping optimization")


# ─── MAIN PIPELINE ───────────────────────────────────────────────────────────

def process(video_path: Path, output_dir: Path, use_greenscreen: bool, fuzz: int, loop_threshold: float, min_loop_frames: int) -> tuple[Path, list[str]]:
    """Run the full pipeline for one video. Returns (gif_path, log_lines).

    All output is collected into log_lines rather than printed directly, so
    that parallel runs don't interleave their output. The caller is responsible
    for printing the log once the video is done.
    """
    log: list[str] = []
    name = video_path.stem
    work_dir = output_dir / name

    log.append(f"\n{'='*60}")
    log.append(f"Processing: {video_path.name}")
    log.append(f"{'='*60}")

    log.append("\n[1] Extracting frames...")
    raw_dir = work_dir / "1_raw"
    frames = extract_frames(video_path, raw_dir, log, fps=GIF_FPS)
    log.append(f"  Extracted {len(frames)} frames")

    log.append("\n[2] Finding loop point...")
    loop = find_loop_point(frames, loop_threshold, log, min_loop_frames=min_loop_frames)
    if loop:
        start, end = loop
        frames = frames[start:end]
        log.append(f"  Trimmed to {len(frames)} frames")

    if use_greenscreen:
        log.append("\n[3] Removing green screen...")
        chroma_dir = work_dir / "2_chroma"
        frames = remove_greenscreen_pil(frames, chroma_dir, log, fuzz=fuzz)

    log.append("\n[4] Cropping to content...")
    crop_dir = work_dir / "3_cropped"
    frames = crop_to_content(frames, crop_dir, log)

    log.append("\n[5] Converting to GIF...")
    gif_path = output_dir / f"{name}.gif"
    frames_to_gif(frames, gif_path, log)
    log.append(f"  Saved: {gif_path}")

    return gif_path, log


def _process_worker(args):
    """Top-level wrapper for ProcessPoolExecutor (must be picklable)."""
    video_path, output_dir, use_greenscreen, fuzz, loop_threshold, min_loop_frames = args
    return process(video_path, output_dir, use_greenscreen, fuzz, loop_threshold, min_loop_frames)


def main():
    parser = argparse.ArgumentParser(description="Sprite animation pipeline")
    parser.add_argument("inputs", nargs="+", type=Path, help="Input video file(s)")
    parser.add_argument("--output-dir", type=Path, default=Path("./output"))
    parser.add_argument("--greenscreen", action="store_true", help="Apply chroma key removal")
    parser.add_argument("--fuzz", type=int, default=30, help="Green screen fuzz tolerance (0-255)")
    parser.add_argument("--loop-threshold", type=float, default=5)
    parser.add_argument("--min-loop-frames", type=int, default=20)
    parser.add_argument("--workers", type=int, default=None,
                        help="Max parallel videos (default: CPU count / 2)")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    videos = [v for v in args.inputs if v.exists() or print(f"ERROR: {v} not found", file=sys.stderr)]

    if len(videos) == 1:
        _, log = process(videos[0], args.output_dir, args.greenscreen, args.fuzz, args.loop_threshold, args.min_loop_frames)
        print("\n".join(log))
    else:
        # Each video process() already uses threads internally, so limit outer parallelism
        # to avoid thrashing (default: half of CPU count)
        max_workers = args.workers or max(1, (os.cpu_count() or 2) // 2)
        print(f"\nProcessing {len(videos)} videos with up to {max_workers} parallel workers...")
        worker_args = [
            (v, args.output_dir, args.greenscreen, args.fuzz, args.loop_threshold, args.min_loop_frames)
            for v in videos
        ]
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_process_worker, a): a[0] for a in worker_args}
            for fut in as_completed(futures):
                vid = futures[fut]
                try:
                    _, log = fut.result()
                    print("\n".join(log), flush=True)
                except Exception as e:
                    print(f"ERROR processing {vid}: {e}", file=sys.stderr)

    print("\nDone.")


if __name__ == "__main__":
    main()
