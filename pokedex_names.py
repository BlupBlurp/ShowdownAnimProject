"""
Shared Pokédex name-resolution logic used by rename_sprites.py and rename_home.py.

Both scripts produce filenames that follow the same Pokémon Showdown convention,
so all parsing, override tables, and name-building live here to avoid drift.
"""

import re
from pathlib import Path


# ─── SHOWDOWN SLUG HELPER ────────────────────────────────────────────────────

def showdown_slug(raw: str) -> str:
    """Convert a Showdown display name to a flat filename slug.

    Rules (matching Pokémon Showdown's actual filenames):
    - Lowercase throughout.
    - JSON-style unicode escapes (e.g. \\u2019) are decoded before processing.
    - ALL hyphens and non-alphanumeric characters are removed.
    - Result is a flat alphanumeric string with NO hyphens.
      The form-separator hyphen is re-inserted by resolve_showdown_name().

    Examples:
      "Charizard"        → "charizard"
      "Charizard-Mega-X" → "charizardmegax"
      "Ho-Oh"            → "hooh"
      "Porygon-Z"        → "porygonz"
      "Kommo-o"          → "kommoo"
      "Mr. Mime"         → "mrmime"
      "Farfetch\\u2019d" → "farfetchd"
    """
    name = raw.strip().strip('"').strip("'")
    # Decode JSON-style unicode escapes (literal \u2019 in the .ts source → actual char → stripped)
    name = re.sub(r'\\u[0-9a-fA-F]{4}', '', name)
    name = name.lower()
    # Keep only a-z and 0-9 — strip everything else (hyphens, spaces, apostrophes, dots, unicode)
    name = re.sub(r"[^a-z0-9]", "", name)
    return name


# ─── OVERRIDE TABLES ─────────────────────────────────────────────────────────

# Hardcoded overrides keyed by (monsNo, formNo) for cases where multiple game
# forms map to the same Showdown name and can't be distinguished via the dex
# alone.  Applied in resolve_showdown_name() before the normal path.
# Female/variant suffixes are still appended after the override is applied.
FORM_OVERRIDES: dict[tuple[int, int], str] = {
    # Minior Meteor colour variants — game forms 0–6 all resolve to "miniormeteor"
    # from the pokedex formeOrder, so we override them explicitly here.
    (774, 0): "minior-meteor",
    (774, 1): "minior-orangemeteor",
    (774, 2): "minior-yellowmeteor",
    (774, 3): "minior-greenmeteor",
    (774, 4): "minior-bluemeteor",
    (774, 5): "minior-indigometeor",
    (774, 6): "minior-violetmeteor",
    # Maushold — Showdown's base name is "maushold-four"; the family-of-three
    # form (otherForme index 1) is plain "maushold".
    (906, 0): "maushold-four",
    (906, 1): "maushold",
}

# Hardcoded overrides for custom Relumi forms that don't follow standard naming.
# Applied as a final pass after the base name + suffixes are assembled.
RELUMI_OVERRIDES: dict[str, str] = {
    "venusaur-form3":        "venusaur-clone",
    "venusaur-form3-f":      "venusaur-clone-f",
    "blastoise-form3":       "blastoise-clone",
    "charizard-form4":       "charizard-clone",
    "pikachu-rockstar":      "pikachu-clone",
    "pikachu-rockstar-f":    "pikachu-clone-f",
    "pikachu-alola":         "pikachu-libre",
    "pikachu-unova":         "pikachu-popstar",
    "pikachu-sinnoh":        "pikachu-belle",
    "pikachu-kalos":         "pikachu-phd",
    "pikachu-hoenn":         "pikachu-rockstar",
    "eevee-form3":           "eevee-bandanapartner",
    "eevee-form3-f":         "eevee-bandanapartner-f",
    "onix-form1":            "onix-crystal",
    "mewtwo-form5":          "mewtwo-shadow",
    "mewtwo-form3":          "mewtwo-mkiiarmored",
    "mewtwo-form4":          "mewtwo-mkiarmored",
    "mewtwo-form6":          "mewtwo-shadowmega",
    "gengar-form3":          "gengar-stitched",
    "kabutops-form1":        "kabutops-missingno",
    "groudon-form2":         "groudon-meta",
    "lugia-form1":           "lugia-shadow",
    "rayquaza-form2":        "rayquaza-illusory",
    "marowak-alolatotem":    "marowak-ghost",
    "miniormeteor-form8":    "minior-orange",
    "miniormeteor-form9":    "minior-yellow",
    "miniormeteor-form10":   "minior-green",
    "miniormeteor-form11":   "minior-blue",
    "miniormeteor-form12":   "minior-indigo",
    "miniormeteor-form13":   "minior-violet",
    "zygarde-form6":   "zygarde-cell",
    "zygarde-mega":   "zygarde-core",
    "shaymin-form2":   "shaymin-pollutedland",
    "shaymin-form3":   "shaymin-pollutedsky",
}

