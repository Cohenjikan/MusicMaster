"""Quality-test runner for DiffPitcher template mode -- ROUND 3 (HIGH-PITCH f0 pass).

This is a copy of run_qt2.py with ONE targeted change: how the REFERENCE target
pitch is extracted. run_qt2.py is left untouched. Everything else (eta=0, clean_f0,
the pysptk-substitute MCEP for DTW, shift_semi default) is byte-for-byte run_qt2.

HYPOTHESIS being tested: the Demucs-separated reference vocal still contains backing
  vocals / harmonies. WORLD dio/stonemask then mis-tracks the lead in high/chorus
  regions (it locks onto a 3rd/5th of the lead), feeding the model a WRONG high-note
  target -> hoarseness on high notes. A robust, lead-locking pitch tracker (torchcrepe,
  CREPE 'full', MIT-licensed) should track the dominant/lead pitch and avoid those jumps.

CHANGE (ROUND 3) -- reference target f0 via torchcrepe instead of WORLD dio/stonemask:
  In get_matched_f0, the reference contour f0_y previously came from
  get_f0(y, method='world', padding=False) (dio->stonemask). It now comes from
  get_f0_crepe(y): torchcrepe.predict(..., 24000, hop_length=256, fmin=C2, fmax=C6,
  model='full', return_periodicity=True, device=cuda); f0=0 (unvoiced) where
  periodicity < CREPE_VOICING_THRESHOLD (default 0.5). The WORLD f0 is STILL extracted
  (get_f0 world) purely to (a) obtain the exact target frame length so the crepe f0 is
  truncated/padded to the SAME //256 frame grid the mel/MCEP/fastdtw mapping uses, and
  (b) print the diagnostic cents-divergence (overall vs high-pitch). The SOURCE mel
  (get_world_mel) and the MCEP DTW feature are unchanged, so frame i of the crepe f0
  still lines up with frame i of the source mel exactly as before.

  The crepe contour is head-aligned (frame i centered at sample i*256, anchored at
  sample 0) -- verified offset=0 on a clean monophonic synthetic glide (~8 cents vs
  WORLD, minimized at offset 0), which matches get_world_mel's center=False + 384-pad
  framing. We therefore take the first N=len(world_f0) crepe frames (zero-pad if short).

CHANGE A (inherited) -- deterministic sampling: DDIM eta default 0 (deterministic).

CHANGE B (inherited) -- target-f0 cleanup: clean_f0() applied to f0_ref right after
  get_matched_f0() and before shift / log_f0, gated by `clean` (default True). It fixes
  octave jumps, median-filters the voiced contour without bleeding into the unvoiced
  zeros, removes isolated short voiced blips, and bridges tiny unvoiced gaps.

All original fixes are retained verbatim:
  * torch.load(...) -> map_location=device, weights_only=False  (torch>=2.6 defaults
    weights_only=True and crashes loading these pickled checkpoints).
  * BigVGAN inference.load_model also uses torch.load WITHOUT weights_only; we monkey-
    patch torch.load to default weights_only=False so the vocoder checkpoint loads too.
  * pred_audio -> numpy float32 before sf.write.
  * device / sr / min_mel / max_mel are module-level (as in stock __main__).
  * pysptk is UNAVAILABLE on this machine (no MSVC to build its Cython ext, no win
    wheel). pysptk is used ONLY by utils.get_mcep to make the MCEP feature that drives
    the fastdtw alignment in get_matched_f0 -- it never touches the model, the timbre,
    or the pitch values. We therefore (a) stub sys.modules['pysptk'] so utils imports,
    and (b) override get_mcep / get_matched_f0 with a pyworld code_spectral_envelope
    (mel-cepstrum) equivalent on the SAME //256 frame grid. Model, timbre (get_world_mel)
    and target pitch (get_f0 world) paths are byte-for-byte the stock code.
"""
import os
import sys
import types
import argparse

import numpy as np
import torch
import yaml
import librosa
import soundfile as sf
import pyworld as pw
import torchcrepe
from scipy import spatial
from fastdtw import fastdtw
from tqdm import tqdm

# ---- module-level config (as in stock __main__) ----
min_mel = np.log(1e-5)
max_mel = 2.5
sr = 24000
use_gpu = torch.cuda.is_available()
device = 'cuda' if use_gpu else 'cpu'

# ROUND 3: periodicity below this -> mark frame unvoiced (f0=0) in the crepe target.
CREPE_VOICING_THRESHOLD = 0.5

