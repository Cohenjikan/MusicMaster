#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase1·L1 入口质量门:扒谱【之前】先体检录音,烂输入在门口拦下并给修法。
核心:不只信容器元数据(采样率/码率),而是从【频谱测真实有效带宽】——
      因为低质源升采样后容器会假装 44.1k,但真实内容仍只到 ~3.8kHz(本项目钢琴案例)。
对外 API:assess(path) -> dict(含 tier/gate/issues/metrics)。CLI 打印人话报告 + JSON。
用法: PYTHONUTF8=1 PY quality_gate.py IN.wav [--json]
"""
from __future__ import annotations
import argparse, json, shutil, subprocess, sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
import numpy as np
import librosa

FFPROBE = shutil.which("ffprobe") or "ffprobe"


def ffprobe_meta(path: str) -> dict:
    """容器/编码元数据(可能与真实内容不符,仅作参考)。"""
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries",
             "format=duration,bit_rate,format_name:stream=codec_name,sample_rate,channels,bits_per_sample",
             "-of", "json", path],
            capture_output=True, text=True, timeout=30)
        j = json.loads(out.stdout or "{}")
    except Exception as e:
        return {"error": repr(e)[:120]}
    st = (j.get("streams") or [{}])[0]
    fm = j.get("format") or {}
    def _i(x):
        try: return int(x)
        except Exception: return None
    def _f(x):
        try: return float(x)
        except Exception: return None
    return {"codec": st.get("codec_name"), "declared_sr": _i(st.get("sample_rate")),
            "channels": _i(st.get("channels")), "bits": _i(st.get("bits_per_sample")),
            "bit_rate": _i(fm.get("bit_rate")), "duration": _f(fm.get("duration")),
            "container": fm.get("format_name")}


def effective_bandwidth(y: np.ndarray, sr: int):
    """真实有效带宽 = 活跃帧平均频谱里、能量跌到峰值 -40dB 前的最高频率。
    返回 (rolloff_hz, peak_db_ref)。这是抓"假高采样率"的关键指标。"""
    n_fft = 4096
    S = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=1024)) + 1e-10
    fe = S.sum(axis=0)
    thr = np.median(fe)
    active = S[:, fe >= thr]
    spec = active.mean(axis=1) if active.shape[1] else S.mean(axis=1)
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    db = 20.0 * np.log10(spec / spec.max())
    # 忽略极低频直流/嗡声峰值的影响:以 100Hz 以上的峰为基准
    valid = freqs >= 100
    peak_idx = np.argmax(db[valid])
    above = np.where(db >= -40.0)[0]
    roll = float(freqs[above[-1]]) if above.size else float(freqs[-1])
    return roll, float(db[valid][peak_idx])


def clipping_frac(y: np.ndarray) -> float:
    return float(np.mean(np.abs(y) >= 0.999))


def dynamic_range_db(y: np.ndarray, sr: int) -> float:
    """活跃 vs 安静帧的能量差,粗估动态范围/底噪。越大越干净。"""
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=512)) + 1e-10
    fe = 20.0 * np.log10(S.sum(axis=0))
    return float(np.percentile(fe, 95) - np.percentile(fe, 5))


def assess(path: str) -> dict:
    meta = ffprobe_meta(path)
    try:
        y, sr = librosa.load(path, sr=None, mono=True)
    except Exception as e:
        return {"tier": "太差", "gate": "block", "score": 0.0,
                "issues": [["严重", f"读取失败/非音频文件({type(e).__name__})", "确认是有效音频文件(wav/mp3/...)后重试"]],
                "metrics": {}, "meta": meta}
    if y.size == 0:
        return {"tier": "太差", "gate": "block", "score": 0.0,
                "issues": [["严重", "空音频/读取失败", "换文件"]],
                "metrics": {}, "meta": meta}
    roll, _peak = effective_bandwidth(y, sr)
    eff_sr = 2.0 * roll
    clip = clipping_frac(y)
    dr = dynamic_range_db(y, sr)

    issues = []  # (级别, 描述, 修法)
    # —— 主指标:真实有效带宽(扒谱需顶音~2-3 次谐波清晰,实践地板 roll≈5kHz)——
    if roll < 5000:
        issues.append(["严重",
            f"真实有效带宽仅 ~{roll/1000:.1f}kHz(真实采样≈{eff_sr/1000:.1f}kHz),"
            f"4kHz 以上信息已丢失,音高跟踪在连贯/快速段会漂移失锁",
            "用 ≥44.1kHz 无损 WAV,或手机录音 App 选「无损/高质量(≥128kbps)」重录"])
    elif roll < 7000:
        issues.append(["明显",
            f"有效带宽偏低 ~{roll/1000:.1f}kHz,高音区与快速段精度会下降",
            "尽量用 ≥44.1kHz / ≥128kbps 重录"])
    elif roll < 11000:
        issues.append(["轻度",
            f"有效带宽 ~{roll/1000:.1f}kHz(有损压缩痕迹),扒谱多数可用",
            "可接受;追求最佳则用无损"])

    # —— 容器元数据(次要,与真实带宽冲突时以带宽为准)——
    dsr = meta.get("declared_sr")
    if dsr and dsr < 32000:
        issues.append(["明显", f"声明采样率仅 {dsr}Hz", "用 ≥44.1kHz 重录"])
    if dsr and eff_sr < 0.6 * dsr:
        issues.append(["提示", f"容器声明 {dsr}Hz 但真实内容只到 ~{eff_sr/1000:.1f}kHz(疑似低质源升采样,别被文件名骗)", "以真实带宽为准"])
    br = meta.get("bit_rate")
    if br and meta.get("codec") not in ("pcm_s16le", "pcm_s24le", "flac", "alac") and br < 96000:
        issues.append(["明显", f"有损码率仅 {br/1000:.0f}kbps", "用 ≥128kbps 或无损重录"])

    # —— 削波 / 动态范围 ——
    if clip > 0.01:
        issues.append(["明显", f"削波样本占 {clip*100:.1f}%(录音过载失真)", "降低录音电平,峰值留 -6dB 余量重录"])
    elif clip > 0.001:
        issues.append(["轻度", f"轻微削波 {clip*100:.2f}%", "略降录音电平"])
    if dr < 20:
        issues.append(["轻度", f"动态范围偏小 ~{dr:.0f}dB(底噪偏高/压限过重)", "更安静环境重录"])

    levels = [it[0] for it in issues]
    if "严重" in levels:
        tier, gate = "太差", "block"
    elif "明显" in levels:
        tier, gate = "差", "warn"
    elif "轻度" in levels or "提示" in levels:
        tier, gate = "可用", "warn"
    else:
        tier, gate = "好", "pass"

    # 0-100 体检分(带宽主导)
    score = 100.0
    score -= max(0.0, (11000 - min(roll, 11000)) / 11000) * 60.0  # 带宽最多扣 60
    score -= min(clip * 100 * 5, 20.0)
    score -= max(0.0, (20 - min(dr, 20))) * 1.0
    # 分数与档位一致:别让"太差"显示 60 多分,削弱信任
    score = min(score, {"太差": 40.0, "差": 65.0, "可用": 85.0, "好": 100.0}[tier])
    score = round(max(0.0, min(100.0, score)), 1)

    return {"tier": tier, "gate": gate, "score": score, "issues": issues,
            "metrics": {"effective_bandwidth_hz": round(roll, 1),
                        "effective_sr_hz": round(eff_sr, 1),
                        "loaded_sr": sr, "clipping_pct": round(clip * 100, 3),
                        "dynamic_range_db": round(dr, 1), "duration_s": round(len(y) / sr, 2)},
            "meta": meta}


def human_report(r: dict) -> str:
    L = []
    badge = {"好": "✅ 好", "可用": "🟢 可用", "差": "⚠️ 差", "太差": "⛔ 太差"}.get(r["tier"], r["tier"])
    m = r.get("metrics", {})
    L.append(f"录音体检:{badge}  (体检分 {r.get('score','?')}/100,门控={r['gate']})")
    L.append(f"  真实有效带宽 ~{m.get('effective_bandwidth_hz','?')}Hz(≈真实采样 {m.get('effective_sr_hz','?')}Hz)"
             f" | 削波 {m.get('clipping_pct','?')}% | 动态范围 {m.get('dynamic_range_db','?')}dB")
    meta = r.get("meta", {})
    L.append(f"  容器声明:{meta.get('codec')} {meta.get('declared_sr')}Hz "
             f"{(str(meta.get('bit_rate')//1000)+'kbps') if meta.get('bit_rate') else ''} {meta.get('channels')}ch")
    if r["issues"]:
        L.append("  问题与修法:")
        for lvl, desc, fix in r["issues"]:
            L.append(f"    [{lvl}] {desc}\n           ↳ 修:{fix}")
    else:
        L.append("  无明显问题,放心扒。")
    if r["gate"] == "block":
        L.append("  → 建议:质量过低,扒谱结果会大段不可靠;先按上面修法重录再扒(仍可强行扒,但结果仅供参考)。")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--json", action="store_true", help="同时输出机器可读 JSON")
    a = ap.parse_args(argv)
    r = assess(a.input)
    print(human_report(r))
    if a.json:
        print("\nJSON " + json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
