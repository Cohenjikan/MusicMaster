#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""规整 notes.json 便于全曲记谱:① 首音平移到 ~0(去掉前奏空小节);
② 把超长间隙(间奏/前奏空白)压到 maxgap 秒(避免几十秒空小节 + jianpu-ly barcheck 失败)。
不改音高,只压缩静默。用法: PY normalize_notes.py in.json out.json [maxgap=2.0]"""
import json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

inp, out = sys.argv[1], sys.argv[2]
maxgap = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0
d = json.loads(Path(inp).read_text(encoding="utf-8"))
ns = sorted(d.get("notes", []), key=lambda x: x["start_s"])
res, prev_end, cum = [], 0.0, None
for n in ns:
    if cum is None:
        cum = n["start_s"] - 0.1  # 首音移到 0.1s
    s = n["start_s"] - cum
    e = n["end_s"] - cum
    gap = s - prev_end
    if gap > maxgap:
        extra = gap - maxgap
        s -= extra; e -= extra; cum += extra
    res.append({"midi": int(n["midi"]), "start_s": round(s, 4), "end_s": round(e, 4)})
    prev_end = e
d["notes"] = res
Path(out).write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"normalized {len(ns)} notes -> {out}  (maxgap={maxgap}s, span {res[-1]['end_s']:.1f}s)")
