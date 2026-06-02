#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""规整 notes.json 便于全曲记谱:① 首音平移到 ~0(去掉前奏空小节);
② 把超长间隙(间奏/前奏空白)压到 maxgap 秒(避免几十秒空小节 + jianpu-ly barcheck 失败)。
不改音高,只压缩静默。用法: PY normalize_notes.py in.json out.json [maxgap=2.0]"""
import json, sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if len(argv) < 2:
        print("用法: python -m musicmaster.transcribe.tools.normalize_notes in.json out.json [maxgap=2.0]")
        return 2
    inp, out = argv[0], argv[1]
    maxgap = float(argv[2]) if len(argv) > 2 else 2.0
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
    span = res[-1]["end_s"] if res else 0.0
    print(f"normalized {len(ns)} notes -> {out}  (maxgap={maxgap}s, span {span:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
