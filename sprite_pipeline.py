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
    6. [PLACEHOLDER] Rename from monID_formID to Showdown name
    7. TODO: Scale gifs down based on existing showdown ones
"""

import argparse
import subprocess
import sys
from pathlib import Path
from PIL import Image
import numpy as np
import shutil

# ─── CONFIG ──────────────────────────────────────────────────────────────────

GREEN_COLOR = "0x00FF00"   # Adjust to your actual green screen hex
CHROMA_SIMILARITY = 0.15   # ffmpeg chromakey similarity (0.0–1.0)
CHROMA_BLEND = 0.05        # ffmpeg chromakey blend
GIF_FPS = 33.33            # Output GIF framerate

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def run(cmd, check=True):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def extract_frames(video_path: Path, out_dir: Path, fps=None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    fps_filter = f"fps={fps}," if fps else ""
    run([
        "ffmpeg", "-i", str(video_path),
        "-vf", f"{fps_filter}scale=iw:ih",
        str(out_dir / "frame_%04d.png"),
        "-y"
    ])
    return sorted(out_dir.glob("frame_*.png"))


def frame_diff(img_a: Path, img_b: Path) -> float:
    a = np.array(Image.open(img_a).convert("RGB"), dtype=float)
    b = np.array(Image.open(img_b).convert("RGB"), dtype=float)
    if a.shape != b.shape:
        return float("inf")
    return float(np.mean(np.abs(a - b)))


def find_loop_point(frames: list[Path], threshold: float, min_loop_frames: int = 20) -> tuple[int, int] | None:
    print(f"  Scanning {len(frames)} frames for loop point (min_loop={min_loop_frames})...")
    # Pre-load all frames into memory as numpy arrays to avoid repeated disk I/O
    arrays = [np.array(Image.open(f).convert("RGB"), dtype=np.float32) for f in frames]

    # Find the shortest loop: scan all pairs, pick smallest (end - start) within threshold.
    # min_loop_frames prevents trivially-short matches (e.g. two identical background frames).
    best_start, best_end = None, None
    best_len = float("inf")
    for end in range(1, len(arrays)):
        for start in range(0, end):
            loop_len = end - start
            if loop_len < min_loop_frames:
                continue  # too short to be a real animation loop
            if loop_len >= best_len:
                continue  # already found a shorter valid loop
            diff = float(np.mean(np.abs(arrays[end] - arrays[start])))
            if diff <= threshold:
                best_start, best_end = start, end
                best_len = loop_len
    if best_start is not None:
        print(f"  Loop point found: frame {best_start} -> frame {best_end} ({best_len} frames, diff<={threshold})")
        return best_start, best_end
    print(f"  WARNING: No loop point found within threshold={threshold}. Using full video.")
    return None


def remove_greenscreen_pil(frames: list[Path], out_dir: Path, fuzz=30) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    result_frames = []
    for frame in frames:
        img = Image.open(frame).convert("RGBA")
        data = np.array(img)
        r, g, b = data[:,:,0].astype(int), data[:,:,1].astype(int), data[:,:,2].astype(int)
        mask = (g - r > fuzz) & (g - b > fuzz)
        data[mask, 3] = 0
        out = out_dir / (frame.stem + ".png")
        Image.fromarray(data).save(out)
        result_frames.append(out)
    return result_frames


def crop_to_content(frames: list[Path], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    min_x, min_y = float("inf"), float("inf")
    max_x, max_y = 0, 0
    for frame in frames:
        img = Image.open(frame).convert("RGBA")
        bbox = img.split()[3].getbbox()
        if bbox is None:
            continue
        min_x = min(min_x, bbox[0])
        min_y = min(min_y, bbox[1])
        max_x = max(max_x, bbox[2])
        max_y = max(max_y, bbox[3])
    if min_x == float("inf"):
        print("  WARNING: No content found for crop, using full frame.")
        return frames
    bbox = (int(min_x), int(min_y), int(max_x), int(max_y))
    print(f"  Crop box: {bbox}")
    result_frames = []
    for frame in frames:
        img = Image.open(frame).convert("RGBA")
        out = out_dir / frame.name
        img.crop(bbox).save(out)
        result_frames.append(out)
    return result_frames


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


def frames_to_gif(frames: list[Path], out_path: Path, fps=GIF_FPS):
    delay_cs = round(100 / fps)  # 100/33.33 = 3cs = 33.33fps
    duration_ms = delay_cs * 10
    print(f"  GIF delay: {delay_cs}cs per frame ({100/delay_cs:.2f} fps effective)")

    images = [Image.open(f).convert("RGBA") for f in frames]
    frames_p = [to_palette_transparent(img) for img in images]

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
        run(["gifsicle", "--optimize=3", "--loop", f"--delay={delay_cs}", str(tmp_gif), "-o", str(out_path)])
        tmp_gif.unlink()
    else:
        tmp_gif.rename(out_path)
        print("  gifsicle not found, skipping optimization")


# ─── Showdown Rename (delegated to rename_sprites.py) ────────────────────────
# Call rename_sprites.py after the pipeline to rename and resize all output GIFs.


# ─── MAIN PIPELINE ───────────────────────────────────────────────────────────

def process(video_path: Path, output_dir: Path, use_greenscreen: bool, fuzz: int, loop_threshold: float, min_loop_frames: int):
    name = video_path.stem
    work_dir = output_dir / name
    print(f"\n{'='*60}")
    print(f"Processing: {video_path.name}")
    print(f"{'='*60}")

    print("\n[1] Extracting frames...")
    raw_dir = work_dir / "1_raw"
    frames = extract_frames(video_path, raw_dir, fps=GIF_FPS)
    print(f"  Extracted {len(frames)} frames")

    print("\n[2] Finding loop point...")
    loop = find_loop_point(frames, loop_threshold, min_loop_frames=min_loop_frames)
    if loop:
        start, end = loop
        frames = frames[start:end]
        print(f"  Trimmed to {len(frames)} frames")

    if use_greenscreen:
        print("\n[3] Removing green screen...")
        chroma_dir = work_dir / "2_chroma"
        frames = remove_greenscreen_pil(frames, chroma_dir, fuzz=fuzz)

    print("\n[4] Cropping to content...")
    crop_dir = work_dir / "3_cropped"
    frames = crop_to_content(frames, crop_dir)

    print("\n[5] Converting to GIF...")
    gif_path = output_dir / f"{name}.gif"
    frames_to_gif(frames, gif_path)
    print(f"  Saved: {gif_path}")

    print("\n[6] Rename (run rename_sprites.py separately to rename and resize all output GIFs).")
    print(f"  Output filename: {name}.gif  (use: python rename_sprites.py --output-dir {output_dir})")

    return gif_path


def main():
    parser = argparse.ArgumentParser(description="Sprite animation pipeline")
    parser.add_argument("inputs", nargs="+", type=Path, help="Input video file(s)")
    parser.add_argument("--output-dir", type=Path, default=Path("./output"))
    parser.add_argument("--greenscreen", action="store_true", help="Apply chroma key removal")
    parser.add_argument("--fuzz", type=int, default=30, help="Green screen fuzz tolerance (0-255)")
    parser.add_argument("--loop-threshold", type=float, default=5,
                        help="Max mean pixel diff to consider frames identical (default: 5)")
    parser.add_argument("--min-loop-frames", type=int, default=20,
                        help="Minimum number of frames a loop must contain (default: 50)")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    for video in args.inputs:
        if not video.exists():
            print(f"ERROR: {video} not found", file=sys.stderr)
            continue
        process(video, args.output_dir, args.greenscreen, args.fuzz, args.loop_threshold, args.min_loop_frames)

    print("\nDone.")


if __name__ == "__main__":
    main()