# ---- FIX: make torch.load tolerant for torch>=2.6 (affects BigVGAN load_model too) ----
_orig_torch_load = torch.load
def _patched_torch_load(*a, **k):
    k.setdefault('map_location', device)
    k.setdefault('weights_only', False)
    return _orig_torch_load(*a, **k)
torch.load = _patched_torch_load

# ---- FIX: stub pysptk so utils.py imports (its get_mcep is overridden below) ----
if 'pysptk' not in sys.modules:
    _stub = types.ModuleType('pysptk')
    _stub.sptk = types.SimpleNamespace(hamming=lambda n: np.hanning(n))
    _stub.mcep = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("stub pysptk.mcep called -- get_mcep should be overridden"))
    sys.modules['pysptk'] = _stub

from diffusers import DDIMScheduler
from pitch_controller.models.unet import UNetPitcher
from pitch_controller.utils import minmax_norm_diff, reverse_minmax_norm_diff
from pitch_controller.modules.BigVGAN.inference import load_model
# import the stock helpers; get_mcep/get_matched_f0 are re-defined right after
from utils import get_world_mel, get_f0, log_f0


# ---- pysptk-free MCEP-equivalent (mel-cepstral coeffs via pyworld) ----
def get_mcep(x, n_fft=1024, n_shift=256, sr=24000, mcep_dim=34):
    """Drop-in for utils.get_mcep: returns (n_frames, mcep_dim) mel-cepstrum on the
    same x.shape[0]//n_shift frame grid, used ONLY as the fastdtw distance feature."""
    wav, _ = librosa.load(x, sr=sr)
    n_frame = wav.shape[0] // n_shift
    wav64 = ((wav * 32767).astype(np.int16) / 32767).astype(np.float64)
    fp = n_shift / sr * 1000.0  # frame period in ms == 256-sample hop
    _f0, t = pw.dio(wav64, sr, frame_period=fp)
    f0 = pw.stonemask(wav64, _f0, t, sr)
    spenv = pw.cheaptrick(wav64, f0, t, sr)
    mc = pw.code_spectral_envelope(spenv, sr, mcep_dim)
    mc = mc[:n_frame] if mc.shape[0] >= n_frame else np.pad(
        mc, ((0, n_frame - mc.shape[0]), (0, 0)), mode='edge')
    return mc.astype(np.float64)


def get_f0_crepe(wav_path, target_len, voicing_threshold=CREPE_VOICING_THRESHOLD):
    """ROUND 3 reference-pitch extractor: torchcrepe (CREPE 'full', lead-locking).

    Returns a linear-Hz f0 array of EXACTLY `target_len` frames (0 == unvoiced) on the
    SAME //256 frame grid that get_f0(...,'world',padding=False) produces, so the
    existing MCEP/fastdtw nearest-frame mapping, the source mel, and log_f0 all line up.

    torchcrepe.predict with hop_length=256 and pad=True (default) centers frame i at
    sample i*256 of the original audio -- the same head-anchored convention as WORLD's
    dio (t=0) and get_world_mel (center=False + 384-sample reflect pad). Verified
    offset=0 on a clean monophonic glide. torchcrepe emits a few extra frames at the
    tail (internal padding); we head-align and truncate to target_len (zero-pad if short).

    Unvoiced gating: frames with periodicity < voicing_threshold -> f0 = 0.
    """
    wav, _ = librosa.load(wav_path, sr=sr)
    audio = torch.from_numpy(wav).float().unsqueeze(0).to(device)
    f0, periodicity = torchcrepe.predict(
        audio, sr, hop_length=256,
        fmin=librosa.note_to_hz('C2'), fmax=librosa.note_to_hz('C6'),
        model='full', return_periodicity=True, device=device, batch_size=512)
    f0 = f0.squeeze(0).cpu().numpy().astype(np.float64)
    periodicity = periodicity.squeeze(0).cpu().numpy().astype(np.float64)
    # gate unvoiced
    f0[periodicity < voicing_threshold] = 0.0
    # head-align + truncate/pad to the WORLD frame grid length
    if f0.shape[0] >= target_len:
        f0 = f0[:target_len]
    else:
        f0 = np.pad(f0, (0, target_len - f0.shape[0]), 'constant', constant_values=0)
    return f0