# Alcremie sweet decoration suffixes, indexed by variantIdx (0–6).
# variantIdx 0 = Strawberry Sweet = no suffix (base name only).
ALCREMIE_SWEET_SUFFIXES: dict[int, str] = {
    0: "",           # Strawberry Sweet — base name, no suffix
    1: "-berry",     # Berry Sweet
    2: "-love",      # Love Sweet
    3: "-star",      # Star Sweet
    4: "-clover",    # Clover Sweet
    5: "-flower",    # Flower Sweet
    6: "-ribbon",    # Ribbon Sweet
}

# Alcremie cream-form index → Showdown base name.
# Used by rename_home.py which receives cream-form as a file field rather than
# a variantIdx.  The cream forms are cosmeticFormes in pokedex.ts so
# parse_pokedex() (which skips isCosmeticForme blocks) only returns the base
# entry; we hardcode the mapping here.
ALCREMIE_CREAM_FORMS: dict[int, str] = {
    0: "alcremie",
    1: "alcremie-rubycream",
    2: "alcremie-matchacream",
    3: "alcremie-mintcream",
    4: "alcremie-lemoncream",
    5: "alcremie-saltedcream",
    6: "alcremie-rubyswirl",
    7: "alcremie-caramelswirl",
    8: "alcremie-rainbowswirl",
}


# ─── PARSE pokedex.ts ────────────────────────────────────────────────────────

def _parse_string_array(text: str, field: str) -> list[str]:
    """Extract a string array field like formeOrder or cosmeticFormes from a block."""
    m = re.search(r'\b' + field + r'\s*:\s*\[([^\]]*)\]', text, re.DOTALL)
    if not m:
        return []
    return re.findall(r'"([^"]+)"', m.group(1))


def parse_pokedex(path: Path) -> tuple[dict[int, list[str]], dict[int, str]]:
    """
    Returns (forms, base_slugs) where:
      forms:      {num: [slug_form0, slug_form1, ...]} in correct game form order
      base_slugs: {num: base_species_slug} — the slug of the base entry (no baseSpecies
                  field), used as the prefix for hyphen reconstruction in
                  resolve_showdown_name().  Always "vivillon", "charizard", etc.
                  regardless of formeOrder reordering.

    Ordering rules:
    1. If the base entry has formeOrder, use it to sort all collected names for
       that num (covers Arceus and similar where alphabetical order is wrong).
       formeOrder may be a subset (e.g. Charizard excludes Gmax) — names not in
       formeOrder are appended after in their original order.
    2. If the base entry has cosmeticFormes but no otherFormes (e.g. Unown,
       Sawsbuck), include the cosmetic formes as additional form slots using
       formeOrder.
    3. Entries with isCosmeticForme are skipped (they have their own block but
       are not separate image slots — except for the Unown/Sawsbuck case above).

    All names are stored as flat slugs via showdown_slug() — hyphens are
    re-inserted by resolve_showdown_name() at lookup time.
    """
    text = path.read_text(encoding="utf-8")

    result: dict[int, list[str]] = {}
    base_slugs: dict[int, str] = {}          # ← base species slug per num
    forme_order_by_num: dict[int, list[str]] = {}
    cosmetic_formes_by_num: dict[int, list[str]] = {}
    has_other_formes_by_num: set[int] = set()

    entries_raw: list[str] = []
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
        name = showdown_slug(name_m.group(1))

        if num not in result:
            result[num] = []
        result[num].append(name)

        if 'baseSpecies' not in block:
            base_slugs[num] = name          # ← record base slug before reordering
            fo = _parse_string_array(block, 'formeOrder')
            if fo:
                forme_order_by_num[num] = [showdown_slug(n) for n in fo]
            cf = _parse_string_array(block, 'cosmeticFormes')
            if cf:
                cosmetic_formes_by_num[num] = [showdown_slug(n) for n in cf]
            if 'otherFormes' in block:
                has_other_formes_by_num.add(num)

    # Reorder by formeOrder where available
    for num, fo in forme_order_by_num.items():
        if num not in result:
            continue
        collected = result[num]
        collected_set = set(collected)
        ordered = [n for n in fo if n in collected_set]
        in_fo = set(fo)
        extras = [n for n in collected if n not in in_fo]
        result[num] = ordered + extras

    # Expand cosmeticFormes as real form slots.
    # For mons with ONLY cosmeticFormes (no otherFormes, e.g. Unown, Sawsbuck):
    #   append them as additional slots.
    # For mons with BOTH otherFormes AND cosmeticFormes (e.g. Vivillon):
    #   formeOrder already lists every slot in game order — use it directly,
    #   which naturally interleaves cosmetic and real formes.
    for num, cf in cosmetic_formes_by_num.items():
        if num not in result:
            continue
        fo = forme_order_by_num.get(num, [])
        if fo:
            result[num] = fo
        elif num not in has_other_formes_by_num:
            result[num] = result[num] + cf

    return result, base_slugs


