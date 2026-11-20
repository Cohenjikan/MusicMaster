#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 score_to_jianpu 产出的简谱文本渲染成纯 SVG(不依赖 LilyPond)。

用法:
    python scripts/render_jianpu_svg.py 简谱.txt [-o jianpu.svg]

文本格式见 musicmaster/convert/convert.py:前 3 行为 1=X / 拍号 / tempo,
之后以空格分隔的音符 token 与 | 小节线。渲染只做排版,不改写记谱含义。
"""
from __future__ import annotations

import argparse
import html
from pathlib import Path

WIDTH = 1600
PAD = 48
LINE_H = 58
NOTE_DX = 34
BAR_DX = 26
FONT_NOTE = 26
FONT_HEAD = 20


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path, help="简谱文本(score_to_jianpu 输出)")
    ap.add_argument("-o", "--output", type=Path, help="输出 SVG(默认同目录 jianpu.svg)")
    a = ap.parse_args()

    lines = a.input.read_text(encoding="utf-8").splitlines()
    header = lines[:3] if len(lines) >= 3 else lines
    tokens = " ".join(lines[3:]).split() if len(lines) > 3 else []

    out = a.output or a.input.with_name("jianpu.svg")
    out.parent.mkdir(parents=True, exist_ok=True)

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" '
             f'viewBox="0 0 {WIDTH} 400" font-family="Segoe UI, SimHei, sans-serif">',
             '<rect width="100%" height="100%" fill="#fff"/>',
             '<style>.head{fill:#374151}.note{fill:#111827}.bar{stroke:#6b7280;stroke-width:1.5}</style>']

    y = PAD
    for i, line in enumerate(header):
        anchor = "start" if i < 2 else "end"
        x = PAD if i < 2 else WIDTH - PAD
        parts.append(f'<text class="head" x="{x}" y="{y}" font-size="{FONT_HEAD}" '
                     f'text-anchor="{anchor}">{html.escape(line)}</text>')
    y += LINE_H

    x = PAD
    max_y = y
    bar_count = 0
    for tok in tokens:
        if tok == "|":
            bar_count += 1
            x += BAR_DX
            y2 = y + 10
            parts.append(f'<line class="bar" x1="{x}" y1="{y2-24}" x2="{x}" y2="{y2+2}"/>')
            x += BAR_DX
        else:
            parts.append(f'<text class="note" x="{x}" y="{y}" font-size="{FONT_NOTE}" '
                         f'font-family="Consolas, Menlo, monospace">{html.escape(tok)}</text>')
            x += NOTE_DX
        if x > WIDTH - PAD:
            x = PAD
            y += LINE_H
            max_y = max(max_y, y)

    max_y = max(max_y + LINE_H, 400)
    # viewBox 高度按内容伸缩
    parts[0] = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" '
                f'viewBox="0 0 {WIDTH} {max_y}" font-family="Segoe UI, SimHei, sans-serif">')
    parts.append("</svg>")
    out.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(f"[OK] {out}  ({len(tokens)} tokens, {bar_count} bars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