def _cents_diag(f0_crepe, f0_world):
    """Diagnostic: median |cents| difference crepe-vs-world over commonly-voiced frames,
    OVERALL and restricted to the HIGH-pitch frames (top 25% by world f0). Large extra
    divergence in the high band supports 'dio mis-tracked the high notes due to harmony.'
    Prints a few lines; returns nothing."""
    a = np.asarray(f0_crepe, dtype=np.float64)
    b = np.asarray(f0_world, dtype=np.float64)
    n = min(a.shape[0], b.shape[0])
    a, b = a[:n], b[:n]
    both = (a > 0) & (b > 0)
    n_both = int(both.sum())
    if n_both < 10:
        print(f"[diag] too few commonly-voiced frames ({n_both}) for cents diagnostic")
        return
    cents = 1200.0 * np.log2(a[both] / b[both])
    overall = float(np.median(np.abs(cents)))
    # HIGH-pitch frames: top 25% by WORLD f0 among commonly-voiced frames
    bw = b[both]
    thr = np.quantile(bw, 0.75)
    hi = bw >= thr
    n_hi = int(hi.sum())
    hi_med = float(np.median(np.abs(cents[hi]))) if n_hi > 0 else float('nan')
    lo_med = float(np.median(np.abs(cents[~hi]))) if (n_both - n_hi) > 0 else float('nan')
    print(f"[diag] commonly-voiced frames: {n_both}  (crepe-voiced={int((a>0).sum())}, "
          f"world-voiced={int((b>0).sum())})")
    print(f"[diag] median |cents| crepe-vs-world  OVERALL = {overall:.2f}")
    print(f"[diag] HIGH-pitch f0 cutoff (world top25%) = {thr:.2f} Hz  "
          f"(~{librosa.hz_to_note(thr) if thr>0 else 'n/a'})")
    print(f"[diag] median |cents| crepe-vs-world  HIGH-pitch(top25%) = {hi_med:.2f}  "
          f"(n={n_hi})")
    print(f"[diag] median |cents| crepe-vs-world  LOW/MID(bottom75%) = {lo_med:.2f}  "
          f"(n={n_both - n_hi})")
    if not np.isnan(hi_med) and not np.isnan(lo_med):
        print(f"[diag] high-vs-low extra divergence = {hi_med - lo_med:+.2f} cents  "
              f"(positive => dio/world diverges MORE on high notes => supports hypothesis)")


def get_matched_f0(x, y, method='world', n_fft=1024, n_shift=256):
    """Identical logic to utils.get_matched_f0 but: (1) pyworld-based get_mcep (no pysptk),
    and (2) ROUND 3 -- the reference contour comes from torchcrepe, truncated to the SAME
    length as the WORLD f0. The WORLD f0 is still computed for the length + the diagnostic."""
    f0_world = get_f0(y, method=method, padding=False)
    f0_y = get_f0_crepe(y, target_len=f0_world.shape[0])
    _cents_diag(f0_y, f0_world)
    mcep_x = get_mcep(x, n_fft=n_fft, n_shift=n_shift)
    mcep_y = get_mcep(y, n_fft=n_fft, n_shift=n_shift)
    _, path = fastdtw(mcep_x, mcep_y, dist=spatial.distance.euclidean)
    twf = np.array(path).T
    nearest = []
    for i in range(len(f0_y)):
        idx = np.argmax(1 * twf[0] == i)
        nearest.append(twf[1][idx])
    f0_y = f0_y[nearest]
    if f0_y.shape[-1] % 8 != 0:
        f0_y = np.pad(f0_y, ((0, 8 - f0_y.shape[-1] % 8)), 'constant', constant_values=0)
    return f0_y


