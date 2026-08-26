#!/usr/bin/env python3
"""Cute flat-cartoon SVG illustrations for the hornsbychiropractor blog.

Every scene function returns a standalone ``<svg>`` string with
``viewBox="0 0 800 450"`` (16:9). Style rules:

* flat pastel design — no thick outlines, no gradients, no anatomy detail;
* people are built from circles / ellipses / rounded rects only;
* faces are two dot eyes plus a tiny smile curve;
* backgrounds are soft single-colour circles or rounded rects.

Each function accepts an optional ``palette`` dict so colour schemes can be
varied between posts without changing the drawing itself.

Public API: ``SCENES`` maps scene names to their drawing functions.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

DEFAULT_PALETTE = {
    "bg": "#FDF6EC",       # cream backdrop circle
    "skin": "#FFDCB8",
    "hair": "#8C6A5D",
    "shirt": "#A8D8EA",    # soft blue
    "pants": "#7FB3D5",
    "accent": "#FFB5A7",   # coral
    "sage": "#B8E0D2",     # sage green
    "yellow": "#FFE5B4",   # soft yellow
    "white": "#FFFFFF",
    "ground": "#DDEBE4",
}


def _p(palette: dict | None, key: str) -> str:
    return (palette or {}).get(key, DEFAULT_PALETTE[key])


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------


def _svg_open() -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 450" '
        'role="img">'
    )


def _svg_close() -> str:
    return "</svg>"


def _backdrop(bg: str, shape: str = "circle") -> str:
    """Soft single-colour backdrop."""
    if shape == "rect":
        return (
            '<rect x="20" y="20" width="760" height="410" rx="48" '
            f'fill="{bg}"/>'
        )
    return f'<circle cx="400" cy="225" r="215" fill="{bg}"/>'


def _face(cx: float, cy: float, r: float, skin: str, sleeping: bool = False,
        tilt: int = 0, blush: bool = False) -> str:
    """Dot eyes + tiny smile. Optionally closed eyes / rotated head / blush."""
    transform = f' transform="rotate({tilt} {cx} {cy})"' if tilt else ""
    out = [f'<g{transform}>']
    out.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{skin}"/>')
    eye_y = cy - r * 0.05
    dx = r * 0.38
    if sleeping:
        # Closed eyes: tiny downward arcs.
        out.append(
            f'<path d="M {cx - dx - 4} {eye_y} q 4 4 8 0" stroke="#5B4A42" '
            'stroke-width="3" fill="none" stroke-linecap="round"/>'
        )
        out.append(
            f'<path d="M {cx + dx - 4} {eye_y} q 4 4 8 0" stroke="#5B4A42" '
            'stroke-width="3" fill="none" stroke-linecap="round"/>'
        )
    else:
        out.append(f'<circle cx="{cx - dx}" cy="{eye_y}" r="3.2" fill="#5B4A42"/>')
        out.append(f'<circle cx="{cx + dx}" cy="{eye_y}" r="3.2" fill="#5B4A42"/>')
    # Blush cheeks.
    if blush:
        by = cy + r * 0.28
        bx = r * 0.62
        out.append(f'<ellipse cx="{cx - bx}" cy="{by}" rx="6.5" ry="4.2" '
                   'fill="#F9B4AB" opacity="0.75"/>')
        out.append(f'<ellipse cx="{cx + bx}" cy="{by}" rx="6.5" ry="4.2" '
                   'fill="#F9B4AB" opacity="0.75"/>')
    # Smile.
    my = cy + r * 0.32
    out.append(
        f'<path d="M {cx - 6} {my} q 6 6 12 0" stroke="#5B4A42" '
        'stroke-width="3" fill="none" stroke-linecap="round"/>'
    )
    out.append("</g>")
    return "".join(out)


def _hair_cap(cx: float, cy: float, r: float, hair: str) -> str:
    """Simple half-circle hair cap."""
    return (
        f'<path d="M {cx - r} {cy} A {r} {r} 0 0 1 {cx + r} {cy} L {cx + r} '
        f'{cy - r * 0.15} Q {cx} {cy - r * 1.35} {cx - r} {cy - r * 0.15} Z" '
        f'fill="{hair}"/>'
    )


def _capsule(x: float, y: float, w: float, h: float, fill: str,
             rx: float | None = None, rotate: float = 0,
             cx: float = 0, cy: float = 0) -> str:
    """Rounded rect limb/torso, optionally rotated about (cx, cy)."""
    r = rx if rx is not None else min(w, h) / 2
    rot = f' transform="rotate({rotate} {cx} {cy})"' if rotate else ""
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" '
        f'fill="{fill}"{rot}/>'
    )


def _zzz(x: float, y: float, fill: str = "#9DB8D9") -> str:
    return (
        f'<g fill="none" stroke="{fill}" stroke-width="4" '
        'stroke-linecap="round" stroke-linejoin="round">'
        f'<path d="M {x} {y} h 14 l -14 16 h 14"/>'
        f'<path d="M {x + 26} {y - 22} h 11 l -11 12 h 11"/>'
        "</g>"
    )


def _sun(cx: float, cy: float, r: float, yellow: str) -> str:
    rays = "".join(
        f'<line x1="{cx + r * 1.45 * __import__("math").cos(a)}" '
        f'y1="{cy + r * 1.45 * __import__("math").sin(a)}" '
        f'x2="{cx + r * 1.85 * __import__("math").cos(a)}" '
        f'y2="{cy + r * 1.85 * __import__("math").sin(a)}" '
        'stroke="' + yellow + '" stroke-width="6" stroke-linecap="round"/>'
        for a in [i * 3.14159 / 4 for i in range(8)]
    )
    return f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{yellow}"/>' + rays


def _tree(cx: float, base_y: float, sage: str, trunk: str = "#B08968") -> str:
    return (
        f'<rect x="{cx - 7}" y="{base_y - 52}" width="14" height="52" '
        f'rx="6" fill="{trunk}"/>'
        f'<circle cx="{cx}" cy="{base_y - 78}" r="42" fill="{sage}"/>'
        f'<circle cx="{cx - 30}" cy="{base_y - 58}" r="26" fill="{sage}"/>'
        f'<circle cx="{cx + 30}" cy="{base_y - 58}" r="26" fill="{sage}"/>'
    )


# ---------------------------------------------------------------------------
# Scenes
# ---------------------------------------------------------------------------


def side_sleeping(palette: dict | None = None) -> str:
    """Keywords: sleep, sleeping, side sleeper, pillow between knees,
    night pain, rest, bedtime, position.

    Side-view scene: a person lying on their LEFT side on a bed with a
    pillow between the knees. Rich context: night window with moon and
    stars, bed frame with headboard + legs, blanket with folds, rug,
    bedside lamp."""
    pal = palette or {}
    skin   = _p(pal, "skin")
    shirt  = _p(pal, "shirt")
    accent = _p(pal, "accent")
    hair   = _p(pal, "hair")
    white  = _p(pal, "white")
    bg     = _p(pal, "bg")
    pants  = _p(pal, "pants")
    sage   = _p(pal, "sage")
    yellow = _p(pal, "yellow")
    ground = _p(pal, "ground")

    p = [_svg_open()]

    # --- Night sky backdrop -------------------------------------------------
    p.append('<rect x="0" y="0" width="800" height="450" fill="#2B3A67"/>')

    # Window with curtains (right wall).
    p.append('<rect x="580" y="60" width="170" height="150" rx="14" fill="#1E2A4A"/>')
    p.append('<rect x="592" y="72" width="146" height="126" rx="10" fill="#3D5A80"/>')
    # Moon in window.
    p.append('<circle cx="700" cy="110" r="22" fill="#FFE9A8"/>')
    p.append('<circle cx="692" cy="104" r="18" fill="#3D5A80"/>')
    # Stars in window.
    for sx, sy in ((615, 95), (640, 150), (665, 120)):
        p.append(f'<circle cx="{sx}" cy="{sy}" r="3" fill="#FFE9A8"/>')
    p.append('<rect x="662" y="60" width="8" height="150" fill="#2B3A67"/>')   # window bar
    # Curtains.
    p.append(f'<path d="M 570 55 q -12 85 8 158 l -26 0 q -14 -80 -4 -158 Z" fill="{sage}"/>')
    p.append(f'<path d="M 758 55 q 12 85 -8 158 l 24 0 q 12 -80 4 -158 Z" fill="{sage}"/>')

    # --- Floor ---------------------------------------------------------------
    p.append(f'<rect x="0" y="392" width="800" height="58" fill="{ground}"/>')

    # --- Rug -----------------------------------------------------------------
    p.append('<ellipse cx="180" cy="420" rx="120" ry="20" fill="#C9DED6"/>')

    # --- Bed -----------------------------------------------------------------
    bed_x, bed_y, bed_w, bed_h = 90, 280, 600, 84
    # Headboard (left side).
    p.append(f'<rect x="70" y="200" width="26" height="190" rx="13" fill="{pants}"/>')
    p.append(f'<rect x="62" y="192" width="42" height="46" rx="16" fill="{pants}"/>')
    # Mattress.
    p.append(f'<rect x="92" y="{bed_y}" width="{bed_w}" height="{bed_h}" rx="26" fill="{white}"/>')
    # Mattress shading line.
    p.append(f'<rect x="92" y="{bed_y+64}" width="{bed_w}" height="20" rx="10" fill="#EDF2F7"/>')
    # Blanket covering lower two-thirds of the bed (coral).
    p.append(f'<path d="M {bed_x+230} {bed_y-6} h {bed_w-250} a 26 26 0 0 1 26 26 v 38 '
             f'a 26 26 0 0 1 -26 26 h -{bed_w-250} Z" fill="{accent}"/>')
    # Blanket fold curves.
    for fx in (bed_x+290, bed_x+360, bed_x+430):
        p.append(f'<path d="M {fx} {bed_y} q 6 32 0 64" stroke="#F49E93" '
                 f'stroke-width="5" fill="none" stroke-linecap="round"/>')
    # Bed legs.
    p.append(f'<rect x="110" y="362" width="18" height="34" rx="7" fill="{hair}"/>')
    p.append(f'<rect x="660" y="362" width="18" height="34" rx="7" fill="{hair}"/>')

    # --- Person lying on their left side -------------------------------------
    # Legs: bent at knees, drawn first so torso overlaps them.
    p.append(f'<path d="M 380 296 L 500 282 Q 512 280 514 292 L 516 306 '
             f'Q 480 322 384 330 Z" fill="{pants}"/>')
    # Knee pillow BETWEEN the knees — clearly visible coral capsule standing upright.
    p.append(_capsule(508, 240, 52, 96, "#F49E93", rx=26))
    p.append(_capsule(516, 252, 36, 72, accent, rx=18))
    # Lower leg + foot beyond knee pillow.
    p.append(f'<path d="M 548 296 L 622 300 Q 634 301 633 313 L 632 326 '
             f'Q 590 330 550 326 Z" fill="{pants}"/>')
    # Foot.
    p.append('<ellipse cx="636" cy="322" rx="16" ry="11" fill="' + skin + '"/>')

    # Torso (shirt).
    p.append(_capsule(268, 258, 128, 74, shirt, rx=37))

    # Arm resting along the top of the body, hand resting forward.
    p.append(f'<path d="M 300 268 Q 370 258 420 286 Q 442 298 452 312" '
             f'stroke="{accent}" stroke-width="24" fill="none" stroke-linecap="round"/>')
    # Hand.
    p.append('<circle cx="456" cy="316" r="13" fill="' + skin + '"/>')

    # Head resting on pillow.
    head_cx, head_cy, head_r = 218, 262, 34

    # --- Head pillow under cheek ---------------------------------------------
    p.append(_capsule(168, 276, 108, 44, white, rx=22))
    p.append('<path d="M 176 296 q 46 14 92 0" stroke="#E2E8F0" stroke-width="4" '
             'fill="none" stroke-linecap="round"/>')

    # Head over pillow.
    p.append(f'<circle cx="{head_cx}" cy="{head_cy}" r="{head_r}" fill="{skin}"/>')
    p.append(f'<path d="M {head_cx-head_r} {head_cy} a {head_r} {head_r} 0 0 1 '
             f'{head_r*2} 0 l 0 -8 q -{head_r} -{head_r-6} -{head_r*2} 0 Z" fill="{hair}"/>')
    p.append(f'<circle cx="{head_cx-head_r+4}" cy="{head_cy-18}" r="11" fill="{hair}"/>')
    p.append(_face(head_cx, head_cy, head_r, skin, sleeping=True, blush=True))

    # --- Bedside table + lamp (right of bed) ---------------------------------
    p.append(f'<rect x="686" y="308" width="76" height="88" rx="10" fill="{sage}"/>')
    p.append('<rect x="700" y="318" width="48" height="8" rx="4" fill="#FFFFFF" opacity="0.5"/>')
    # Lamp.
    p.append(f'<rect x="716" y="278" width="14" height="32" rx="6" fill="{hair}"/>')
    p.append(f'<path d="M 706 280 L 740 280 L 732 258 L 714 258 Z" fill="{yellow}"/>')
    p.append('<circle cx="723" cy="270" r="6" fill="#FFF6DC"/>')

    # --- Zzz floating above --------------------------------------------------
    p.append(_zzz(320, 150, fill="#FFE9A8"))

    # Soft glow around sleeper to focus attention.
    p.append('<circle cx="400" cy="300" r="210" fill="none" stroke="#FFE9A8" '
             'stroke-opacity="0.08" stroke-width="40"/>')

    p.append(_svg_close())
    return "".join(p)


def back_sleeping(palette: dict | None = None) -> str:
    """Keywords: sleep, back sleeper, pillow under knees, spine support,
    night pain, rest, lying down, posture in bed."""
    pal = palette or {}
    skin, shirt = _p(pal, "skin"), _p(pal, "shirt")
    accent = _p(pal, "accent")
    hair, white, bg = _p(pal, "hair"), _p(pal, "white"), _p(pal, "bg")
    parts = [_svg_open(), _backdrop(bg)]
    # Stars.
    for x, y in ((170, 120), (620, 100), (680, 180)):
        parts.append(f'<circle cx="{x}" cy="{y}" r="5" fill="{_p(pal, "yellow")}"/>')
    # Mattress.
    parts.append(_capsule(150, 330, 500, 54, white))
    parts.append('<rect x="160" y="382" width="480" height="24" rx="12" '
                 f'fill="{_p(pal, "pants")}"/>')
    # Pillow under head.
    parts.append(_capsule(206, 306, 104, 38, _p(pal, "shirt"), rx=19))
    # Straight body.
    parts.append(_capsule(310, 288, 210, 50, shirt))
    # Legs straight, wedge pillow under knees.
    parts.append(_capsule(510, 292, 120, 42, _p(pal, "pants")))
    parts.append('<path d="M 512 334 l 66 -34 l 10 34 Z" '
                 f'fill="{_p(pal, "accent")}"/>')          # knee wedge
    # Blanket over torso.
    parts.append(_capsule(330, 322, 200, 26, accent, rx=13))
    # Head.
    parts.append(_hair_cap(240, 300, 30, hair))
    parts.append(_face(240, 300, 30, skin, sleeping=True))
    parts.append(_zzz(600, 150))
    parts.append(_svg_close())
    return "".join(parts)


def morning_stretch(palette: dict | None = None) -> str:
    """Keywords: morning, wake, waking up, stretch, stretching, rise,
    start the day, energy, bed."""
    pal = palette or {}
    skin, shirt = _p(pal, "skin"), _p(pal, "shirt")
    hair, bg = _p(pal, "hair"), _p(pal, "bg")
    parts = [_svg_open(), _backdrop(bg)]
    # Sun through window.
    parts.append('<rect x="520" y="70" width="170" height="150" rx="18" '
                 f'fill="{_p(pal, "white")}"/>')
    parts.append(_sun(605, 145, 32, _p(pal, "yellow")))
    # Bed edge.
    parts.append(_capsule(150, 360, 420, 40, _p(pal, "white")))
    # Sitting person: torso upright, both arms stretched high.
    parts.append(_capsule(300, 220, 62, 130, shirt))                    # torso
    parts.append(_capsule(258, 118, 22, 116, _p(pal, "accent"), rotate=18,         # left arm
                          cx=280, cy=230))
    parts.append(_capsule(382, 118, 22, 116, _p(pal, "accent"), rotate=-18,        # right arm
                          cx=382, cy=230))
    # Legs hanging over bed edge.
    parts.append(_capsule(304, 340, 24, 70, _p(pal, "pants")))
    parts.append(_capsule(336, 340, 24, 70, _p(pal, "pants")))
    accent = _p(pal, "accent")
    # Head between raised arms.
    parts.append(_hair_cap(331, 186, 34, hair))
    parts.append(_face(331, 192, 34, skin))
    parts.append(_svg_close())
    return "".join(parts)


def desk_setup(palette: dict | None = None) -> str:
    """Keywords: desk, computer, laptop, monitor, office, ergonomics,
    workstation, sitting posture, work, screen, chair."""
    pal = palette or {}
    skin, shirt = _p(pal, "skin"), _p(pal, "shirt")
    hair, bg = _p(pal, "hair"), _p(pal, "bg")
    parts = [_svg_open(), _backdrop(bg)]
    # Desk.
    parts.append('<rect x="380" y="270" width="260" height="18" rx="9" '
                 f'fill="{_p(pal, "pants")}"/>')
    parts.append('<rect x="398" y="288" width="14" height="110" rx="7" '
                 f'fill="{_p(pal, "pants")}"/>')
    parts.append('<rect x="608" y="288" width="14" height="110" rx="7" '
                 f'fill="{_p(pal, "pants")}"/>')
    # Monitor on riser.
    parts.append('<rect x="450" y="176" width="130" height="86" rx="12" '
                 f'fill="{_p(pal, "sage")}"/>')
    parts.append('<rect x="505" y="262" width="18" height="12" rx="4" '
                 f'fill="{_p(pal, "pants")}"/>')
    # Chair.
    parts.append(_capsule(214, 214, 84, 130, _p(pal, "accent"), rx=24))
    parts.append(_capsule(196, 330, 120, 18, _p(pal, "accent"), rx=9))
    parts.append('<rect x="248" y="344" width="14" height="52" rx="7" '
                 f'fill="{_p(pal, "pants")}"/>')
    # Person sitting upright.
    parts.append(_capsule(228, 168, 58, 112, shirt))                     # torso
    parts.append(_capsule(272, 196, 96, 20, _p(pal, "accent"), rx=10))              # arm to desk
    parts.append(_capsule(224, 272, 22, 76, _p(pal, "pants")))           # thigh
    parts.append(_capsule(232, 288, 22, 72, _p(pal, "pants")))           # shin
    # Head level with screen.
    parts.append(_hair_cap(257, 138, 32, hair))
    parts.append(_face(257, 144, 32, skin))
    # Plant for cosiness.
    parts.append(_capsule(700, 330, 40, 34, _p(pal, "accent"), rx=8))
    parts.append(f'<circle cx="720" cy="308" r="24" fill="{_p(pal, "sage")}"/>')
    accent = _p(pal, "accent")
    parts.append(_svg_close())
    return "".join(parts)


def neck_stretch(palette: dict | None = None) -> str:
    """Keywords: neck, neck pain, neck stretch, tilt, stiff neck,
    headache, shoulder tension, seated stretch, break."""
    pal = palette or {}
    skin, shirt = _p(pal, "skin"), _p(pal, "shirt")
    hair, bg = _p(pal, "hair"), _p(pal, "bg")
    parts = [_svg_open(), _backdrop(bg)]
    # Chair.
    parts.append(_capsule(330, 208, 90, 132, _p(pal, "accent"), rx=26))
    parts.append(_capsule(312, 328, 128, 18, _p(pal, "accent"), rx=9))
    parts.append('<rect x="368" y="342" width="14" height="56" rx="7" '
                 f'fill="{_p(pal, "pants")}"/>')
    # Seated person, gently tilted head.
    parts.append(_capsule(344, 166, 60, 108, shirt))
    parts.append(_capsule(392, 200, 88, 20, accent := _p(pal, "accent"), rx=10))
    parts.append(_capsule(342, 264, 24, 78, _p(pal, "pants")))
    parts.append(_capsule(350, 282, 24, 74, _p(pal, "pants")))
    # Hand supporting tilted head.
    parts.append(_capsule(300, 148, 64, 20, accent, rx=10, rotate=-30,
                          cx=352, cy=156))
    parts.append(_hair_cap(322, 136, 32, hair, ) )
    parts.append(_face(322, 142, 32, skin, tilt=-18))
    # Little sparkles suggesting relief.
    for x, y in ((250, 110), (470, 130), (452, 96)):
        parts.append(f'<circle cx="{x}" cy="{y}" r="6" fill="{_p(pal, "yellow")}"/>')
    accent = _p(pal, "accent")
    parts.append(_svg_close())
    return "".join(parts)


def walking_outdoor(palette: dict | None = None) -> str:
    """Keywords: walk, walking, outdoor, park, exercise, active, steps,
    movement, daily activity, nature, health."""
    pal = palette or {}
    accent = _p(pal, "accent")
    skin, shirt = _p(pal, "skin"), _p(pal, "shirt")
    hair, bg = _p(pal, "hair"), _p(pal, "bg")
    sage = _p(pal, "sage")
    parts = [_svg_open()]
    parts.append(f'<rect x="20" y="20" width="760" height="410" rx="48" '
                 f'fill="{_p(pal, "yellow")}"/>')
    parts.append(_sun(650, 110, 34, "#FFD98E"))
    parts.append(_tree(140, 372, sage))
    parts.append(_tree(690, 372, sage))
    parts.append('<rect x="40" y="368" width="720" height="34" rx="17" '
                 f'fill="{sage}"/>')
    # Walker mid-stride.
    parts.append(_capsule(372, 172, 60, 118, shirt))                       # torso
    parts.append(_capsule(348, 188, 82, 20, _p(pal, "accent"), rx=10,
                          rotate=24, cx=372, cy=196))                      # back arm
    parts.append(_capsule(382, 184, 82, 20, _p(pal, "accent"), rx=10,
                          rotate=-24, cx=382, cy=192))                     # front arm
    parts.append(_capsule(366, 278, 24, 84, _p(pal, "pants"),
                          rotate=-22, cx=378, cy=282))                     # back leg
    parts.append(_capsule(396, 278, 24, 84, _p(pal, "pants"),
                          rotate=22, cx=408, cy=282))                      # front leg
    parts.append(_hair_cap(402, 140, 32, hair))
    parts.append(_face(402, 146, 32, skin))
    parts.append(_svg_close())
    return "".join(parts)


def lifting_correct(palette: dict | None = None) -> str:
    """Keywords: lift, lifting, lifting correctly, bend the knees, squat,
    carry, heavy object, safe lifting, box, back care."""
    pal = palette or {}
    skin, shirt = _p(pal, "skin"), _p(pal, "shirt")
    hair, bg = _p(pal, "hair"), _p(pal, "bg")
    pants = _p(pal, "pants")
    parts = [_svg_open(), _backdrop(bg)]
    parts.append('<rect x="60" y="382" width="680" height="26" rx="13" '
                 f'fill="{_p(pal, "ground")}"/>')
    # Box held low, close to body.
    parts.append('<rect x="330" y="286" width="92" height="72" rx="10" '
                 f'fill="{_p(pal, "accent")}"/>')
    parts.append('<rect x="330" y="314" width="92" height="12" fill="#E89F91"/>')
    # Squatting person: folded knees, upright straight back.
    parts.append(_capsule(352, 158, 58, 122, shirt))                       # torso
    parts.append(_capsule(398, 216, 84, 20, _p(pal, "accent"), rx=10))     # arms to box
    # Folded legs.
    parts.append(_capsule(344, 268, 26, 66, pants, rotate=-52, cx=356, cy=274))
    parts.append(_capsule(382, 268, 26, 66, pants, rotate=52, cx=394, cy=274))
    parts.append(_capsule(330, 320, 26, 58, pants))
    parts.append(_capsule(396, 320, 26, 58, pants))
    parts.append(_hair_cap(381, 128, 32, hair))
    parts.append(_face(381, 134, 32, skin))
    # Green tick-style sparkle (no X marks anywhere).
    parts.append(f'<circle cx="540" cy="140" r="26" fill="{_p(pal, "sage")}"/>')
    accent = _p(pal, "accent")
    parts.append('<path d="M 528 140 l 9 10 l 16 -20" stroke="#FFFFFF" '
                 'stroke-width="6" fill="none" stroke-linecap="round" '
                 'stroke-linejoin="round"/>')
    parts.append(_svg_close())
    return "".join(parts)


def lifting_wrong_crossed(palette: dict | None = None) -> str:
    """Keywords: lift, lifting wrong, bending, bent back, poor lifting
    posture, stooping, rounded back, comparison, avoid."""
    pal = palette or {}
    accent = _p(pal, "accent")
    skin, shirt = _p(pal, "skin"), _p(pal, "shirt")
    hair, bg = _p(pal, "hair"), _p(pal, "bg")
    pants = _p(pal, "pants")
    parts = [_svg_open(), _backdrop(bg)]
    parts.append('<rect x="60" y="382" width="680" height="26" rx="13" '
                 f'fill="{_p(pal, "ground")}"/>')
    # Box on the ground.
    parts.append('<rect x="440" y="308" width="92" height="72" rx="10" '
                 f'fill="{_p(pal, "accent")}"/>')
    parts.append('<rect x="440" y="336" width="92" height="12" fill="#E89F91"/>')
    # Person bending from the waist with straight legs and rounded back.
    parts.append(_capsule(238, 268, 30, 112, pants))                        # straight legs
    parts.append(_capsule(292, 268, 30, 112, pants))
    # Rounded-back torso reaching down towards the box.
    parts.append('<path d="M 236 268 q 30 -66 96 -52 q 62 12 96 68 q 10 18 '
                 '-8 26 q -18 8 -28 -10 q -26 -42 -68 -50 q -40 -8 -58 34 '
                 f'q -10 20 -26 10 q -12 -10 -4 -26 Z" fill="{shirt}"/>')
    parts.append(_capsule(388, 288, 70, 20, _p(pal, "accent"), rx=10,
                          rotate=28, cx=392, cy=296))                       # arms down
    parts.append(_hair_cap(430, 262, 30, hair))
    parts.append(_face(430, 268, 30, skin, tilt=35))
    parts.append(_svg_close())
    return "".join(parts)


def chiro_consult(palette: dict | None = None) -> str:
    """Keywords: chiropractor, consult, consultation, appointment, clinic,
    practitioner, treatment, visit, patient, assessment."""
    pal = palette or {}
    skin, shirt = _p(pal, "skin"), _p(pal, "shirt")
    hair, bg = _p(pal, "hair"), _p(pal, "bg")
    parts = [_svg_open(), _backdrop(bg)]
    # Small table between them.
    parts.append('<rect x="352" y="286" width="120" height="14" rx="7" '
                 f'fill="{_p(pal, "pants")}"/>')
    parts.append('<rect x="366" y="300" width="12" height="80" rx="6" '
                 f'fill="{_p(pal, "pants")}"/>')
    parts.append('<rect x="446" y="300" width="12" height="80" rx="6" '
                 f'fill="{_p(pal, "pants")}"/>')
    # Patient seated left.
    parts.append(_capsule(238, 218, 56, 106, _p(pal, "sage")))
    parts.append(_capsule(234, 314, 24, 66, _p(pal, "pants")))
    parts.append(_capsule(242, 330, 24, 62, _p(pal, "pants")))
    parts.append(_hair_cap(266, 190, 31, "#5D4638"))
    parts.append(_face(266, 196, 31, skin))
    # Chiropractor standing right, white coat.
    parts.append(_capsule(498, 152, 62, 168, _p(pal, "white")))
    parts.append(_capsule(472, 190, 76, 18, _p(pal, "accent"), rx=9,
                          rotate=26, cx=498, cy=198))                      # greeting arm
    parts.append(_capsule(502, 312, 24, 68, _p(pal, "pants")))
    parts.append(_capsule(534, 312, 24, 68, _p(pal, "pants")))
    parts.append(_hair_cap(529, 122, 33, hair))
    parts.append(_face(529, 128, 33, skin))
    # Speech bubble heart.
    parts.append(_capsule(392, 96, 96, 56, _p(pal, "white"), rx=28))
    parts.append('<path d="M 424 118 c 0 -8 12 -12 16 -4 c 4 -8 16 -4 16 4 '
                 'c 0 8 -16 16 -16 16 s -16 -8 -16 -16 Z" '
                 f'fill="{_p(pal, "accent")}"/>')
    accent = _p(pal, "accent")
    parts.append(_svg_close())
    return "".join(parts)


def hydration_water(palette: dict | None = None) -> str:
    """Keywords: water, hydrate, hydration, drink, drinking, glass of
    water, fluid, thirst, healthy habit."""
    pal = palette or {}
    skin, shirt = _p(pal, "skin"), _p(pal, "shirt")
    hair, bg = _p(pal, "hair"), _p(pal, "bg")
    parts = [_svg_open(), _backdrop(bg)]
    # Glass of water raised in the right hand.
    parts.append('<path d="M 452 168 l 10 84 q 2 14 16 14 q 14 0 16 -14 '
                 'l 10 -84 Z" fill="#CFE8F5"/>')
    parts.append('<path d="M 456 196 l 44 0 l -6 52 q -1 10 -16 10 '
                 'q -15 0 -16 -10 Z" fill="#7FC5E8"/>')
    # Person.
    parts.append(_capsule(348, 176, 60, 128, shirt))
    parts.append(_capsule(396, 196, 74, 20, _p(pal, "accent"), rx=10,
                          rotate=-38, cx=398, cy=204))                      # raised arm
    parts.append(_capsule(330, 196, 74, 20, _p(pal, "accent"), rx=10))      # other arm
    parts.append(_capsule(352, 296, 24, 84, _p(pal, "pants")))
    parts.append(_capsule(384, 296, 24, 84, _p(pal, "pants")))
    accent = _p(pal, "accent")
    parts.append(_hair_cap(378, 146, 33, hair))
    parts.append(_face(378, 152, 33, skin))
    # Droplet sparkles.
    for x, y in ((540, 130), (572, 168), (524, 190)):
        parts.append(f'<circle cx="{x}" cy="{y}" r="6" fill="#7FC5E8"/>')
    parts.append(_svg_close())
    return "".join(parts)


def heat_ice_pack(palette: dict | None = None) -> str:
    """Keywords: ice, heat, hot pack, cold pack, ice pack, heat pack,
    compress, relief, sore back, recovery, apply, therapy."""
    pal = palette or {}
    skin, shirt = _p(pal, "skin"), _p(pal, "shirt")
    hair, bg = _p(pal, "hair"), _p(pal, "bg")
    parts = [_svg_open(), _backdrop(bg)]
    # Comfy recliner.
    parts.append(_capsule(250, 170, 120, 190, _p(pal, "accent"), rx=34))
    parts.append(_capsule(238, 316, 210, 44, _p(pal, "accent"), rx=22))
    # Reclining person leaning back, legs on footrest.
    parts.append(_capsule(292, 196, 56, 116, shirt, rotate=-14,
                          cx=318, cy=250))
    parts.append(_capsule(330, 300, 26, 96, _p(pal, "pants"), rotate=-64,
                          cx=336, cy=316))
    parts.append(_capsule(344, 336, 60, 22, _p(pal, "pants"), rx=11))
    # Pack on the lower back.
    parts.append(_capsule(286, 236, 62, 46, _p(pal, "sage"), rx=14))
    accent = _p(pal, "accent")
    # Steam curls (warmth).
    parts.append('<path d="M 250 130 q 8 -14 0 -26 M 274 124 q 8 -14 0 -26" '
                 'stroke="#F4A9A0" stroke-width="5" fill="none" '
                 'stroke-linecap="round"/>')
    # Snowflake dots (cool option).
    for x, y in ((560, 140), (600, 176), (576, 110)):
        parts.append(f'<circle cx="{x}" cy="{y}" r="5" fill="#A9D6F0"/>')
    # Relaxed head against backrest.
    parts.append(_hair_cap(300, 168, 31, hair))
    parts.append(_face(300, 174, 31, skin, sleeping=True))
    parts.append(_svg_close())
    return "".join(parts)


def gentle_exercise(palette: dict | None = None) -> str:
    """Keywords: exercise, stretch, yoga, mat, cat-cow, knees to chest,
    gentle movement, mobility, floor exercise, core, rehab."""
    pal = palette or {}
    accent = _p(pal, "accent")
    skin, shirt = _p(pal, "skin"), _p(pal, "shirt")
    hair, bg = _p(pal, "hair"), _p(pal, "bg")
    parts = [_svg_open(), _backdrop(bg)]
    # Yoga mat.
    parts.append(_capsule(160, 356, 480, 34, _p(pal, "sage"), rx=17))
    # Curled-up person on the mat (knees hugged to chest).
    parts.append('<circle cx="400" cy="288" r="62" '
                 f'fill="{shirt}"/>')                                    # curled body
    parts.append(_capsule(368, 262, 96, 30, _p(pal, "pants"), rx=15,
                          rotate=24, cx=400, cy=280))                    # legs folded
    parts.append(_capsule(352, 288, 88, 24, _p(pal, "accent"), rx=12,
                          rotate=18, cx=390, cy=300))                    # hugging arms
    # Head tucked near knees.
    parts.append(_hair_cap(348, 250, 30, hair))
    parts.append(_face(348, 256, 30, skin))
    parts.append(_svg_close())
    return "".join(parts)


def happy_family_walk(palette: dict | None = None) -> str:
    """Keywords: family, kids, children, together, walk, walking, park,
    parents, lifestyle, bonding, outdoor activity."""
    pal = palette or {}
    accent = _p(pal, "accent")
    skin, shirt = _p(pal, "skin"), _p(pal, "shirt")
    hair, sage = _p(pal, "hair"), _p(pal, "sage")
    parts = [_svg_open()]
    parts.append(f'<rect x="20" y="20" width="760" height="410" rx="48" '
                 f'fill="{_p(pal, "yellow")}"/>')
    parts.append(_sun(660, 108, 32, "#FFD98E"))
    parts.append(_tree(120, 372, sage))
    parts.append('<rect x="40" y="368" width="720" height="34" rx="17" '
                 f'fill="{sage}"/>')
    # Adult 1 (taller, coral shirt).
    parts.append(_capsule(282, 178, 54, 112, _p(pal, "accent")))
    parts.append(_capsule(262, 296, 22, 74, _p(pal, "pants")))
    parts.append(_capsule(296, 296, 22, 74, _p(pal, "pants")))
    parts.append(_hair_cap(309, 150, 29, hair))
    parts.append(_face(309, 156, 29, skin))
    # Adult 2.
    parts.append(_capsule(392, 192, 52, 102, shirt))
    parts.append(_capsule(372, 300, 21, 70, _p(pal, "pants")))
    parts.append(_capsule(404, 300, 21, 70, _p(pal, "pants")))
    parts.append(_hair_cap(418, 164, 28, "#5D4638"))
    parts.append(_face(418, 170, 28, skin))
    # Child skipping between them, hands joined via short link arms.
    parts.append(_capsule(344, 258, 38, 62, _p(pal, "sage")))
    parts.append(_capsule(330, 312, 17, 52, _p(pal, "pants")))
    parts.append(_capsule(352, 312, 17, 52, _p(pal, "pants")))
    parts.append(_hair_cap(363, 234, 22, "#B08968"))
    parts.append(_face(363, 238, 22, skin))
    # Joined-hand links.
    parts.append(_capsule(330, 226, 26, 14, _p(pal, "accent"), rx=7,
                          rotate=32, cx=330, cy=232))
    parts.append(_capsule(386, 232, 26, 14, _p(pal, "accent"), rx=7,
                          rotate=-32, cx=386, cy=238))
    parts.append(_svg_close())
    return "".join(parts)


def phone_posture(palette: dict | None = None) -> str:
    """Keywords: phone, smartphone, texting, mobile, screen time, tech
    neck, looking down, device, posture comparison, scrolling."""
    pal = palette or {}
    skin, shirt = _p(pal, "skin"), _p(pal, "shirt")
    hair, bg = _p(pal, "hair"), _p(pal, "bg")
    parts = [_svg_open(), _backdrop(bg)]
    # Left figure: head dropped forward over the phone.
    parts.append(_capsule(212, 182, 54, 110, shirt))
    parts.append(_capsule(252, 214, 66, 18, _p(pal, "accent"), rx=9,
                          rotate=34, cx=256, cy=222))
    parts.append(_capsule(208, 284, 22, 78, _p(pal, "pants")))
    parts.append(_capsule(238, 284, 22, 78, _p(pal, "pants")))
    parts.append('<rect x="286" y="238" width="34" height="56" rx="8" '
                 f'fill="{_p(pal, "sage")}"/>')                           # phone low
    parts.append(_hair_cap(268, 158, 29, hair))
    parts.append(_face(274, 166, 29, skin, tilt=38))                        # head pitched down
    # Divider: soft dotted line.
    for y in range(110, 360, 26):
        parts.append(f'<circle cx="400" cy="{y}" r="4" fill="#CBB8A8"/>')
    # Right figure: phone raised to eye level, upright.
    parts.append(_capsule(492, 182, 54, 110, _p(pal, "sage")))
    parts.append(_capsule(532, 196, 66, 18, _p(pal, "accent"), rx=9,
                          rotate=-58, cx=536, cy=204))
    parts.append(_capsule(488, 284, 22, 78, _p(pal, "pants")))
    parts.append(_capsule(518, 284, 22, 78, _p(pal, "pants")))
    parts.append('<rect x="576" y="146" width="34" height="56" rx="8" '
                 f'fill="{_p(pal, "accent")}"/>')                         # phone at eyes
    accent = _p(pal, "accent")
    parts.append(_hair_cap(519, 154, 29, "#5D4638"))
    parts.append(_face(519, 160, 29, skin))
    parts.append(_svg_close())
    return "".join(parts)


# ---------------------------------------------------------------------------
# Academic paper-card SVG (used by generate_blog.py for PubMed references)
# ---------------------------------------------------------------------------


def _card_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


def _wrap_card_text(text: str, max_chars: int, max_lines: int) -> list[str]:
    """Greedy word-wrap for card text lines."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len(words) > len(" ".join(lines).split()):
        # Ellipsize the final line if content was truncated.
        last = lines[-1]
        if " ".join(words) != " ".join(" ".join(lines).split()):
            while len(last) > 3 and len(last) > max_chars - 1:
                last = last[:-2]
            lines[-1] = last.rstrip() + "…"
    return lines or [""]


