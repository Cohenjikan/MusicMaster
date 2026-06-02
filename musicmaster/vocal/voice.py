"""换音色=本人(Stage 2):修音输出 → 在调 + 干净 + 仍是你。

子进程调用 Seed-VC `inference.py`(零样本歌声转换):
  以【用户自己的一段干净清唱】为目标音色,跟随 source(已修音、在调)的 f0 重新合成 → 44.1kHz。
概念:修正后的 f0(在调)+ 用户自己的音色(身份锚)= 在调 + 干净 + 还是你(= 成果 SVC_v2)。

血泪红线(来自交接文档 §5):
  • source 已在用户八度 → auto_f0_adjust=False、semi_tone_shift=0(别再移,移了身份会偏);
  • Seed-VC 的重生成天然洗掉声码器电流声 —— 绝不在 source 上加 noise_gate(那会挖出静音洞=「会断」)。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional, Union

from . import config

# 直跑 `python -m musicmaster.vocal.voice` 时:GBK/英文代码页控制台打印中文会崩,统一切 UTF-8(防御式)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PathLike = Union[str, Path]

# Seed-VC 的 f0 歌唱模型工作在 44.1kHz;Stage 1(DiffPitcher)输出 24kHz,喂入前需重采样。
SEEDVC_SR = 44100


def resample(in_wav: PathLike, out_wav: PathLike, sr: int = SEEDVC_SR) -> Path:
    """把音频重采样到 `sr`(默认 44.1kHz)。在主(CPU)环境用 soundfile + librosa 完成。"""
    import librosa
    import soundfile as sf

    y, _ = librosa.load(str(in_wav), sr=sr, mono=True)
    out_wav = Path(out_wav).resolve()
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_wav), y, sr)
    return out_wav


def voice(
    source: PathLike,
    target: PathLike,
    out_dir: PathLike,
    *,
    diffusion_steps: int = 50,
    length_adjust: float = 1.0,
    inference_cfg_rate: float = 0.7,
    f0_condition: bool = True,
    auto_f0_adjust: bool = False,
    semi_tone_shift: int = 0,
    fp16: bool = True,
    timeout: float = 3600.0,
) -> Optional[Path]:
    """把 `source`(已修音,44.1kHz)的音色换成 `target`(用户自己清唱=身份锚)的音色。

    Args:
        source:  已修音、在调的 wav(应为 44.1kHz;若不是请先 resample())。
        target:  用户自己的一段干净清唱(~10–30s),作零样本目标音色。
        out_dir: 输出目录(Seed-VC 自动命名 vc_<source>_<target>_<len>_<steps>_<cfg>.wav)。
        f0_condition:    歌唱模式,跟随 source 的 f0(默认 True)。
        auto_f0_adjust:  默认 False(source 已在用户八度,不要再移)。
        semi_tone_shift: 默认 0(同上)。

    Returns:
        产物 wav 绝对路径(取 out_dir 下最新的 vc_*.wav);找不到则 None。
    """
    problems = config.check_paths("voice")
    if problems:
        raise RuntimeError("换音色(voice)环境未就绪:\n  - " + "\n  - ".join(problems))

    sv = config.seedvc_dir()
    py = config.svc_python()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # 音色锚只需一段样本(过长无益且更慢)→ 若 >30s 截取前 30s。
    import librosa as _lb
    import soundfile as _sf

    _ty, _tsr = _lb.load(str(target), sr=None, mono=True)
    if len(_ty) > 30 * _tsr:
        target = out_dir / "_target_cap.wav"
        _sf.write(str(target), _ty[: 30 * _tsr], _tsr)

    cmd = [
        str(py), "inference.py",
        "--source", str(Path(source).resolve()),
        "--target", str(Path(target).resolve()),
        "--output", str(out_dir),
        "--diffusion-steps", str(int(diffusion_steps)),
        "--length-adjust", str(float(length_adjust)),
        "--inference-cfg-rate", str(float(inference_cfg_rate)),
        "--f0-condition", str(bool(f0_condition)),
        "--auto-f0-adjust", str(bool(auto_f0_adjust)),
        "--semi-tone-shift", str(int(semi_tone_shift)),
        "--fp16", str(bool(fp16)),
    ]
    # cwd=seed-vc:inference.py 顶部把 HF_HUB_CACHE 设为 ./checkpoints/hf_cache(相对路径)。
    subprocess.run(cmd, cwd=str(sv), env=config.subprocess_env(), check=True, timeout=timeout)

    produced = sorted(out_dir.glob("vc_*.wav"), key=lambda p: p.stat().st_mtime)
    return produced[-1] if produced else None


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="换音色=本人(Seed-VC inference 子进程包装)")
    ap.add_argument("--source", required=True, help="已修音的 wav(应 44.1kHz)")
    ap.add_argument("--target", required=True, help="用户自己清唱(目标音色=身份锚)")
    ap.add_argument("--out-dir", required=True, help="输出目录")
    ap.add_argument("--steps", type=int, default=50)
    ap.add_argument("--resample", action="store_true", help="先把 source 重采样到 44.1kHz 再换音色")
    a = ap.parse_args()
    src = a.source
    if a.resample:
        src = resample(a.source, Path(a.out_dir) / "_src_44k.wav")
    p = voice(src, a.target, a.out_dir, diffusion_steps=a.steps)
    print(f"[voice] 换音色完成 → {p}")
