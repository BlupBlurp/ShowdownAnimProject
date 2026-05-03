#!/usr/bin/env python3
"""
Sprite Recording Script — KDE Wayland
Repeatedly records a fixed screen region using gpu-screen-recorder,
injecting a keypress via evdev/uinput before each take.

Usage:
    python record_sprites.py [--output-dir ./input] [--start-index N] [--count N]
                             [--order ./References/video_order.txt]

Files are named NNNN_FF.mp4 where NNNN=monsNo and FF=formNo, derived from
References/video_order.txt. Gender variants (3-value entries) and 4-value
Variant entries are skipped — they share the same recording as the base form.

Each iteration:
  1. Press Z via uinput (evdev) — advances to next Pokémon
  2. Wait KEYPRESS_DELAY seconds for the game to register
  3. Record RECORD_SECONDS via gpu-screen-recorder
  4. Save to output_dir/NNNN_FF.mp4
  5. Repeat

One-time setup:
    sudo usermod -aG input $USER   # then log out and back in
"""

import argparse
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import evdev
from evdev import UInput, ecodes as e

# ─── CONFIG ──────────────────────────────────────────────────────────────────

CAPTURE_W      = 1057
CAPTURE_H      = 912

# DP-1 (primary, 2560x1440) sits at compositor offset +1920+0
# DP-2 (1920x1080) sits at compositor offset +0+213
MONITOR_X      = 1920   # DP-1 left edge in compositor space
MONITOR_Y      = 0      # DP-1 top edge in compositor space
SCREEN_W       = 2560
SCREEN_H       = 1440
CAPTURE_X      = MONITOR_X + (SCREEN_W - CAPTURE_W) // 2   # 2671
CAPTURE_Y      = MONITOR_Y + (SCREEN_H - CAPTURE_H) // 2   # 264

RECORD_FPS     = 60
RECORD_SECONDS = 4
KEYPRESS_DELAY = 0.3   # seconds between Z press and recording start

# ─── VIDEO ORDER PARSING ─────────────────────────────────────────────────────

def parse_recording_order(path: Path) -> list[tuple[int, int, int, int]]:
    """
    Parse video_order.txt and return a list of (monsNo, formNo, genderVariant, variantIdx)
    for every entry that needs its own recording.

    Rules:
    - 2-value (monsNo, formNo)                      → gender=0, variant=0
    - 3-value (monsNo, formNo, gender)               → variant=0
    - 4-value (monsNo, formNo, -1, variantIdx)       → gender=0, variant=variantIdx
    """
    order = []

    for raw_line in path.read_text().splitlines():
        line = raw_line.split(";")[0].strip()
        if not line:
            continue
        line = line.lstrip("(").rstrip(")")
        parts = [p.strip() for p in line.split(",")]
        try:
            nums = [int(p) for p in parts if p != ""]
        except ValueError:
            continue

        if len(nums) == 2:
            order.append((nums[0], nums[1], 0, 0))
        elif len(nums) == 3:
            order.append((nums[0], nums[1], nums[2], 0))
        elif len(nums) == 4:
            # (monsNo, formNo, -1, variantIdx)
            order.append((nums[0], nums[1], 0, nums[3]))

    return order


# ─── KEYPRESS via evdev/uinput ────────────────────────────────────────────────

def press_z():
    """Inject a Z keypress via /dev/uinput. Requires user to be in 'input' group."""
    try:
        ui = UInput({e.EV_KEY: [e.KEY_Z]}, name="sprite-recorder")
        ui.write(e.EV_KEY, e.KEY_Z, 1)  # key down
        ui.syn()
        time.sleep(0.05)
        ui.write(e.EV_KEY, e.KEY_Z, 0)  # key up
        ui.syn()
        ui.close()
        print("  Pressed Z")
    except PermissionError:
        print("  ERROR: Cannot open /dev/uinput.")
        print("  Fix: sudo usermod -aG input $USER  then log out and back in.")
        sys.exit(1)


# ─── RECORDING ───────────────────────────────────────────────────────────────

def record_take(out_path: Path) -> bool:
    """
    Press Z, then record RECORD_SECONDS of the configured screen region.
    gpu-screen-recorder is stopped via SIGINT after the duration elapses.
    """
    press_z()
    time.sleep(KEYPRESS_DELAY)

    cmd = [
        "gpu-screen-recorder",
        "-w", "region",
        "-region", f"{CAPTURE_W}x{CAPTURE_H}+{CAPTURE_X}+{CAPTURE_Y}",
        "-f", str(RECORD_FPS),
        "-k", "av1",
        "-q", "very_high",
        "-c", "mp4",
        "-o", str(out_path),
    ]
    print(f"  $ {' '.join(cmd)}")

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(RECORD_SECONDS)
    proc.send_signal(signal.SIGINT)
    proc.wait()

    if not out_path.exists() or out_path.stat().st_size == 0:
        print(f"  WARNING: output file missing or empty: {out_path}")
        return False
    return True


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Looping sprite screen recorder (KDE Wayland)")
    parser.add_argument("--output-dir", type=Path, default=Path("./input"),
                        help="Directory to save recordings (default: ./input)")
    parser.add_argument("--order", type=Path, default=Path("./References/video_order.txt"),
                        help="Path to video_order.txt")
    parser.add_argument("--start-index", type=int, default=0,
                        help="Index into the order list to start from (default: 0)")
    parser.add_argument("--count", type=int, default=0,
                        help="Number of takes (0 = record all remaining, Ctrl+C to stop early)")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    print("Parsing video order...")
    order = parse_recording_order(args.order)
    total = len(order)
    print(f"  {total} unique recordings needed")

    start = args.start_index
    end = total if args.count == 0 else min(total, start + args.count)
    batch = order[start:end]

    print(f"Capture region : {CAPTURE_W}x{CAPTURE_H}+{CAPTURE_X}+{CAPTURE_Y}")
    print(f"Output dir     : {args.output_dir}")
    print(f"Recording      : entries {start}–{end - 1} of {total - 1} ({len(batch)} takes)")
    print("Press Ctrl+C to stop.\n")

    print("Starting in ", end="", flush=True)
    for i in range(3, 0, -1):
        print(f"{i}...", end=" ", flush=True)
        time.sleep(1)
    print("Go!\n")

    done = 0
    try:
        for idx, (mon, form, gender, variant) in enumerate(batch):
            gender_suffix = f"_g{gender}" if gender > 0 else ""
            variant_suffix = f"_v{variant}" if variant > 0 else ""
            out_path = args.output_dir / f"{mon:04d}_{form:02d}{gender_suffix}{variant_suffix}.mp4"
            label = f"[{start + idx}/{total - 1}] mon={mon} form={form} gender={gender} variant={variant}"
            print(f"{label} → {out_path.name}")
            success = record_take(out_path)
            if success:
                print(f"  Saved: {out_path}")
            else:
                print(f"  Failed.")
            done += 1
    except KeyboardInterrupt:
        print(f"\nStopped after {done} take(s).")
        sys.exit(0)

    print(f"\nDone. {done} take(s) recorded.")


if __name__ == "__main__":
    main()