def add_paper_card_svg(title: str, authors: str, journal: str, year: str,
                       doi: str, out_path: str,
                       open_access: bool = False) -> str:
    """Render a clean academic citation-card SVG and write it to out_path.

    White background, top colour bar, big two-line title summary,
    authors/journal/year line, DOI at the bottom and an optional
    "Open Access" badge. Returns the SVG markup written.
    """
    title = _card_escape(re.sub(r"\s+", " ", title or "").strip())
    authors = _card_escape(re.sub(r"\s+", " ", authors or "").strip())
    journal = _card_escape(re.sub(r"\s+", " ", journal or "").strip())
    year = _card_escape(str(year or "").strip())
    doi = _card_escape((doi or "").strip())

    ACCENT = "#1B6CA8"      # deep clinical blue
    ACCENT_SOFT = "#EAF2F9"
    INK = "#1C2B36"
    MUTED = "#5A6B78"

    parts: list[str] = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 450" '
        'role="img">',
        '<rect width="800" height="450" fill="#FFFFFF"/>',
        f'<rect width="800" height="14" fill="{ACCENT}"/>',
        # subtle side accent strip
        f'<rect x="0" y="14" width="10" height="436" fill="{ACCENT_SOFT}"/>',
    ]

    # Small document icon.
    parts.append(
        f'<g fill="{ACCENT}">'
        '<rect x="52" y="58" width="34" height="44" rx="5"/>'
        '<rect x="60" y="70" width="18" height="4" rx="2" fill="#FFFFFF"/>'
        '<rect x="60" y="80" width="18" height="4" rx="2" fill="#FFFFFF"/>'
        '<rect x="60" y="90" width="12" height="4" rx="2" fill="#FFFFFF"/>'
        '</g>'
    )

    # Open Access badge (top right).
    if open_access:
        parts.append(
            f'<g><rect x="618" y="56" width="132" height="34" rx="17" '
            f'fill="{ACCENT_SOFT}"/>'
            f'<circle cx="640" cy="73" r="8" fill="{ACCENT}"/>'
            f'<path d="M 635 73 a 5 5 0 1 1 10 0 M 637 74 h 6 v 6 h -6 z" '
            f'stroke="#FFFFFF" stroke-width="2" fill="#FFFFFF"/>'
            f'<text x="656" y="79" font-family="Arial, Helvetica, sans-serif" '
            f'font-size="16" font-weight="700" fill="{ACCENT}">Open Access'
            f'</text></g>'
        )

    # Title — up to two large lines.
    title_lines = _wrap_card_text(title, max_chars=42, max_lines=2)
    for i, line in enumerate(title_lines):
        parts.append(
            '<text x="110" y="' + str(92 + i * 46) + '" '
            'font-family="Georgia, \'Times New Roman\', serif" '
            f'font-size="38" font-weight="700" fill="{INK}">{line}</text>'
        )
    ty = 92 + max(1, len(title_lines)) * 46 - 46

    # Divider.
    parts.append(
        f'<rect x="110" y="{ty + 40}" width="180" height="5" rx="2.5" '
        f'fill="{ACCENT}"/>'
    )

    # Authors line.
    author_lines = _wrap_card_text(authors, max_chars=64, max_lines=2)
    ay = ty + 92
    for i, line in enumerate(author_lines):
        parts.append(
            f'<text x="110" y="{ay + i * 32}" '
            'font-family="Arial, Helvetica, sans-serif" font-size="24" '
            f'font-weight="600" fill="{MUTED}">{line}</text>'
        )
    ay += max(1, len(author_lines)) * 32

    # Journal · Year.
    jy = journal
    if year:
        jy = f"{journal} ({year})" if journal else str(year)
    jy_line = _wrap_card_text(jy, max_chars=64, max_lines=1)
    parts.append(
        f'<text x="110" y="{ay + 22}" font-family="Arial, Helvetica, '
        f'sans-serif" font-size="22" font-style="italic" fill="{ACCENT}">'
        f'{jy_line[0]}</text>'
    )

    # DOI footer.
    footer_y = 396
    parts.append(
        f'<rect x="0" y="414" width="800" height="36" fill="{ACCENT_SOFT}"/>'
    )
    doi_text = _wrap_card_text(doi or "DOI unavailable", max_chars=72,
                               max_lines=1)[0]
    label = "DOI:" if doi else ""
    parts.append(
        f'<text x="52" y="{footer_y}" font-family="Arial, Helvetica, '
        f'sans-serif" font-size="19" font-weight="700" fill="{ACCENT}">'
        f'{label}</text>'
        f'<text x="{118 if label else 52}" y="{footer_y}" '
        'font-family="Arial, Helvetica, sans-serif" font-size="19" '
        f'fill="{MUTED}">{doi_text}</text>'
    )

    parts.append("</svg>")
    svg = "".join(parts)
    from pathlib import Path as _Path

    _Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    _Path(out_path).write_text(svg, encoding="utf-8")
    return svg


# Scene registry used by generate_blog.py.
SCENES = {
    "side_sleeping": side_sleeping,
    "back_sleeping": back_sleeping,
    "morning_stretch": morning_stretch,
    "desk_setup": desk_setup,
    "neck_stretch": neck_stretch,
    "walking_outdoor": walking_outdoor,
    "lifting_correct": lifting_correct,
    "lifting_wrong_crossed": lifting_wrong_crossed,
    "chiro_consult": chiro_consult,
    "hydration_water": hydration_water,
    "heat_ice_pack": heat_ice_pack,
    "gentle_exercise": gentle_exercise,
    "happy_family_walk": happy_family_walk,
    "phone_posture": phone_posture,
}
