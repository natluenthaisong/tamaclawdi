#!/usr/bin/env python3
"""Render every TamaClaude mascot (Clawd) state across its animation loop into
one transparent sprite-sheet PNG, plus a labeled preview PNG.

    python3 tools/gen_spritesheet.py                    # -> ./clawd-spritesheet.png (+ preview)
    python3 tools/gen_spritesheet.py --out /tmp --px 12 --frames 12
    python3 tools/gen_spritesheet.py --disconnected     # the gray, offline Clawd
    python3 tools/gen_spritesheet.py --no-preview       # sprite sheet only

Frames come straight from the firmware's own vector generator
(gen/mascot.py -> gen/render.py), so they match exactly what the board draws.
Rows are states in enum order; columns sweep the animation phase 0 -> 1.
Requires Pillow (already used by tools/preview.py and tools/make_icon.py).
"""
from __future__ import annotations

import argparse
import os
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)

from gen import mascot, render          # noqa: E402
from gen.rects import bounds            # noqa: E402
from gen.config import PAL              # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

KEY = (255, 0, 255)  # magenta chroma key, punched out to alpha (rects are aliased)


def hexrgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def punch_chroma(im: Image.Image) -> Image.Image:
    im = im.convert("RGBA")
    data = im.get_flattened_data() if hasattr(im, "get_flattened_data") else list(im.getdata())
    im.putdata([(0, 0, 0, 0) if (p[0], p[1], p[2]) == KEY else p for p in data])
    return im


def main() -> None:
    ap = argparse.ArgumentParser(description="Render Clawd's animation states to a sprite sheet.")
    ap.add_argument("--out", default=".", help="output directory (default: cwd)")
    ap.add_argument("--px", type=int, default=10, help="pixels per unit (the board runs at 4)")
    ap.add_argument("--frames", type=int, default=8, help="animation frames per state")
    ap.add_argument("--disconnected", action="store_true", help="render the gray offline Clawd")
    ap.add_argument("--no-preview", action="store_true", help="skip the labeled preview PNG")
    args = ap.parse_args()

    states = mascot.all_states()
    phases = [i / args.frames for i in range(args.frames)]
    connected = not args.disconnected
    px = args.px

    # one uniform cell box = union of every state's bounds across the loop, so
    # all cells share a size and Clawd stays registered on the same baseline
    inf = float("inf")
    x0 = y0 = inf
    x1 = y1 = -inf
    for st in states:
        for ph in phases:
            a, b, c, d = bounds(mascot.build(st, ph, connected, 0))
            x0, y0, x1, y1 = min(x0, a), min(y0, b), max(x1, c), max(y1, d)
    pad = 0.6
    box = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    cw = round((box[2] - box[0]) * px)
    ch = round((box[3] - box[1]) * px)

    cells = {
        (st, ph): punch_chroma(render.render_rects(mascot.build(st, ph, connected, 0), px, box, "#ff00ff", True))
        for st in states
        for ph in phases
    }

    os.makedirs(args.out, exist_ok=True)

    sheet = Image.new("RGBA", (cw * len(phases), ch * len(states)), (0, 0, 0, 0))
    for r, st in enumerate(states):
        for c, ph in enumerate(phases):
            sheet.alpha_composite(cells[(st, ph)], (c * cw, r * ch))
    sheet_path = os.path.join(args.out, "clawd-spritesheet.png")
    sheet.save(sheet_path)
    print("wrote", sheet_path)

    if not args.no_preview:
        lab, title = 150, 92
        try:
            f_title = ImageFont.load_default(size=40)
            f_row = ImageFont.load_default(size=24)
            f_small = ImageFont.load_default(size=18)
        except TypeError:  # very old Pillow: non-scalable default font
            f_title = f_row = f_small = ImageFont.load_default()
        prev = Image.new("RGBA", (lab + cw * len(phases), title + ch * len(states)), hexrgb(PAL.bg) + (255,))
        dr = ImageDraw.Draw(prev)
        tag = "offline" if args.disconnected else "all"
        dr.text((20, 22), f"Clawd - {tag} TamaClaude mascot states", fill=hexrgb(PAL.text), font=f_title)
        for c in range(len(phases)):
            dr.text((lab + c * cw + cw // 2 - 16, title - 26), f"{c + 1}/{len(phases)}",
                    fill=hexrgb(PAL.text_dim), font=f_small)
        for r, st in enumerate(states):
            y = title + r * ch
            dr.line([(0, y), (prev.width, y)], fill=hexrgb(PAL.outline), width=1)
            dr.text((16, y + ch // 2 - 12), st, fill=hexrgb(PAL.text), font=f_row)
            for c, ph in enumerate(phases):
                prev.alpha_composite(cells[(st, ph)], (lab + c * cw, y))
        prev_path = os.path.join(args.out, "clawd-animations-preview.png")
        prev.save(prev_path)
        print("wrote", prev_path)

    print(f"states={len(states)} frames={args.frames} cell={cw}x{ch} sheet={sheet.size}")


if __name__ == "__main__":
    main()
