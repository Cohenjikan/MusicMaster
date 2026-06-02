#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分部·扒谱入口:清唱 wav(可选段) → notes.json / out.mid / out.musicxml(带正确调号)
→ 五线谱 staff.svg(Verovio) + 简谱 jianpu.pdf(jianpu-ly+LilyPond)。
核心:transcribe2(音符级中位数赋音高,修半音汤)+ engine.target_to_musicxml(写对调号,1=主音)+ engine.render。

用法(在 .venv 跑;简谱图需 LilyPond 在 PATH):
  PYTHONUTF8=1 PY transcribe_entry.py IN.wav --out OUTDIR --key G [--offset 84 --dur 28 --fmax C5 --snap G:maj --bpm 0]
"""
import argparse, json, sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
import importlib
from . import transcribe2
# musicmaster.core.__init__ 把同名函数绑到了包属性上,普通 import 会拿到函数而非子模块;
# 用 import_module 强制取真正的子模块对象。
eng_t = importlib.import_module("musicmaster.core.transcribe")
eng_r = importlib.import_module("musicmaster.core.render")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--out", required=True)
    ap.add_argument("--key", default="G", help="渲染调,如 G(=G大调)。已知调务必传,别让它瞎猜。")
    ap.add_argument("--offset", type=float, default=0.0)
    ap.add_argument("--dur", type=float, default=None)
    ap.add_argument("--fmin", default="C2")
    ap.add_argument("--fmax", default="C5")
    # 默认生产配置(对照权威副歌谱实测最佳):CREPE f0 + 孤立毛刺平滑 + 不吸附(保留真实#4#5)。
    ap.add_argument("--f0", dest="f0_method", default="crepe", choices=["crepe", "pyin"])
    ap.add_argument("--snap", default="off", help="off / G:maj 等;默认 off(权威副歌含真实#4#5,吸附反而降相似度)")
    ap.add_argument("--snap-tol", type=float, default=0.65)
    ap.add_argument("--no-smooth", dest="smooth", action="store_false")
    ap.add_argument("--simplify", action="store_true", help="lead-sheet风:激进合并melisma,贴近出版简谱(相似度更高但会简化真实短音)")
    ap.add_argument("--crepe-conf", type=float, default=0.5)
    ap.add_argument("--jump", type=float, default=0.7)
    ap.add_argument("--min-ms", type=float, default=90.0)
    ap.add_argument("--bpm", type=float, default=0.0)
    ap.add_argument("--jianpu", default="pdf", choices=["pdf", "svg"])
    a = ap.parse_args(argv)

    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    target, info = transcribe2.transcribe(
        a.input, offset=a.offset, dur=a.dur, fmin=a.fmin, fmax=a.fmax,
        snap=a.snap, snap_tol=a.snap_tol, jump=a.jump, min_ms=a.min_ms,
        smooth=a.smooth, smooth_max_ms=(160.0 if a.simplify else 90.0),
        simplify=a.simplify, f0_method=a.f0_method, crepe_conf=a.crepe_conf,
    )
    if a.bpm > 0:
        target["tempo"] = a.bpm
    (out / "notes.json").write_text(json.dumps(target, ensure_ascii=False, indent=2), encoding="utf-8")
    eng_t.target_to_pretty_midi(target).write(str(out / "out.mid"))
    mxl = eng_t.target_to_musicxml(target, out / "out.musicxml", key_name=a.key)
    res = {}
    try:
        res = eng_r.render(str(mxl), str(out), formats=("staff", "jianpu"), jianpu_format=a.jianpu)
    except Exception as e:
        print("RENDER-ERR", repr(e))
    print("INFO", json.dumps(info, ensure_ascii=False))
    print("RENDER", {k: str(v) for k, v in res.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
