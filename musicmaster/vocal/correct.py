"""修音准(Stage 1):跑调清唱 → 在调,仍是本人音色。

子进程调用验证过的 DiffPitcher `run_qt4.py`(template 模式):
  torchcrepe 取目标 f0(锁主旋律抗和声)→ DDIM(eta=0 确定性)扩散修音高 → BigVGAN 声码器 → 24kHz。
**不改 run_qt4 内部逻辑**(守「唯一验证配方,勿重建」红线)。

血泪红线(来自交接文档 §5):
  • ref 必须是【去和声】的干净主唱参考,否则高音发哑;
  • shift 默认 -12(八度锁;别用 run_qt4 的 --octave 自动档,dio 中位基频会被八度折半误判)。
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Union

from . import config

PathLike = Union[str, Path]


SR = 24000
MAX_SECONDS_DEFAULT = 30.0


def _prep_pair(source, ref, max_seconds):
    """加载 source+ref → 单声道 24k → 裁到等长(修 source/ref 时长不一致导致的 UNet 尺寸崩溃)
    + 限时长(避 8GB 显存 OOM)。返回 (src_wav, ref_wav, 实际秒数, 是否被截断)。
    在主(CPU)环境用 librosa/soundfile 完成,顺带把 m4a/mp3 等统一成 wav。"""
    import tempfile

    import librosa
    import soundfile as sf

    ys, _ = librosa.load(str(source), sr=SR, mono=True)
    yr, _ = librosa.load(str(ref), sr=SR, mono=True)
    n = min(len(ys), len(yr))
    capped = False
    if max_seconds and n > int(max_seconds * SR):
        n = int(max_seconds * SR)
        capped = True
    ys, yr = ys[:n], yr[:n]
    d = Path(tempfile.mkdtemp(prefix="mm_correct_"))
    sp, rp = d / "src_24k.wav", d / "ref_24k.wav"
    sf.write(str(sp), ys, SR)
    sf.write(str(rp), yr, SR)
    return sp, rp, n / SR, capped


def correct(
    source: PathLike,
    ref: PathLike,
    out: PathLike,
    *,
    steps: int = 150,
    shift: int = -12,
    eta: float = 0.0,
    clean: bool = True,
    max_seconds: float = MAX_SECONDS_DEFAULT,
    timeout: float = 3600.0,
) -> Path:
    """把 `source`(用户原始清唱)的音高修向 `ref`(去和声干净参考)的旋律,写到 `out`(24kHz)。

    Args:
        source:  用户原始清唱 wav(任意采样率,内部重采样到 24k)。
        ref:     目标旋律的【去和声】干净主唱参考 wav(取目标 f0)。
        out:     输出修音 wav(24kHz)。
        steps:   扩散步数;质量档 150(快速迭代可 50)。
        shift:   半音移调;默认 -12(把较高的参考降一个八度到用户音域)。
        eta:     DDIM eta;0=确定性(默认,去发抖),1=随机。
        clean:   是否对目标 f0 做清洗(去八度跳/中值滤波/去碎段);默认 True。
        timeout: 子进程超时(秒)。

    Returns:
        写出的修音 wav 绝对路径。

    Raises:
        RuntimeError: DiffPitcher / 修音 venv 路径缺失(给出 setup 提示)。
        subprocess.CalledProcessError: run_qt4 执行失败。
    """
    problems = config.check_paths("correct")
    if problems:
        raise RuntimeError("修音(correct)环境未就绪:\n  - " + "\n  - ".join(problems))

    dp = config.diffpitcher_dir()
    py = config.vocal_python()
    out_path = Path(out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 鲁棒前处理:统一 24k 单声道 + 裁到等长(修「source/ref 时长不一致」的 UNet 尺寸崩溃)
    # + 限时长(8GB 显存单次上限;整首请用片段,或走分块 run_full)。
    src_wav, ref_wav, dur, capped = _prep_pair(source, ref, max_seconds)
    if capped:
        print(f"[correct] 输入较长 → 仅处理前 {dur:.0f}s(8GB 显存单次上限;整首请分段或用 run_full 分块)。")

    cmd = [
        str(py), "run_qt4.py",
        "--source", str(src_wav),
        "--ref", str(ref_wav),
        "--out", str(out_path),
        "--steps", str(int(steps)),
        "--shift", str(int(shift)),
        "--eta", str(float(eta)),
    ]
    if not clean:
        cmd.append("--no-clean")

    # cwd=DiffPitcher:run_qt4 用相对路径读 pitch_controller/config 与 ckpts/。
    subprocess.run(cmd, cwd=str(dp), env=config.subprocess_env(), check=True, timeout=timeout)
    return out_path


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="修音准(DiffPitcher run_qt4 子进程包装)")
    ap.add_argument("--source", required=True, help="用户原始清唱 wav")
    ap.add_argument("--ref", required=True, help="去和声干净参考 wav(取目标旋律)")
    ap.add_argument("--out", required=True, help="输出修音 wav(24kHz)")
    ap.add_argument("--steps", type=int, default=150)
    ap.add_argument("--shift", type=int, default=-12)
    ap.add_argument("--eta", type=float, default=0.0)
    ap.add_argument("--no-clean", dest="clean", action="store_false")
    a = ap.parse_args()
    p = correct(a.source, a.ref, a.out, steps=a.steps, shift=a.shift, eta=a.eta, clean=a.clean)
    print(f"[correct] 修音完成 → {p}")
