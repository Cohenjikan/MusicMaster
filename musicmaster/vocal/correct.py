"""修音准(Stage 1):跑调清唱 → 在调,仍是本人音色。

子进程调用验证过的 DiffPitcher 配方(template 模式):torchcrepe 取目标 f0(锁主旋律抗和声)
→ DDIM(eta=0)扩散修音高 → BigVGAN 声码器 → 24kHz。**不改 run_qt4 配方**。

长度处理:经 `diffpitcher_scripts/run_full.py` 分块(30s 窗 + 交叉淡入)→ **任意长度都出完整输出**;
≤30s 即单窗,等价单次 run_qt4。(8GB 显存:逐窗 30s,不会 OOM;整首约每 30s/步数 线性耗时。)

血泪红线(交接文档 §5):② ref 必须【去和声】(否则高音哑);shift 默认 -12(八度锁)。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Union

from . import config

# 直跑 `python -m musicmaster.vocal.correct` 时:GBK/英文代码页控制台打印中文会崩,统一切 UTF-8(防御式)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PathLike = Union[str, Path]

# 分块全曲渲染器(我方脚本;import run_qt4 + 读 ckpts 需 cwd/PYTHONPATH = DiffPitcher 目录)
_RUN_FULL = Path(__file__).resolve().parent / "diffpitcher_scripts" / "run_full.py"


def correct(
    source: PathLike,
    ref: PathLike,
    out: PathLike,
    *,
    steps: int = 150,
    shift: int = -12,
    eta: float = 0.0,
    clean: bool = True,
    window: float = 30.0,
    overlap: float = 5.0,
    timeout: float = 7200.0,
) -> Path:
    """把 `source`(用户原始清唱)的音高修向 `ref`(去和声干净参考)的旋律,写到 `out`(24kHz)。

    任意长度:内部分块(30s 窗 + 5s 交叉淡入)→ 完整长度输出,不截断。
    source/ref 任意格式/采样率(内部统一 24k 单声道并裁到等长;两者须时间对齐)。

    Args:
        steps:   扩散步数;质量档 150(快速可 50)。**注意:整首 × 150 步在 8GB 上可能 20+ 分钟。**
        shift:   半音移调;默认 -12。
        window/overlap: 分块窗长/重叠(秒)。
        timeout: 子进程超时(秒,默认 2 小时,容纳整首高步数)。
    """
    problems = config.check_paths("correct")
    if problems:
        raise RuntimeError("修音(correct)环境未就绪:\n  - " + "\n  - ".join(problems))

    dp = config.diffpitcher_dir()
    py = config.vocal_python()
    out_path = Path(out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(py), str(_RUN_FULL),
        "--source", str(Path(source).resolve()),
        "--ref", str(Path(ref).resolve()),
        "--out", str(out_path),
        "--steps", str(int(steps)),
        "--shift", str(int(shift)),
        "--eta", str(float(eta)),
        "--window", str(float(window)),
        "--overlap", str(float(overlap)),
    ]
    if not clean:
        cmd.append("--no-clean")

    # run_full.py import run_qt4 + 读 pitch_controller/ckpts → cwd & PYTHONPATH 都指向 DiffPitcher。
    env = config.subprocess_env()
    env["PYTHONPATH"] = str(dp) + os.pathsep + env.get("PYTHONPATH", "")
    subprocess.run(cmd, cwd=str(dp), env=env, check=True, timeout=timeout)
    return out_path


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="修音准(分块全曲;包装验证配方)")
    ap.add_argument("--source", required=True, help="用户原始清唱")
    ap.add_argument("--ref", required=True, help="去和声干净参考(取目标旋律)")
    ap.add_argument("--out", required=True, help="输出修音 wav(24kHz)")
    ap.add_argument("--steps", type=int, default=150)
    ap.add_argument("--shift", type=int, default=-12)
    ap.add_argument("--no-clean", dest="clean", action="store_false")
    a = ap.parse_args()
    p = correct(a.source, a.ref, a.out, steps=a.steps, shift=a.shift, clean=a.clean)
    print(f"[correct] 修音完成 → {p}")
