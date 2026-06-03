"""两段式人声管线编排(= 验证过的 SVC_v2 配方)。

  RAW(用户原始清唱) + REF(去和声干净参考)
      └─[Stage 1 correct: DiffPitcher]→ 修音(在调,24kHz)
            └─[resample 44.1k]→ 喂 Seed-VC
                  └─[Stage 2 voice: Seed-VC, target=SELF]→ 在调 + 干净 + 仍是你(44.1kHz)= SVC_v2

本编排函数运行在主(CPU)环境:它只负责串接两个 GPU 子进程(correct / voice)+ 中间一步轻量重采样。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Union

from .correct import correct
from .voice import resample, voice

# 直跑 `python -m musicmaster.vocal.pipeline` 时:GBK/英文代码页控制台打印中文会崩,统一切 UTF-8(防御式)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PathLike = Union[str, Path]


def two_stage(
    raw: PathLike,
    ref: PathLike,
    self_ref: PathLike,
    out_dir: PathLike,
    *,
    correct_steps: int = 150,
    shift: int = -12,
    voice_steps: int = 50,
    voice_cfg: float = 0.7,
) -> dict:
    """完整两段式:修音准 → 换音色=本人。

    Args:
        raw:       用户原始清唱 wav。
        ref:       目标旋律的【去和声】干净主唱参考 wav。
        self_ref:  用户自己的一段干净清唱(目标音色=身份锚)。
        out_dir:   输出目录(产出 corrected_24k.wav / corrected_44k.wav / 最终 SVC wav)。
        correct_steps / shift / voice_steps: 各阶段参数(默认 = 验证配方)。
        voice_cfg: Seed-VC 的 inference_cfg_rate(引导强度,0~1;越高越强调目标音色条件,
                   效果较细微;0 最快≈1.5×。默认 0.7 = 验证配方)。

    Returns:
        {"corrected_24k": Path, "corrected_44k": Path, "final": Path|None}
    """
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Stage 1:修音准(GPU 子进程 → .venv-neural)
    corrected_24k = out_dir / "corrected_24k.wav"
    correct(raw, ref, corrected_24k, steps=correct_steps, shift=shift)

    # 中间:24k → 44.1k(Seed-VC f0 模型要 44k)
    corrected_44k = resample(corrected_24k, out_dir / "corrected_44k.wav")

    # Stage 2:换音色=本人(GPU 子进程 → .venv-svc)
    final = voice(corrected_44k, self_ref, out_dir,
                  diffusion_steps=voice_steps, inference_cfg_rate=voice_cfg)

    return {"corrected_24k": corrected_24k, "corrected_44k": corrected_44k, "final": final}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="两段式人声管线(修音准 → 换音色=本人 = SVC_v2 配方)")
    ap.add_argument("--raw", required=True, help="用户原始清唱 wav")
    ap.add_argument("--ref", required=True, help="去和声干净参考 wav")
    ap.add_argument("--self", dest="self_ref", required=True, help="用户自己清唱(目标音色=身份锚)")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--correct-steps", type=int, default=150)
    ap.add_argument("--shift", type=int, default=-12)
    ap.add_argument("--voice-steps", type=int, default=50)
    ap.add_argument("--voice-cfg", type=float, default=0.7,
                    help="Seed-VC 引导强度 inference_cfg_rate(0~1;越高越贴目标音色,效果细微;0 最快)")
    a = ap.parse_args()
    r = two_stage(a.raw, a.ref, a.self_ref, a.out,
                  correct_steps=a.correct_steps, shift=a.shift,
                  voice_steps=a.voice_steps, voice_cfg=a.voice_cfg)
    print("[two_stage] 产物:")
    for k, v in r.items():
        print(f"  {k:14s} {v}")
