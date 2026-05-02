#!/usr/bin/env python3
"""
Sprite Rename & Resize Tool
Renames output GIFs from monsNo_formNo.gif to Showdown names,
and resizes them to match the reference GIF dimensions from References/ani/.

Usage:
    python rename_sprites.py [--output-dir ./output] [--ani-dir ./References/ani]
                             [--order ./References/video_order.txt]
                             [--pokedex ./References/pokedex.ts]
                             [--scale 1.0] [--dry-run]

The script:
  1. Parses video_order.txt to map (monsNo, formNo, genderVariant) → sequential index
  2. Parses pokedex.ts to map num → ordered list of Showdown names
  3. For each NNNN_FF.gif in output-dir, resolves the Showdown name
  4. Finds the matching reference GIF in ani-dir
  5. Resizes our GIF so its larger side matches the reference's larger side,
     then applies an optional --scale multiplier on top
  6. Renames the file
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

from PIL import Image
import numpy as np


# ─── PARSE video_order.txt ───────────────────────────────────────────────────

def parse_video_order(path: Path) -> list[tuple[int, int, bool, int]]:
    """
    Returns a list of (monsNo, formNo, is_female, variantIdx) for every valid entry.

    - 2-value (monsNo, formNo)                → female=False, variant=0
    - 3-value (monsNo, formNo, gender)         → female=(gender==1), variant=0
    - 4-value (monsNo, formNo, -1, variantIdx) → female=False, variant=variantIdx
    """
    entries = []
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
            entries.append((nums[0], nums[1], False, 0))
        elif len(nums) == 3:
            entries.append((nums[0], nums[1], nums[2] == 1, 0))
        elif len(nums) == 4:
            entries.append((nums[0], nums[1], False, nums[3]))

    return entries


# ─── PARSE pokedex.ts ────────────────────────────────────────────────────────

def _showdown_name(raw: str) -> str:
    """Convert a Showdown display name to its filename slug (lowercase, spaces→hyphens)."""
    # Remove special unicode chars that appear in some names (e.g. Farfetch'd apostrophe)
    name = raw.strip().strip('"').strip("'")
    # Lowercase, replace spaces with hyphens, strip non-alphanumeric except hyphens
    name = name.lower()
    name = name.replace(" ", "-")
    # Remove characters that don't belong in filenames (apostrophes, dots, colons, etc.)
    name = re.sub(r"[^a-z0-9\-]", "", name)
    # Collapse multiple hyphens
    name = re.sub(r"-+", "-", name)
    return name


def parse_pokedex(path: Path) -> dict[int, list[str]]:
    """
    Returns {num: [name_form0, name_form1, ...]} ordered by appearance in the file.
    Entries with isCosmeticForme are skipped (they don't get their own GIF slot).
    """
    text = path.read_text(encoding="utf-8")

    # Split into individual entries by top-level key
    # Each entry looks like:   somekey: { ... },
    # We'll extract num and name from each block
    result: dict[int, list[str]] = {}

    # Find all entry blocks: key: { ... }
    # Use a simple state machine to handle nested braces
    entries_raw = []
    i = 0
    while i < len(text):
        # Find start of an entry (identifier followed by colon and brace)
        m = re.search(r'\b(\w+)\s*:\s*\{', text[i:])
        if not m:
            break
        start = i + m.start()
        brace_start = i + m.end() - 1  # position of '{'
        # Walk to find matching closing brace
        depth = 0
        j = brace_start
        while j < len(text):
            if text[j] == '{':
                depth += 1
            elif text[j] == '}':
                depth -= 1
                if depth == 0:
                    entries_raw.append(text[start:j + 1])
                    i = j + 1
                    break
            j += 1
        else:
            break

    for block in entries_raw:
        # Skip cosmetic formes
        if "isCosmeticForme" in block:
            continue

        # Extract num
        num_m = re.search(r'\bnum\s*:\s*(-?\d+)', block)
        if not num_m:
            continue
        num = int(num_m.group(1))

        # Extract name
        name_m = re.search(r'\bname\s*:\s*"([^"]+)"', block)
        if not name_m:
            continue
        name = _showdown_name(name_m.group(1))

        if num not in result:
            result[num] = []
        result[num].append(name)

    return result


# ─── BUILD MAPPING: (monsNo, formNo, is_female) → showdown_name ──────────────

def build_name_map(
    order: list[tuple[int, int, bool, int]],
    dex: dict[int, list[str]],
) -> dict[tuple[int, int, bool, int], str]:
    """
    For each entry in the order list, resolve the Showdown name.

    formNo maps directly to the dex form index.
    Female variants get a -f suffix.
    Cosmetic variants (variantIdx > 0) get a -vN suffix.
    """
    name_map: dict[tuple[int, int, bool, int], str] = {}

    for (mon, form, female, variant) in order:
        names = dex.get(mon, [])
        if not names:
            base_name = f"mon{mon:04d}-form{form:02d}"
        elif form < len(names):
            base_name = names[form]
        else:
            # formNo exceeds known dex entries — custom/game-only form.
            # Name it after the base form (index 0) + the form number.
            base_name = names[0] + f"-form{form}"

        suffix = ""
        if female:
            suffix += "-f"
        if variant > 0:
            suffix += f"-v{variant}"

        name_map[(mon, form, female, variant)] = base_name + suffix

    return name_map


# ─── RESIZE GIF ──────────────────────────────────────────────────────────────

def get_gif_size(path: Path) -> tuple[int, int]:
    img = Image.open(path)
    return img.size  # (width, height)


def resize_gif(src: Path, dst: Path, target_w: int, target_h: int):
    """
    Resize all frames of src GIF to (target_w, target_h) and save to dst.
    Preserves palette transparency.
    """
    img = Image.open(src)
    frames = []
    durations = []
    disposal = []

    try:
        while True:
            frame = img.convert("RGBA").resize((target_w, target_h), Image.LANCZOS)
            frames.append(frame)
            info = img.info
            durations.append(info.get("duration", 30))
            disposal.append(info.get("disposal", 2))
            img.seek(img.tell() + 1)
    except EOFError:
        pass

    if not frames:
        shutil.copy2(src, dst)
        return

    # Re-quantize each frame with transparency preserved
    from sprite_pipeline import to_palette_transparent
    frames_p = [to_palette_transparent(f) for f in frames]

    frames_p[0].save(
        dst,
        save_all=True,
        append_images=frames_p[1:],
        loop=0,
        duration=durations[0],
        optimize=False,
        transparency=0,
        disposal=2,
    )

    # Re-run gifsicle if available
    import shutil as _shutil
    import subprocess
    if _shutil.which("gifsicle"):
        delay_cs = max(1, round(durations[0] / 10))
        tmp = dst.with_suffix(".tmp.gif")
        dst.rename(tmp)
        subprocess.run(
            ["gifsicle", "--optimize=3", "--loop", f"--delay={delay_cs}", str(tmp), "-o", str(dst)],
            check=False, capture_output=True,
        )
        if tmp.exists():
            tmp.unlink()


def compute_target_size(
    our_w: int, our_h: int,
    ref_w: int, ref_h: int,
) -> tuple[int, int]:
    """
    Scale our GIF so its larger side equals the reference's larger side.
    Preserves aspect ratio.
    """
    ref_max = max(ref_w, ref_h)
    our_max = max(our_w, our_h)
    if our_max == 0:
        return ref_w, ref_h
    scale = ref_max / our_max
    new_w = max(1, round(our_w * scale))
    new_h = max(1, round(our_h * scale))
    return new_w, new_h


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Rename and resize sprite GIFs")
    parser.add_argument("--output-dir", type=Path, default=Path("./output"))
    parser.add_argument("--ani-dir", type=Path, default=Path("./References/ani"))
    parser.add_argument("--order", type=Path, default=Path("./References/video_order.txt"))
    parser.add_argument("--pokedex", type=Path, default=Path("./References/pokedex.ts"))
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would happen without doing it")
    parser.add_argument("--scale", type=float, default=1.0,
                        help="Extra scale multiplier applied on top of the reference-matched size. "
                             "E.g. 1.5 makes the output 1.5× larger than the reference size. "
                             "Default: 1.0 (no extra scaling)")
    args = parser.parse_args()

    print("Parsing video_order.txt...")
    order = parse_video_order(args.order)
    print(f"  {len(order)} entries")

    print("Parsing pokedex.ts...")
    dex = parse_pokedex(args.pokedex)
    print(f"  {len(dex)} Pokémon")

    print("Building name map...")
    name_map = build_name_map(order, dex)

    # Find all GIFs in output dir
    gif_files = sorted(args.output_dir.glob("*.gif"))
    if not gif_files:
        print(f"No GIFs found in {args.output_dir}")
        sys.exit(0)

    # Build ani index for fast lookup
    ani_files = {p.stem.lower(): p for p in args.ani_dir.glob("*.gif")}

    processed = 0
    skipped = 0

    for gif in gif_files:
        # Parse filename: NNNN_FF.gif, NNNN_FF_gG.gif, NNNN_FF_vV.gif, or NNNN_FF_gG_vV.gif
        m = re.match(r'^(\d+)_(\d+)(?:_g(\d+))?(?:_v(\d+))?\.gif$', gif.name)
        if not m:
            print(f"  SKIP (unexpected name): {gif.name}")
            skipped += 1
            continue

        mon = int(m.group(1))
        form = int(m.group(2))
        gender = int(m.group(3)) if m.group(3) is not None else 0
        variant = int(m.group(4)) if m.group(4) is not None else 0
        female = (gender == 1)
        key = (mon, form, female, variant)

        showdown_name = name_map.get(key)
        if not showdown_name:
            print(f"  SKIP (not in order list): {gif.name} → mon={mon} form={form} gender={gender} variant={variant}")
            skipped += 1
            continue

        new_path = args.output_dir / f"{showdown_name}.gif"

        # Find reference GIF
        ref_path = ani_files.get(showdown_name)
        if ref_path is None:
            # Strip -f suffix (female → use male reference)
            base = showdown_name.removesuffix("-f")
            ref_path = ani_files.get(base)
        if ref_path is None:
            # Strip -vN suffix (cosmetic variant → use base variant reference)
            base = re.sub(r'-v\d+(-f)?$', '', showdown_name)
            ref_path = ani_files.get(base)
        if ref_path is None:
            # Strip custom form suffix (e.g. venusaur-form3 → venusaur)
            base = re.sub(r'-form\d+(-[fv]\d*)*$', '', showdown_name)
            ref_path = ani_files.get(base)

        # Determine target size
        our_w, our_h = get_gif_size(gif)
        if ref_path and ref_path.exists():
            ref_w, ref_h = get_gif_size(ref_path)
            target_w, target_h = compute_target_size(our_w, our_h, ref_w, ref_h)
            size_note = f"{our_w}×{our_h} → {target_w}×{target_h} (ref: {ref_w}×{ref_h})"
        else:
            target_w, target_h = our_w, our_h
            size_note = f"{our_w}×{our_h} (no reference found)"

        # Apply extra scale multiplier if requested
        if args.scale != 1.0:
            target_w = max(1, round(target_w * args.scale))
            target_h = max(1, round(target_h * args.scale))
            size_note += f" ×{args.scale} → {target_w}×{target_h}"

        needs_resize = (target_w != our_w or target_h != our_h)

        print(f"  {gif.name} → {new_path.name}  [{size_note}]")

        if not args.dry_run:
            if needs_resize:
                resize_gif(gif, new_path, target_w, target_h)
                if new_path != gif:
                    gif.unlink()
            else:
                gif.rename(new_path)
        processed += 1

    print(f"\nDone. {processed} renamed/resized, {skipped} skipped.")


if __name__ == "__main__":
    main()
