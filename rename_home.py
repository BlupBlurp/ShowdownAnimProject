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

Forms not present in pokedex.ts are named "{base_name}-{form}" using form 0 as the base.

Centering: the sprite content (non-transparent pixels) is centered within
the original canvas size.
"""

import argparse
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from pokedex_names import (
    parse_pokedex,
    parse_gender_differences,
    resolve_showdown_name,
    ALCREMIE_CREAM_FORMS,
    ALCREMIE_SWEET_SUFFIXES,
    RELUMI_OVERRIDES,
)


# ─── CENTER IMAGE ─────────────────────────────────────────────────────────────

def center_sprite(img: Image.Image) -> Image.Image:
    """
    Center the non-transparent content of an RGBA image on its canvas.
    The canvas size is preserved.

    Alpha threshold is 4 (not 0) to ignore stray near-transparent fringe pixels
    (alpha=1–4) that HOME sprites carry at canvas edges as anti-aliasing artefacts.
    These pixels would otherwise expand the bounding box to the full canvas and
    prevent centering from working correctly.
    """
    canvas_w, canvas_h = img.size
    data = np.array(img.convert("RGBA"))
    alpha = data[:, :, 3]

    rows = np.any(alpha > 4, axis=1)
    cols = np.any(alpha > 4, axis=0)

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


# ─── ALCREMIE SWEET DECORATION MAPPING ───────────────────────────────────────
# ALCREMIE_CREAM_FORMS and ALCREMIE_SWEET_SUFFIXES are imported from pokedex_names.


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
    dex, base_slugs = parse_pokedex(args.pokedex)
    print(f"  {len(dex)} Pokémon")
    gender_diffs = parse_gender_differences(args.pokedex)

    out_normal = args.output_dir / "home-centered"
    out_shiny  = args.output_dir / "home-centered-shiny"
    if not args.dry_run:
        out_normal.mkdir(parents=True, exist_ok=True)
        out_shiny.mkdir(parents=True, exist_ok=True)

    # Collect all PNGs and split Alcremie (4-field names) from the rest
    pngs = sorted(args.input_dir.glob("*.png"))
    if not pngs:
        print(f"No PNGs found in {args.input_dir}")
        sys.exit(0)

    # Build a map: (mon, form) → {(tens_group: int, shiny: bool, variant_idx: int): Path}
    # Alcremie files (pm0869_...) are handled separately below.
    #
    # HOME filename formats:
    #   3-field: pm{mon:04d}_{form:02d}_{GS:02d}.png
    #     GS is a two-digit code: tens = HOME's internal group index (not always 0-based),
    #                              units = shiny (0=normal, 1=shiny)
    #     The tens group is NOT a reliable gender indicator on its own — we resolve
    #     female status later by checking whether the mon has gender differences.
    #
    #   4-field: pm{mon:04d}_{form:02d}_{GS:02d}_{variant:02d}.png
    #     Same GS encoding, plus an explicit variant index (for Arbok, Magikarp etc.)
    #     When 4-field files exist for a (mon, form) group, 3-field files are skipped.
    from collections import defaultdict
    groups: dict[tuple[int, int], dict[tuple[int, bool, int], Path]] = defaultdict(dict)
    has_4field: set[tuple[int, int]] = set()
    alcremie_pngs: list[Path] = []
    unparsed = []
    pending_3field: list[tuple[tuple[int, int], tuple[int, bool, int], Path]] = []

    for png in pngs:
        m4 = re.match(r'^pm(\d{4})_(\d{2})_(\d{2})_(\d{2})', png.name)
        m3 = re.match(r'^pm(\d{4})_(\d{2})_(\d{2})$', png.stem)
        if m4:
            mon     = int(m4.group(1))
            form    = int(m4.group(2))
            gs      = int(m4.group(3))
            variant = int(m4.group(4))
            tens    = gs // 10
            shiny   = (gs % 10) == 1
            if mon == 869:
                alcremie_pngs.append(png)
                continue
            groups[(mon, form)][(tens, shiny, variant)] = png
            has_4field.add((mon, form))
        elif m3:
            mon  = int(m3.group(1))
            form = int(m3.group(2))
            gs   = int(m3.group(3))
            tens  = gs // 10
            shiny = (gs % 10) == 1
            if mon == 869:
                alcremie_pngs.append(png)
                continue
            pending_3field.append(((mon, form), (tens, shiny, 0), png))
        else:
            unparsed.append(png)

    # Add 3-field entries only for groups that have no 4-field files
    for key, variant_key, png in pending_3field:
        if key not in has_4field:
            groups[key][variant_key] = png

    for png in unparsed:
        print(f"  SKIP (unexpected name): {png.name}")

    processed = skipped = 0

    # ── Alcremie special case ─────────────────────────────────────────────────
    # Filename format: pm0869_FF_NS[_SS].png
    #   FF = cream form index (00–08)
    #   NS = 10 (normal) or 11 (shiny)
    #   SS = sweet index 00–06 (absent = strawberry sweet, same as 00)
    # Sweet index → suffix: 00/absent=strawberry (no suffix), 01=berry, 02=love,
    #   03=star, 04=clover, 05=flower, 06=ribbon.
    # The cream forms are cosmeticFormes in pokedex.ts so we use the imported
    # ALCREMIE_CREAM_FORMS table instead of the dex lookup.
    for png in sorted(alcremie_pngs):
        # Match both 3-field (pm0869_FF_NS) and 4-field (pm0869_FF_NS_SS) names
        m = re.match(r'^pm0869_(\d{2})_(10|11)(?:_(\d{2}))?', png.name)
        if not m:
            print(f"  SKIP (unexpected Alcremie name): {png.name}")
            skipped += 1
            continue

        form      = int(m.group(1))
        is_shiny  = m.group(2) == "11"
        sweet_idx = int(m.group(3)) if m.group(3) is not None else 0

        cream_base = ALCREMIE_CREAM_FORMS.get(form)
        if cream_base is None:
            print(f"  SKIP (unknown Alcremie cream form {form}): {png.name}")
            skipped += 1
            continue

        sweet_suffix = ALCREMIE_SWEET_SUFFIXES.get(sweet_idx)
        if sweet_suffix is None:
            print(f"  SKIP (unknown Alcremie sweet index {sweet_idx}): {png.name}")
            skipped += 1
            continue

        # When a sweet suffix is present the cream form's internal hyphen is dropped:
        # e.g. "alcremie-rubycream" + "-berry" → "alcremierubycream-berry"
        # The base (strawberry sweet, no suffix) keeps its hyphen: "alcremie-rubycream"
        prefix = cream_base.replace("-", "") if sweet_suffix else cream_base
        full_name = RELUMI_OVERRIDES.get(prefix + sweet_suffix, prefix + sweet_suffix)
        dest_dir  = out_shiny if is_shiny else out_normal
        dest      = dest_dir / f"{full_name}.png"
        label     = f"{'[shiny] ' if is_shiny else ''}{png.name} → {dest.relative_to(args.output_dir)}"

        if not args.dry_run:
            img = Image.open(png).convert("RGBA")
            centered = center_sprite(img)
            centered.save(dest, optimize=True)

        print(f"  {label}")
        processed += 1
    # ── end Alcremie ──────────────────────────────────────────────────────────

    for (mon, form), variants in sorted(groups.items()):
        names = dex.get(mon)
        if names is None:
            skipped += len(variants)
            continue

        base_name = resolve_showdown_name(mon, form, False, 0, dex, base_slugs)

        # variants is keyed by (tens_group, is_shiny, variant_idx).
        # tens_group is HOME's internal group index — not necessarily 0-based.
        # Sort the unique tens values: first = male/default, second = female
        # (only if this mon has gender differences per pokedex).
        # variant_idx 0 → base name, variant_idx N>0 → base_name + "-vN"
        tens_groups = sorted(set(tg for (tg, _, _) in variants))
        has_female = mon in gender_diffs and len(tens_groups) >= 2

        for (tens_group, is_shiny, variant_idx), png in sorted(variants.items()):
            is_female = has_female and (tens_group == tens_groups[1])
            name = resolve_showdown_name(mon, form, is_female, 0, dex, base_slugs)
            if variant_idx > 0:
                name = f"{name}-v{variant_idx}"

            dest_dir = out_shiny if is_shiny else out_normal
            dest = dest_dir / f"{name}.png"
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
