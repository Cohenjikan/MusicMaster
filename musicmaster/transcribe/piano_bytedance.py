#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase2 钢琴复音核心:ByteDance 高分辨率钢琴转录 → target.json(M4b 契约)。
替代 basic-pitch 的乐器/钢琴路线(权重 MAESTRO=NC,2026-06-02 解禁可用)。
- 绕过旧包 `load_audio` 的过时 librosa.resample 调用(新版 librosa 改 keyword-only)。
- 有 CUDA torch 自动用 GPU(RTX4060),否则 CPU(也能跑,12s 片段秒级)。
- 输出含 onset/offset/velocity(比 basic-pitch 丰富);本封装先取 notes 进契约。

API: transcribe(path, out_mid=None) -> {"tempo":..., "notes":[{midi,start_s,end_s,velocity}]}
CLI: PYTHONUTF8=1 PY piano_bytedance.py IN.wav [--out-mid x.mid]
"""
from __future__ import annotations
import argparse, json, sys, tempfile
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
import librosa

_TR = None  # 复用转录器(避免重复加载权重)


def _get_transcriptor():
    global _TR
    if _TR is None:
        try:
            import torch
        except Exception as e:
            raise RuntimeError(
                "ByteDance 引擎需要 PyTorch,但当前环境未装 torch。请改用 --engine crepe,"
                "或在本 venv 安装 torch 后重试。"
            ) from e
        from piano_transcription_inference import PianoTranscription
        device = "cuda" if torch.cuda.is_available() else "cpu"
        try:
            _TR = PianoTranscription(device=device)
        except Exception as e:
            raise RuntimeError(
                "ByteDance 钢琴模型加载失败(通常是权重未就绪)。首次使用会下载 ~165MB 权重到 "
                "~/piano_transcription_inference_data/;Windows 无 wget 时可能下载失败。"
                "请确保联网/wget 可用,或手动下载该 .pth 到上述目录后重试;也可改用 --engine crepe。"
                f"(原始错误:{type(e).__name__}: {e})"
            ) from e
        _TR._device_str = device
    return _TR


def transcribe(path, out_mid=None):
    from piano_transcription_inference import sample_rate
    audio, _ = librosa.load(str(path), sr=sample_rate, mono=True)
    tr = _get_transcriptor()
    if out_mid is None:
        out_mid = str(Path(tempfile.gettempdir()) / "bytedance_tmp.mid")
    res = tr.transcribe(audio, str(out_mid))
    notes = [{"midi": int(e["midi_note"]), "start_s": round(float(e["onset_time"]), 4),
              "end_s": round(float(e["offset_time"]), 4), "velocity": int(e.get("velocity", 0))}
             for e in res["est_note_events"]]
    notes.sort(key=lambda x: (x["start_s"], x["midi"]))
    return {"tempo": 120.0, "notes": notes, "_engine": "bytedance", "_device": getattr(tr, "_device_str", "cpu")}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--out", default=None, help="输出目录(写 notes.json/out.mid)")
    ap.add_argument("--out-mid", default=None)
    a = ap.parse_args(argv)
    out_mid = a.out_mid or (str(Path(a.out) / "out.mid") if a.out else None)
    if a.out:
        Path(a.out).mkdir(parents=True, exist_ok=True)
    target = transcribe(a.input, out_mid=out_mid)
    if a.out:
        (Path(a.out) / "notes.json").write_text(json.dumps(target, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"ByteDance({target['_device']}): notes={len(target['notes'])}  out_mid={out_mid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
