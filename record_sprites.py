#!/usr/bin/env python3
"""
Sprite Recording Script — KDE Wayland
Repeatedly records a fixed screen region using gpu-screen-recorder,
injecting a keypress via evdev/uinput before each take.

Usage:
    python record_sprites.py [--output-dir ./input] [--prefix sprite] [--count N]

Each iteration:
  1. Press Z via uinput (evdev) — advances to next Pokémon
  2. Wait KEYPRESS_DELAY seconds for the game to register
  3. Record RECORD_SECONDS via gpu-screen-recorder
  4. Save to output_dir/prefix_NNNN.mp4
  5. Repeat

One-time setup:
    sudo usermod -aG input $USER   # then log out and back in
"""

import argparse
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
RECORD_SECONDS = 5
KEYPRESS_DELAY = 0.3   # seconds between Z press and recording start

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
        "-w", f"{CAPTURE_W}x{CAPTURE_H}+{CAPTURE_X}+{CAPTURE_Y}",
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
    parser.add_argument("--prefix", type=str, default="sprite",
                        help="Filename prefix for recordings (default: sprite)")
    parser.add_argument("--count", type=int, default=0,
                        help="Number of takes (0 = infinite, Ctrl+C to stop)")
    parser.add_argument("--start-index", type=int, default=0,
                        help="Starting index for output filenames (default: 0)")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Capture region : {CAPTURE_W}x{CAPTURE_H}+{CAPTURE_X}+{CAPTURE_Y}")
    print(f"Output dir     : {args.output_dir}")
    print(f"Takes          : {'infinite' if args.count == 0 else args.count}")
    print("Press Ctrl+C to stop.\n")

    print("Starting in ", end="", flush=True)
    for i in range(3, 0, -1):
        print(f"{i}...", end=" ", flush=True)
        time.sleep(1)
    print("Go!\n")

    take = args.start_index
    try:
        while True:
            out_path = args.output_dir / f"{args.prefix}_{take:04d}.mp4"
            print(f"[Take {take}] Recording -> {out_path}")
            success = record_take(out_path)
            if success:
                print(f"[Take {take}] Saved: {out_path}")
            else:
                print(f"[Take {take}] Failed.")
            take += 1
            if args.count > 0 and take >= args.start_index + args.count:
                break
    except KeyboardInterrupt:
        print(f"\nStopped after {take - args.start_index} take(s).")
        sys.exit(0)

    print(f"\nDone. {take - args.start_index} take(s) recorded.")


if __name__ == "__main__":
    main()
