#!/usr/bin/env python3
"""
Home Sprite Rename & Center Tool

Renames Pokémon HOME sprites from pm{mon}_{form}_{variant}-CAB-... naming
to Showdown names, centers the sprite content on its canvas, and separates
shiny variants into a dedicated subfolder.

Usage:
    python rename_home.py [--input-dir ./input/home-centered]
                          [--output-dir ./output]
                          [--pokedex ./References/pokedex.ts]
                          [--dry-run]

Input filename format:
    pm{mon:04d}_{form:02d}_{variant:02d}-CAB-...png

Variant rules:
    _00  → normal sprite  → output/home-centered/{name}.png
    _01  → shiny sprite   → output/home-centered-shiny/{name}.png
    _10, _11, etc. → skipped

Forms not present in pokedex.ts are skipped (already handled upstream).

Centering: the sprite content (non-transparent pixels) is centered within
the original canvas size.
"""

import argparse
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image


# ─── PARSE pokedex.ts ────────────────────────────────────────────────────────

def _showdown_name(raw: str) -> str:
    name = raw.strip().strip('"').strip("'")
    name = name.lower().replace(" ", "-")
    name = re.sub(r"[^a-z0-9\-]", "", name)
    name = re.sub(r"-+", "-", name)
    return name


def parse_pokedex(path: Path) -> dict[int, list[str]]:
    """
    Returns {num: [name_form0, name_form1, ...]} ordered by appearance.
    Cosmetic formes are skipped.
    """
    text = path.read_text(encoding="utf-8")
    result: dict[int, list[str]] = {}

    entries_raw = []
    i = 0
    while i < len(text):
        m = re.search(r'\b(\w+)\s*:\s*\{', text[i:])
        if not m:
            break
        start = i + m.start()
        brace_start = i + m.end() - 1
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
        if "isCosmeticForme" in block:
            continue
        num_m = re.search(r'\bnum\s*:\s*(-?\d+)', block)
        if not num_m:
            continue
        num = int(num_m.group(1))
        name_m = re.search(r'\bname\s*:\s*"([^"]+)"', block)
        if not name_m:
            continue
        name = _showdown_name(name_m.group(1))
        if num not in result:
            result[num] = []
        result[num].append(name)

    return result


# ─── CENTER IMAGE ─────────────────────────────────────────────────────────────

def center_sprite(img: Image.Image) -> Image.Image:
    """
    Center the non-transparent content of an RGBA image on its canvas.
    The canvas size is preserved.
    """
    canvas_w, canvas_h = img.size
    data = np.array(img.convert("RGBA"))
    alpha = data[:, :, 3]

    rows = np.any(alpha > 0, axis=1)
    cols = np.any(alpha > 0, axis=0)

    if not rows.any():
        return img  # fully transparent, nothing to center

    row_min, row_max = int(np.argmax(rows)), int(len(rows) - 1 - np.argmax(rows[::-1]))
    col_min, col_max = int(np.argmax(cols)), int(len(cols) - 1 - np.argmax(cols[::-1]))

    content = img.crop((col_min, row_min, col_max + 1, row_max + 1))
    content_w, content_h = content.size

    paste_x = (canvas_w - content_w) // 2
    paste_y = (canvas_h - content_h) // 2

    result = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    result.paste(content, (paste_x, paste_y))
    return result


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Rename and center HOME sprites")
    parser.add_argument("--input-dir", type=Path, default=Path("./input/home-centered"))
    parser.add_argument("--output-dir", type=Path, default=Path("./output"))
    parser.add_argument("--pokedex", type=Path, default=Path("./References/pokedex.ts"))
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would happen without writing files")
    args = parser.parse_args()

    print("Parsing pokedex.ts...")
    dex = parse_pokedex(args.pokedex)
    print(f"  {len(dex)} Pokémon")

    out_normal = args.output_dir / "home-centered"
    out_shiny  = args.output_dir / "home-centered-shiny"
    if not args.dry_run:
        out_normal.mkdir(parents=True, exist_ok=True)
        out_shiny.mkdir(parents=True, exist_ok=True)

    # Collect all PNGs and group by (mon, form)
    pngs = sorted(args.input_dir.glob("*.png"))
    if not pngs:
        print(f"No PNGs found in {args.input_dir}")
        sys.exit(0)

    # Build a map: (mon, form) → {variant_str: Path}
    from collections import defaultdict
    groups: dict[tuple[int, int], dict[str, Path]] = defaultdict(dict)
    unparsed = []
    for png in pngs:
        m = re.match(r'^pm(\d{4})_(\d{2})_(\d{2})', png.name)
        if not m:
            unparsed.append(png)
            continue
        mon     = int(m.group(1))
        form    = int(m.group(2))
        variant = m.group(3)
        groups[(mon, form)][variant] = png

    for png in unparsed:
        print(f"  SKIP (unexpected name): {png.name}")

    processed = skipped = 0

    for (mon, form), variants in sorted(groups.items()):
        # Look up Showdown name — skip if form not in dex
        names = dex.get(mon)
        if names is None or form >= len(names):
            skipped += len(variants)
            continue

        showdown_name = names[form]

        # Determine which variant is "normal" and which is "shiny".
        #
        # Preferred: _00 = normal, _01 = shiny.
        # Fallback (e.g. pm0792_00_20 / pm0792_00_21): when _00/_01 are absent,
        # use the lowest-numbered variant as normal and the one ending in '1'
        # (same tens digit + 1) as shiny. Any other variants are skipped.

        if "00" in variants or "01" in variants:
            # Standard case
            to_process = {}
            if "00" in variants:
                to_process["00"] = (variants["00"], False)
            if "01" in variants:
                to_process["01"] = (variants["01"], True)
            # Skip everything else (_10, _11, etc.)
        else:
            # Non-standard variant numbering — pick lowest as normal,
            # its +1 counterpart (same tens digit, units=1) as shiny.
            sorted_vars = sorted(variants.keys())
            normal_var = sorted_vars[0]
            # Shiny is the same tens digit with units digit = 1
            tens = normal_var[0]  # first character
            shiny_var = tens + "1"
            to_process = {}
            to_process[normal_var] = (variants[normal_var], False)
            if shiny_var in variants:
                to_process[shiny_var] = (variants[shiny_var], True)
            # Skip any remaining variants
            skipped += len(variants) - len(to_process)

        for var_str, (png, is_shiny) in to_process.items():
            dest_dir = out_shiny if is_shiny else out_normal
            dest = dest_dir / f"{showdown_name}.png"
            label = f"{'[shiny] ' if is_shiny else ''}{png.name} → {dest.relative_to(args.output_dir)}"

            if not args.dry_run:
                img = Image.open(png).convert("RGBA")
                centered = center_sprite(img)
                centered.save(dest, optimize=True)

            print(f"  {label}")
            processed += 1

    print(f"\nDone. {processed} processed, {skipped} skipped.")


if __name__ == "__main__":
    main()
