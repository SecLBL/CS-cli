import json
from pathlib import Path

from materialyoucolor.hct import Hct
from materialyoucolor.utils.color_utils import argb_from_rgb

from caelestia.utils.material.generator import darken, mix

_WAL_CACHE = Path.home() / ".cache/wal/colors.json"


def _parse(hex_str: str) -> Hct:
    h = hex_str.lstrip("#")
    return Hct.from_int(argb_from_rgb(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)))


def _to_hex(hct: Hct) -> str:
    return hex(hct.to_int())[4:]


def _on(hct: Hct, light: Hct, dark: Hct) -> Hct:
    return dark if hct.tone > 50 else light


def get_colours_from_pywal(scheme) -> tuple[dict[str, str], str]:
    wal = json.loads(_WAL_CACHE.read_text())

    raw = {k: v.lstrip("#") for k, v in wal["colors"].items()}
    c = [_parse(raw[f"color{i}"]) for i in range(16)]

    bg   = c[0]   # color0 — background
    gray = c[8]   # color8 — bright black / dark gray
    fg   = c[7]   # color7 — foreground
    bfg  = c[15]  # color15 — bright white

    # Sort accent pairs (bright=color9-14, normal=color1-6) by chroma descending
    accent_pairs = sorted(
        [(c[i + 8], c[i]) for i in range(1, 7)],
        key=lambda p: p[0].chroma,
        reverse=True,
    )

    def br(n: int) -> Hct: return accent_pairs[n][0]
    def nm(n: int) -> Hct: return accent_pairs[n][1]

    mode = "light" if bg.tone > 60 else "dark"

    col: dict[str, Hct] = {}

    # ── Backgrounds ───────────────────────────────────────────────
    col["background"]              = bg
    col["surface"]                 = bg
    col["surfaceDim"]              = bg
    col["surfaceBright"]           = mix(bg, gray, 0.5)
    col["surfaceContainerLowest"]  = darken(bg, 0.5)
    col["surfaceContainerLow"]     = mix(bg, gray, 0.15)
    col["surfaceContainer"]        = mix(bg, gray, 0.28)
    col["surfaceContainerHigh"]    = mix(bg, gray, 0.42)
    col["surfaceContainerHighest"] = mix(bg, gray, 0.58)
    col["surfaceVariant"]          = gray
    col["base"]                    = bg
    col["mantle"]                  = darken(bg, 0.03)
    col["crust"]                   = darken(bg, 0.05)
    col["surface0"]                = mix(bg, gray, 0.10)
    col["surface1"]                = mix(bg, gray, 0.22)
    col["surface2"]                = mix(bg, gray, 0.36)
    col["overlay0"]                = mix(bg, gray, 0.48)
    col["overlay1"]                = mix(bg, gray, 0.62)
    col["overlay2"]                = mix(bg, gray, 0.76)
    col["shadow"]                  = _parse("000000")
    col["scrim"]                   = _parse("000000")

    # ── Foregrounds ───────────────────────────────────────────────
    col["onBackground"]     = fg
    col["onSurface"]        = fg
    col["text"]             = fg
    col["onSurfaceVariant"] = mix(fg, gray, 0.35)
    col["subtext1"]         = mix(fg, gray, 0.35)
    col["outline"]          = mix(fg, gray, 0.55)
    col["subtext0"]         = mix(fg, gray, 0.55)
    col["outlineVariant"]   = mix(gray, bg, 0.45)
    col["inverseSurface"]   = bfg
    col["inverseOnSurface"] = gray

    # ── Primary ───────────────────────────────────────────────────
    pb, pn = br(0), nm(0)
    col["primary"]                 = pb
    col["primaryDim"]              = pn
    col["onPrimary"]               = _on(pb, fg, bg)
    col["primaryContainer"]        = mix(pn, bg, 0.45)
    col["onPrimaryContainer"]      = mix(pb, fg, 0.4)
    col["inversePrimary"]          = pn
    col["primaryFixed"]            = mix(pb, bfg, 0.4)
    col["primaryFixedDim"]         = pb
    col["onPrimaryFixed"]          = bg
    col["onPrimaryFixedVariant"]   = mix(pn, bg, 0.3)
    col["primaryPaletteKeyColor"]  = pn
    col["primary_paletteKeyColor"] = pn
    col["surfaceTint"]             = pb

    # ── Secondary ─────────────────────────────────────────────────
    sb, sn = br(1), nm(1)
    col["secondary"]                  = sb
    col["secondaryDim"]               = sn
    col["onSecondary"]                = _on(sb, fg, bg)
    col["secondaryContainer"]         = mix(sn, bg, 0.45)
    col["onSecondaryContainer"]       = mix(sb, fg, 0.4)
    col["inverseSecondary"]           = sn
    col["secondaryFixed"]             = mix(sb, bfg, 0.4)
    col["secondaryFixedDim"]          = sb
    col["onSecondaryFixed"]           = bg
    col["onSecondaryFixedVariant"]    = mix(sn, bg, 0.3)
    col["secondaryPaletteKeyColor"]   = sn
    col["secondary_paletteKeyColor"]  = sn

    # ── Tertiary ──────────────────────────────────────────────────
    tb, tn = br(2), nm(2)
    col["tertiary"]                   = tb
    col["tertiaryDim"]                = tn
    col["onTertiary"]                 = _on(tb, fg, bg)
    col["tertiaryContainer"]          = mix(tn, bg, 0.45)
    col["onTertiaryContainer"]        = mix(tb, fg, 0.4)
    col["inverseTertiary"]            = tn
    col["tertiaryFixed"]              = mix(tb, bfg, 0.4)
    col["tertiaryFixedDim"]           = tb
    col["onTertiaryFixed"]            = bg
    col["onTertiaryFixedVariant"]     = mix(tn, bg, 0.3)
    col["tertiaryPaletteKeyColor"]    = tn
    col["tertiary_paletteKeyColor"]   = tn

    # ── Neutral Palette Keys ──────────────────────────────────────
    col["neutralPaletteKeyColor"]           = gray
    col["neutral_paletteKeyColor"]          = gray
    col["neutralVariantPaletteKeyColor"]    = mix(gray, pn, 0.2)
    col["neutral_variant_paletteKeyColor"]  = mix(gray, pn, 0.2)

    # ── Named colours: color9-14 → rosewater-maroon, color1-6 → peach-sapphire ──
    named = [
        "rosewater", "flamingo", "pink", "mauve", "red", "maroon",
        "peach", "yellow", "green", "teal", "sky", "sapphire",
    ]
    for i, name in enumerate(named):
        col[name] = c[i + 9] if i < 6 else c[i - 5]
    col["blue"]    = pb
    col["lavender"] = sb

    # ── K-Colors ──────────────────────────────────────────────────
    col["klink"]             = pb
    col["klinkSelection"]    = pb
    col["kvisited"]          = sb
    col["kvisitedSelection"] = sb
    col["kneutral"]          = tb
    col["kneutralSelection"] = tb
    kpos = br(3) if len(accent_pairs) > 3 else tb
    col["kpositive"]          = kpos
    col["kpositiveSelection"] = kpos

    # ── Convert HCT → hex strings ─────────────────────────────────
    result = {k: _to_hex(v) for k, v in col.items()}

    # ── Fixed values (semantic / accessibility) ───────────────────
    result.update({
        "error":                "ffb4ab",
        "errorDim":             "ff554a",
        "onError":              "690005",
        "errorContainer":       "93000a",
        "onErrorContainer":     "ffdad6",
        "errorPaletteKeyColor": "de3730",
        "knegative":            "ff554a",
        "knegativeSelection":   "ff554a",
        "success":              "B5CCBA",
        "onSuccess":            "213528",
        "successContainer":     "374B3E",
        "onSuccessContainer":   "D1E9D6",
    })

    # ── Terminal colours directly from pywal ──────────────────────
    for i in range(16):
        result[f"term{i}"] = raw[f"color{i}"]

    return result, mode
