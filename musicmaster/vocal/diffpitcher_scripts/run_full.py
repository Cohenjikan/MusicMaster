"""Chunked FULL-SONG runner for the validated DiffPitcher pitch-correction pipeline.

Does NOT modify run_qt4.py -- it imports the validated recipe from it:
  template_pitcher(source, pitch_ref, model, hifigan, steps, shift_semi, eta, clean)
  load_models(), sr (==24000).

Strategy (long audio on an 8GB GPU): the validated recipe is a 30s-scale operation
(get_world_mel / torchcrepe / fastdtw / 100-step diffusion).  We therefore window the
full ~270s song into overlapping 30s windows, run template_pitcher UNCHANGED on each
window (writing per-window temp src/ref wavs so the file-path API is reused verbatim),
then CROSSFADE-CONCATENATE the per-window outputs back onto one full timeline.

Windowing:
  window_len = 30.0 s, overlap = 5.0 s, hop = 25.0 s.
  Window starts at 0, 25, 50, ...; the LAST window's end is clamped to the audio end
  (so the last window may be shorter than 30s, which template_pitcher handles fine).

Crossfade stitch:
  Window i is placed at sample round(t0_i * sr) on the output timeline.  Where window i
  and window i+1 overlap (a 5s region), a raised-cosine (Hann-based) equal-power-ish
  crossfade fades window i OUT while fading window i+1 IN -> no hard seams.  Each
  per-window output may be a few ms longer than its input slice (mel %8 padding); we
  anchor by the INTENDED start sample and let the 5s crossfade absorb the small length
  difference, trimming any tail that runs past the next window's region / the timeline.

Seam check:
  After stitching, for every window boundary we report RMS(dB) just-before vs just-after
  the boundary sample; a working crossfade should show no >10 dB jump.

Outputs:
  C:/Users/Cohen/Desktop/singer/shared/samples/out/修音全曲_s100.wav   (stitched, 24k)
  C:/Users/Cohen/Desktop/singer/shared/samples/out/我的清唱_全曲.wav     (src copy, 24k, A/B)
"""
import os
import sys
import time
import tempfile

# Force UTF-8 stdout/stderr. run_qt4._cents_diag prints librosa.hz_to_note(...) which
# can contain the SHARP sign U+266F (e.g. "C#4"); under a piped Windows console Python
# defaults stdout to the GBK codepage and raises UnicodeEncodeError on that glyph.
# This does NOT touch run_qt4.py -- it only changes how THIS process encodes its stdout.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np
import soundfile as sf
import librosa

# import the VALIDATED recipe (do not touch run_qt4.py)
from run_qt4 import template_pitcher, load_models, sr

# ---- paths ----
SRC_FULL = r"C:\Users\Cohen\Desktop\singer\neural\work\src_full.wav"
REF_FULL = r"C:\Users\Cohen\Desktop\singer\neural\work\ref_full.wav"
OUT_DIR = r"C:\Users\Cohen\Desktop\singer\shared\samples\out"
# STEPS overridable via argv[1] (default 100); output auto-named by step count
STEPS = int(sys.argv[1]) if len(sys.argv) > 1 else 100
OUT_FULL = os.path.join(OUT_DIR, f"修音全曲_s{STEPS}.wav")
OUT_SRC_COPY = os.path.join(OUT_DIR, "我的清唱_全曲.wav")

# ---- recipe knobs (validated) ----
SHIFT_SEMI = -12
ETA = 0
CLEAN = True

# ---- windowing ----
WINDOW_LEN = 30.0   # s
OVERLAP = 5.0       # s
HOP = WINDOW_LEN - OVERLAP  # 25.0 s