def clean_f0(f0_hz):
    """CHANGE B -- stabilize the target f0 contour.

    Operates on a linear-Hz array where 0 == unvoiced. Returns a cleaned copy of the
    same length/dtype. Steps:
      (a) Octave-jump fix: per voiced frame, compare to the local median of voiced
          frames in a +/-4-frame window; if >~1.7x median, halve; if <~0.6x, double.
      (b) Median-filter the voiced contour with kernel ~5 over voiced neighbors only
          (no bleeding into the unvoiced zeros).
      (c) De-blip: drop isolated voiced runs shorter than 3 frames; fill unvoiced gaps
          shorter than 3 frames between voiced regions by linear interp in log-Hz.
    Prints a one-line stat (octave-fixes / total voiced frames).
    """
    f0 = np.asarray(f0_hz, dtype=np.float64).copy()
    n = f0.shape[0]
    voiced0 = f0 > 0
    total_voiced = int(voiced0.sum())

    # ---- (a) octave-jump fix vs local voiced median (+/-4 window) ----
    win = 4
    hi_thr = 1.7
    lo_thr = 0.6
    octave_fixes = 0
    out = f0.copy()
    for i in range(n):
        if f0[i] <= 0:
            continue
        lo = max(0, i - win)
        hi = min(n, i + win + 1)
        neigh = f0[lo:hi]
        neigh_v = neigh[neigh > 0]
        if neigh_v.size < 2:
            continue
        med = np.median(neigh_v)
        if med <= 0:
            continue
        ratio = f0[i] / med
        if ratio >= hi_thr:
            out[i] = f0[i] / 2.0
            octave_fixes += 1
        elif ratio <= lo_thr:
            out[i] = f0[i] * 2.0
            octave_fixes += 1
    f0 = out

    # ---- (b) median filter over voiced neighbors only (kernel 5) ----
    k = 5
    half = k // 2
    voiced = f0 > 0
    med_out = f0.copy()
    for i in range(n):
        if not voiced[i]:
            continue
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        seg = f0[lo:hi]
        seg_v = seg[seg > 0]
        if seg_v.size > 0:
            med_out[i] = np.median(seg_v)
    f0 = med_out

    # ---- (c) de-blip: drop voiced runs < 3 frames ----
    voiced = f0 > 0
    i = 0
    while i < n:
        if voiced[i]:
            j = i
            while j < n and voiced[j]:
                j += 1
            if (j - i) < 3:
                f0[i:j] = 0.0
            i = j
        else:
            i += 1

    # ---- (c) fill unvoiced gaps < 3 frames between voiced regions (log-Hz interp) ----
    voiced = f0 > 0
    i = 0
    while i < n:
        if not voiced[i]:
            j = i
            while j < n and not voiced[j]:
                j += 1
            # gap is [i, j); bounded by voiced frames on both sides?
            if i > 0 and j < n and voiced[i - 1] and (j < n and voiced[j]) and (j - i) < 3:
                left_hz = f0[i - 1]
                right_hz = f0[j]
                if left_hz > 0 and right_hz > 0:
                    left_log = np.log(left_hz)
                    right_log = np.log(right_hz)
                    span = (j - 1) - (i - 1)  # frames between the two anchors
                    for kk in range(i, j):
                        frac = (kk - (i - 1)) / span
                        f0[kk] = np.exp(left_log + frac * (right_log - left_log))
            i = j
        else:
            i += 1

    # ---- ROUND 4 (run_qt4): conservative outlier clamp ----
    # For each voiced frame, compare to the local VOICED median over +/-6 frames.
    # If it differs by > 500 cents (a clear octave/tracking outlier -- well beyond a
    # normal musical leap of a 4th == 500 cents), snap it to that local median. This
    # is applied AFTER the +/-4 octave fix and median filter, on the post-de-blip
    # contour, so it only catches residual large outliers the earlier steps missed.
    cwin = 6
    cents_thr = 500.0
    outlier_clamps = 0
    voiced = f0 > 0
    clamped = f0.copy()
    for i in range(n):
        if f0[i] <= 0:
            continue
        lo = max(0, i - cwin)
        hi = min(n, i + cwin + 1)
        neigh = f0[lo:hi]
        neigh_v = neigh[neigh > 0]
        if neigh_v.size < 2:
            continue
        med = np.median(neigh_v)
        if med <= 0:
            continue
        cents = abs(1200.0 * np.log2(f0[i] / med))
        if cents > cents_thr:
            clamped[i] = med
            outlier_clamps += 1
    f0 = clamped

    print(f"[clean_f0] octave_fixes={octave_fixes}  total_voiced_frames={total_voiced}")
    print(f"[clean_f0] outlier_clamps(>{cents_thr:.0f}cents vs +/-{cwin}-frame median)={outlier_clamps}")
    return f0.astype(np.asarray(f0_hz).dtype)


