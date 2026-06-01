#!/usr/bin/env python3
"""
Cry Rename Tool
Renames Relumi cry audio files from PLAY_PV_NNN_FF_00.ext to Showdown names.

Input filename format: PLAY_PV_{mon:03+d}_{form:02d}_{??:02d}.ext
  e.g. PLAY_PV_001_00_00.mp3  → bulbasaur.mp3
       PLAY_PV_003_01_00.mp3  → venusaur-mega.mp3

Processes all audio files recursively under --input-dir, preserving the
relative subdirectory structure under --output-dir.

Usage:
    python rename_cries.py [--input-dir ./input/RelumiCries]
                           [--output-dir ./output/cries]
                           [--pokedex ./References/pokedex.ts]
                           [--dry-run]
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

from pokedex_names import parse_pokedex, resolve_showdown_name

# Audio extensions to process
AUDIO_EXTENSIONS = {".mp3", ".ogg", ".wav"}


def main():
    parser = argparse.ArgumentParser(description="Rename Relumi cry audio files to Showdown names")
    parser.add_argument("--input-dir", type=Path, default=Path("./input/RelumiCries"),
                        help="Root directory to search recursively for PLAY_PV_*.ext files")
    parser.add_argument("--output-dir", type=Path, default=Path("./output/cries"),
                        help="Root directory for renamed output files")
    parser.add_argument("--pokedex", type=Path, default=Path("./References/pokedex.ts"),
                        help="Showdown Pokédex TypeScript source")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print planned renames without writing anything")
    args = parser.parse_args()

    if not args.input_dir.exists():
        print(f"Error: input directory not found: {args.input_dir}", file=sys.stderr)
        sys.exit(1)
    if not args.pokedex.exists():
        print(f"Error: pokedex file not found: {args.pokedex}", file=sys.stderr)
        sys.exit(1)

    print("Parsing pokedex.ts...")
    dex, base_slugs = parse_pokedex(args.pokedex)
    print(f"  {len(dex)} Pokémon")

    # Pattern: PLAY_PV_<mon>_<form>_<extra>.<ext>
    # mon can be 3 or 4 digits (e.g. 001 or 1000)
    pattern = re.compile(r'^PLAY_PV_(\d+)_(\d+)_(\d+)$', re.IGNORECASE)

    # Collect all audio files recursively
    audio_files = sorted(
        p for p in args.input_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
    )

    if not audio_files:
        print(f"No audio files found under {args.input_dir}")
        sys.exit(0)

    processed = 0
    skipped = 0
    collision_counts: dict[Path, int] = {}

    for src in audio_files:
        m = pattern.match(src.stem)
        if not m:
            print(f"  SKIP (unexpected name): {src.relative_to(args.input_dir)}")
            skipped += 1
            continue

        mon = int(m.group(1))
        form = int(m.group(2))
        # group(3) is always 00 in the current dataset — ignored

        showdown_name = resolve_showdown_name(mon, form, False, 0, dex, base_slugs)

        # Preserve relative subdirectory structure
        rel_subdir = src.parent.relative_to(args.input_dir)
        dst_dir = args.output_dir / rel_subdir
        dst = dst_dir / f"{showdown_name}{src.suffix.lower()}"

        # Handle collisions: if two source files would map to the same output
        # name (shouldn't happen with correct dex data, but be safe), append -v2, -v3, …
        if dst in collision_counts:
            collision_counts[dst] += 1
            dst = dst_dir / f"{showdown_name}-v{collision_counts[dst]}{src.suffix.lower()}"
        else:
            collision_counts[dst] = 1

        rel_src = src.relative_to(args.input_dir)
        rel_dst = dst.relative_to(args.output_dir)
        print(f"  {rel_src}  →  {rel_dst}")

        if not args.dry_run:
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        processed += 1

    action = "Would rename" if args.dry_run else "Renamed"
    print(f"\nDone. {action} {processed} files, {skipped} skipped.")


if __name__ == "__main__":
    main()