def rms_db(x):
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return -120.0
    return 10.0 * np.log10(np.mean(x ** 2) + 1e-12)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    src_full, sr_s = sf.read(SRC_FULL)
    ref_full, sr_r = sf.read(REF_FULL)
    assert sr_s == sr and sr_r == sr, f"sr mismatch: src={sr_s} ref={sr_r} expected={sr}"
    if src_full.ndim > 1:
        src_full = src_full.mean(1)
    if ref_full.ndim > 1:
        ref_full = ref_full.mean(1)
    src_full = src_full.astype(np.float32)
    ref_full = ref_full.astype(np.float32)

    # they are time-aligned (start at t=0); use the common length
    n_common = min(len(src_full), len(ref_full))
    src_full = src_full[:n_common]
    ref_full = ref_full[:n_common]
    total_dur = n_common / sr
    print(f"[run_full] src_full={len(src_full)} ref_full(common)={n_common} "
          f"dur={total_dur:.3f}s  steps={STEPS} shift={SHIFT_SEMI} eta={ETA} clean={CLEAN}")

    # build window start times: 0, 25, 50, ... while start < total_dur
    starts = []
    t = 0.0
    while t < total_dur - 1e-6:
        starts.append(t)
        t += HOP
    # if only one window would be created but audio > window_len, the while still covers it
    n_win = len(starts)
    print(f"[run_full] windows: N={n_win}  window_len={WINDOW_LEN}s overlap={OVERLAP}s hop={HOP}s")

    model, hifigan = load_models()
    print("[run_full] models loaded")

    tmpdir = tempfile.mkdtemp(prefix="diffpitch_win_")
    src_win_path = os.path.join(tmpdir, "src_win.wav")
    ref_win_path = os.path.join(tmpdir, "ref_win.wav")

    win_outs = []          # per-window float32 output arrays (@24k)
    win_start_samp = []    # intended placement sample on the output timeline
    per_win_secs = []

    total_diff_t0 = time.time()
    for i, t0 in enumerate(starts):
        s0 = int(round(t0 * sr))
        s1 = min(int(round((t0 + WINDOW_LEN) * sr)), n_common)  # clamp last window
        src_slice = src_full[s0:s1]
        ref_slice = ref_full[s0:s1]
        slice_dur = (s1 - s0) / sr

        sf.write(src_win_path, src_slice, sr)
        sf.write(ref_win_path, ref_slice, sr)

        w_t0 = time.time()
        out_win = template_pitcher(src_win_path, ref_win_path, model, hifigan,
                                   steps=STEPS, shift_semi=SHIFT_SEMI, eta=ETA, clean=CLEAN)
        w_dt = time.time() - w_t0
        out_win = np.asarray(out_win, dtype=np.float32).reshape(-1)
        peak = float(np.max(np.abs(out_win))) if out_win.size else 0.0

        win_outs.append(out_win)
        win_start_samp.append(s0)
        per_win_secs.append(w_dt)
        print(f"[win {i+1}/{n_win}] t0={t0:7.2f}s slice={slice_dur:5.2f}s "
              f"in_samp={s1-s0} out_samp={out_win.size} peak={peak:.4f} "
              f"runtime={w_dt:.1f}s", flush=True)

    total_diff_dt = time.time() - total_diff_t0
    print(f"[run_full] TOTAL diffusion time over {n_win} windows = {total_diff_dt:.1f}s "
          f"({total_diff_dt/60:.2f} min)")

    # ---- crossfade-concatenate ----
    ov = int(round(OVERLAP * sr))  # overlap length in samples
    # raised-cosine (Hann half) fade curves of length ov
    # fade_in goes 0->1, fade_out goes 1->0; sin^2 / cos^2 pair = constant-power-ish
    ramp = np.arange(ov, dtype=np.float64) / max(ov - 1, 1)
    fade_in = np.sin(0.5 * np.pi * ramp) ** 2     # 0 -> 1, raised cosine
    fade_out = 1.0 - fade_in                       # 1 -> 0

    # output timeline length: last window placement + its length, but cap near total_dur
    timeline_len = win_start_samp[-1] + len(win_outs[-1])
    out = np.zeros(timeline_len, dtype=np.float64)
    written_upto = 0  # exclusive sample index already finalized in `out`

    boundary_samples = []  # boundary == start sample of window i (i>=1) on timeline

    for i, (out_win, s0) in enumerate(zip(win_outs, win_start_samp)):
        w = out_win.astype(np.float64)
        if i == 0:
            out[s0:s0 + len(w)] = w
            written_upto = s0 + len(w)
            continue

        boundary_samples.append(s0)
        # crossfade region is [s0, s0+ov) overlapping the previous window's tail.
        xf_len = min(ov, len(w), max(0, written_upto - s0))
        if xf_len <= 0:
            # no overlap available (shouldn't happen with these params) -> butt-join
            out[s0:s0 + len(w)] = w
            written_upto = max(written_upto, s0 + len(w))
            continue

        fi = fade_in[:xf_len]
        fo = fade_out[:xf_len]
        # existing (previous-window) content in the crossfade region
        prev_region = out[s0:s0 + xf_len].copy()
        new_region = w[:xf_len]
        out[s0:s0 + xf_len] = prev_region * fo + new_region * fi
        # the remainder of this window goes straight in after the crossfade region
        rest = w[xf_len:]
        rs = s0 + xf_len
        out[rs:rs + len(rest)] = rest
        written_upto = max(written_upto, rs + len(rest))

    out = out[:written_upto]

    # ---- clamp to avoid clipping ----
    out = np.clip(out, -1.0, 1.0).astype(np.float32)

    # ---- write stitched result ----
    sf.write(OUT_FULL, out, sr)
    dur = len(out) / sr
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    rms = float(np.sqrt(np.mean(out.astype(np.float64) ** 2))) if out.size else 0.0
    print(f"[run_full] wrote {OUT_FULL}  dur={dur:.3f}s  peak={peak:.5f}  rms={rms:.5f}")

    # ---- assertions ----
    chk, chk_sr = sf.read(OUT_FULL)
    chk_dur = len(chk) / chk_sr
    chk_peak = float(np.max(np.abs(chk))) if chk.size else 0.0
    assert chk_sr == sr, f"sr assertion failed: {chk_sr}"
    assert chk_peak > 0.01, f"peak (non-silent) assertion failed: {chk_peak}"
    assert 250.0 <= chk_dur <= 290.0, f"duration ~270s assertion failed: {chk_dur}"
    print(f"[verify] reload dur={chk_dur:.3f}s peak={chk_peak:.5f} sr={chk_sr}  OK (~270s, non-silent)")

    # ---- SEAM CHECK: RMS(dB) just-before vs just-after each window boundary ----
    probe = int(round(0.050 * sr))  # 50ms probe windows on each side
    print(f"[seam] per-boundary RMS(dB) before|after (probe={probe} samp / 50ms); "
          f"flag = |delta|>10 dB")
    max_jump = 0.0
    worst = None
    for k, b in enumerate(boundary_samples):
        a0 = max(0, b - probe)
        before = out[a0:b]
        after = out[b:b + probe]
        rb = rms_db(before)
        ra = rms_db(after)
        d = abs(ra - rb)
        flag = "  <-- >10dB JUMP" if d > 10.0 else ""
        if d > max_jump:
            max_jump = d
            worst = (k + 1, b / sr, rb, ra, d)
        print(f"[seam] boundary {k+1:2d} @ t={b/sr:7.2f}s  before={rb:7.2f}dB  "
              f"after={ra:7.2f}dB  |delta|={d:5.2f}dB{flag}")
    if worst is not None:
        print(f"[seam] WORST boundary: #{worst[0]} @ t={worst[1]:.2f}s "
              f"before={worst[2]:.2f}dB after={worst[3]:.2f}dB |delta|={worst[4]:.2f}dB")
    print(f"[seam] max |delta| across all boundaries = {max_jump:.2f} dB  "
          f"({'ALL CONTINUOUS (<10dB)' if max_jump <= 10.0 else 'SOME >10dB -- CHECK'})")

    # ---- copy src_full for A/B ----
    src_cp, _ = sf.read(SRC_FULL)
    if src_cp.ndim > 1:
        src_cp = src_cp.mean(1)
    sf.write(OUT_SRC_COPY, src_cp.astype(np.float32), sr)
    print(f"[run_full] copied src -> {OUT_SRC_COPY}  dur={len(src_cp)/sr:.3f}s")

    print("[run_full] DONE")


if __name__ == "__main__":
    main()