# ─── GENDER DIFFERENCES ──────────────────────────────────────────────────────

def parse_gender_differences(path: Path) -> set[int]:
    """
    Returns the set of dex numbers that have visually distinct female sprites.

    A Pokémon qualifies if its base entry has 'genderDiffs: true'.
    Pokémon with a fixed gender (gender: "F", "N", or "M") do not have
    separate female sprites.
    """
    text = path.read_text(encoding="utf-8")
    result: set[int] = set()

    entries_raw: list[str] = []
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
        if 'baseSpecies' in block:
            continue  # only check base entries
        if 'genderDiffs' not in block:
            continue
        num_m = re.search(r'\bnum\s*:\s*(-?\d+)', block)
        if not num_m:
            continue
        result.add(int(num_m.group(1)))

    return result


# ─── NAME RESOLUTION ─────────────────────────────────────────────────────────

def resolve_showdown_name(
    mon: int,
    form: int,
    female: bool,
    variant: int,
    dex: dict[int, list[str]],
    base_slugs: dict[int, str] | None = None,
) -> str:
    """
    Resolve the Showdown filename slug for a single (mon, form, female, variant)
    combination.

    base_slugs: the second return value of parse_pokedex(). When provided, it
    supplies the correct base species slug for hyphen reconstruction (e.g.
    "vivillon" even when formeOrder puts a non-base form at index 0).
    Falls back to dex[mon][0] when not provided (correct for most mons).
    """
    # 1. Direct form override
    if (mon, form) in FORM_OVERRIDES:
        base_name = FORM_OVERRIDES[(mon, form)]
        suffix = ""
        if female:
            suffix += "-f"
        if mon == 869 and variant in ALCREMIE_SWEET_SUFFIXES:
            sweet = ALCREMIE_SWEET_SUFFIXES[variant]
            suffix += sweet
            if sweet:
                base_name = base_name.replace("-", "")
        elif variant > 0:
            suffix += f"-v{variant}"
        return RELUMI_OVERRIDES.get(base_name + suffix, base_name + suffix)

    # 2. dex lookup
    names = dex.get(mon, [])
    if not names:
        base_name = f"mon{mon:04d}form{form:02d}"
    elif form < len(names):
        flat = names[form]
        # Use the recorded base species slug for prefix stripping, not names[0],
        # because formeOrder reordering may have moved the base form away from index 0.
        base_slug = (base_slugs or {}).get(mon) or names[0]
        if flat.startswith(base_slug):
            forme_part = flat[len(base_slug):]
            base_name = base_slug + ("-" + forme_part if forme_part else "")
        else:
            base_name = flat
    else:
        # 3. Unknown form index — use base slug + "-form{N}"
        base_slug = (base_slugs or {}).get(mon) or names[0]
        base_name = base_slug + f"-form{form}"

    suffix = ""
    if female:
        suffix += "-f"
    if mon == 869 and variant in ALCREMIE_SWEET_SUFFIXES:
        sweet = ALCREMIE_SWEET_SUFFIXES[variant]
        suffix += sweet
        if sweet:
            base_name = base_name.replace("-", "")
    elif variant > 0:
        suffix += f"-v{variant}"

    return RELUMI_OVERRIDES.get(base_name + suffix, base_name + suffix)