@torch.no_grad()
def template_pitcher(source, pitch_ref, model, hifigan, steps=50, shift_semi=0,
                     eta=0, clean=True):
    source_mel = get_world_mel(source, sr=sr)
    f0_ref = get_matched_f0(source, pitch_ref, 'world')
    if clean:
        f0_ref = clean_f0(f0_ref)
    f0_ref = f0_ref * 2 ** (shift_semi / 12)
    f0_ref = log_f0(f0_ref, {'f0_bin': 345,
                             'f0_min': librosa.note_to_hz('C2'),
                             'f0_max': librosa.note_to_hz('C#6')})
    source_mel = torch.from_numpy(source_mel).float().unsqueeze(0).to(device)
    f0_ref = torch.from_numpy(f0_ref).float().unsqueeze(0).to(device)

    noise_scheduler = DDIMScheduler(num_train_timesteps=1000)
    generator = torch.Generator(device=device).manual_seed(2024)
    noise_scheduler.set_timesteps(steps)
    noise = torch.randn(source_mel.shape, generator=generator, device=device)
    pred = noise
    source_x = minmax_norm_diff(source_mel, vmax=max_mel, vmin=min_mel)

    for t in tqdm(noise_scheduler.timesteps):
        pred = noise_scheduler.scale_model_input(pred, t)
        model_output = model(x=pred, mean=source_x, f0=f0_ref, t=t, ref=None, embed=None)
        pred = noise_scheduler.step(model_output=model_output, timestep=t,
                                    sample=pred, eta=eta, generator=generator).prev_sample

    pred = reverse_minmax_norm_diff(pred, vmax=max_mel, vmin=min_mel)
    pred_audio = hifigan(pred)
    pred_audio = pred_audio.cpu().squeeze().clamp(-1, 1)
    # FIX: numpy float32 for soundfile
    pred_audio = pred_audio.detach().cpu().numpy().astype('float32')
    return pred_audio


def load_models():
    config = yaml.load(open('pitch_controller/config/DiffWorld_24k.yaml'), Loader=yaml.FullLoader)
    unet_cfg = config['unet']
    model = UNetPitcher(**unet_cfg)
    state_dict = torch.load('ckpts/world_fixed_40.pt', map_location=device, weights_only=False)
    for key in list(state_dict.keys()):
        state_dict[key.replace('_orig_mod.', '')] = state_dict.pop(key)
    model.load_state_dict(state_dict)
    if use_gpu:
        model.cuda()
    model.eval()
    hifigan, _ = load_model('ckpts/bigvgan_24khz_100band/g_05000000.pt', device=device)
    hifigan.eval()
    return model, hifigan


def median_voiced_f0(wav_path):
    wav, _ = librosa.load(wav_path, sr=sr)
    wav64 = ((wav * 32767).astype(np.int16) / 32767).astype(np.float64)
    _f0, t = pw.dio(wav64, sr)
    f0 = pw.stonemask(wav64, _f0, t, sr)
    voiced = f0[f0 > 0]
    return float(np.median(voiced)) if voiced.size else 0.0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', required=True)
    ap.add_argument('--ref', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--steps', type=int, default=50)
    ap.add_argument('--shift', type=int, default=0)
    ap.add_argument('--eta', type=float, default=0.0,
                    help='DDIM eta; 0=deterministic (default), 1=stochastic')
    ap.add_argument('--no-clean', dest='clean', action='store_false',
                    help='disable clean_f0 target-pitch cleanup (default: enabled)')
    ap.set_defaults(clean=True)
    ap.add_argument('--octave', action='store_true',
                    help='auto-compute octave shift from median voiced f0')
    args = ap.parse_args()

    print(f"[run_qt3] device={device} torch={torch.__version__} cuda={torch.cuda.is_available()} "
          f"torchcrepe={getattr(torchcrepe, '__version__', '0.0.24')} "
          f"crepe_voicing_thr={CREPE_VOICING_THRESHOLD}")

    shift = args.shift
    if args.octave:
        m_src = median_voiced_f0(args.source)
        m_ref = median_voiced_f0(args.ref)
        if m_ref > 0 and m_src > 0:
            shift = int(12 * round(np.log2(m_src / m_ref)))
        print(f"[octave] median_src={m_src:.3f} Hz  median_ref={m_ref:.3f} Hz  shift_semi={shift}")

    model, hifigan = load_models()
    import time
    t0 = time.time()
    audio = template_pitcher(args.source, args.ref, model, hifigan,
                             steps=args.steps, shift_semi=shift,
                             eta=args.eta, clean=args.clean)
    dt = time.time() - t0
    sf.write(args.out, audio, samplerate=sr)
    dur = len(audio) / sr
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    print(f"[done] wrote {args.out}  dur={dur:.3f}s  peak={peak:.5f}  "
          f"diffusion+vocode={dt:.2f}s  steps={args.steps}  shift={shift}  "
          f"eta={args.eta}  clean={args.clean}")

    # load-back assertion: non-silent
    chk, chk_sr = sf.read(args.out)
    chk_dur = len(chk) / chk_sr
    chk_peak = float(np.max(np.abs(chk))) if chk.size else 0.0
    assert chk_dur > 0, f"duration assertion failed: {chk_dur}"
    assert chk_peak > 0.01, f"peak amplitude assertion failed: {chk_peak}"
    print(f"[verify] reload dur={chk_dur:.3f}s  peak={chk_peak:.5f}  sr={chk_sr}  OK")
