"""分块全曲修音(CLI)—— 包装验证过的 run_qt4.template_pitcher,处理任意长度。

不改 run_qt4。策略:验证配方是 30s 量级操作(8GB 显存),所以把整段切成
重叠 30s 窗,逐窗跑 template_pitcher(原样),再 raised-cosine 交叉淡入拼回完整时间线。
≤一个窗(≤30s)时即单窗 = 等价单次 run_qt4。

用法(cwd 必须在 DiffPitcher,且 PYTHONPATH 含 DiffPitcher,以便 import run_qt4 + 读 ckpts):
  python run_full.py --source S --ref R --out O [--steps 150] [--shift -12] [--eta 0] [--no-clean]
                     [--window 30] [--overlap 5]
"""
import argparse
import os
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np
import soundfile as sf
import librosa

# 验证配方(勿改 run_qt4);本脚本与 run_qt4.py 同目录,import 同目录那份
from run_qt4 import template_pitcher, load_models, sr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--steps", type=int, default=150)
    ap.add_argument("--shift", type=int, default=-12)
    ap.add_argument("--eta", type=float, default=0.0)
    ap.add_argument("--no-clean", dest="clean", action="store_false")
    ap.set_defaults(clean=True)
    ap.add_argument("--window", type=float, default=30.0)
    ap.add_argument("--overlap", type=float, default=5.0)
    a = ap.parse_args()

    WIN, OV = a.window, a.overlap
    HOP = WIN - OV
    if HOP <= 0:  # overlap>=window 会让切窗 while 死循环(修审计 M7)
        raise SystemExit(f"[run_full] 参数错误:overlap({OV}) 必须 < window({WIN}),否则切窗会死循环。")

    # 任意格式/采样率/长度 → 24k 单声道;两者裁到等长(time-aligned)
    src, _ = librosa.load(a.source, sr=sr, mono=True)
    ref, _ = librosa.load(a.ref, sr=sr, mono=True)
    n = min(len(src), len(ref))
    src, ref = src[:n].astype(np.float32), ref[:n].astype(np.float32)
    total = n / sr
    starts = []
    t = 0.0
    while t < total - 1e-6:
        starts.append(t)
        t += HOP
    if not starts:
        starts = [0.0]
    n_win = len(starts)
    print(f"[run_full] dur={total:.2f}s  windows={n_win}  win={WIN}s overlap={OV}s "
          f"steps={a.steps} shift={a.shift} eta={a.eta} clean={a.clean}", flush=True)

    model, hifigan = load_models()
    tmp = tempfile.mkdtemp(prefix="diffpitch_win_")
    sp, rp = os.path.join(tmp, "s.wav"), os.path.join(tmp, "r.wav")

    win_outs, win_start = [], []
    t0all = time.time()
    for i, t0 in enumerate(starts):
        s0 = int(round(t0 * sr))
        s1 = min(int(round((t0 + WIN) * sr)), n)
        sf.write(sp, src[s0:s1], sr)
        sf.write(rp, ref[s0:s1], sr)
        o = template_pitcher(sp, rp, model, hifigan,
                             steps=a.steps, shift_semi=a.shift, eta=a.eta, clean=a.clean)
        o = np.asarray(o, dtype=np.float32).reshape(-1)
        win_outs.append(o)
        win_start.append(s0)
        print(f"[win {i + 1}/{n_win}] t0={t0:.2f}s out_samp={o.size}", flush=True)
    print(f"[run_full] diffusion total={time.time() - t0all:.1f}s", flush=True)

    # raised-cosine 交叉淡入拼接
    ov = int(round(OV * sr))
    ramp = np.arange(ov, dtype=np.float64) / max(ov - 1, 1)
    fade_in = np.sin(0.5 * np.pi * ramp) ** 2
    fade_out = 1.0 - fade_in
    # 时间线长度 = 所有窗的最远延伸(短输入时第一窗可能比最后一窗更靠后,故用 max 不能只看最后一窗)
    timeline = max(s + len(w) for w, s in zip(win_outs, win_start))
    out = np.zeros(timeline, dtype=np.float64)
    written = 0
    for i, (w, s0) in enumerate(zip(win_outs, win_start)):
        w = w.astype(np.float64)
        if i == 0:
            out[s0:s0 + len(w)] = w
            written = s0 + len(w)
            continue
        xf = min(ov, len(w), max(0, written - s0))
        if xf <= 0:
            out[s0:s0 + len(w)] = w
            written = max(written, s0 + len(w))
            continue
        out[s0:s0 + xf] = out[s0:s0 + xf] * fade_out[:xf] + w[:xf] * fade_in[:xf]
        rest = w[xf:]
        rs = s0 + xf
        out[rs:rs + len(rest)] = rest
        written = max(written, rs + len(rest))
    out = np.clip(out[:written], -1.0, 1.0).astype(np.float32)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    sf.write(a.out, out, sr)
    dur = len(out) / sr
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    print(f"[run_full] wrote {a.out}  dur={dur:.2f}s  peak={peak:.4f}  windows={n_win}", flush=True)
    assert peak > 0.01, f"silent output (peak={peak})"


if __name__ == "__main__":
    main()
