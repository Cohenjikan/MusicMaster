#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""建分离环境(.venv-sep,GPU):audio-separator + CUDA torch。

    python scripts/setup_sep.py [--venv vendor/.venv-sep] [--cuda cu124]

模型权重(BS-RoFormer / Karaoke RoFormer / UVR)在首次运行时由 audio-separator 自动下载
(默认 C:\\tmp\\audio-separator-models;可用环境变量 MUSICMASTER_SEP_MODELS 改)。
"""
from __future__ import annotations
import argparse, os, subprocess, sys, venv
from pathlib import Path

# 系统 Python + GBK 控制台:输出/重定向遇非 GBK 字符会 UnicodeEncodeError,统一切 UTF-8(防御式)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
# 国内加速:Windows 上 PyPI 的 torch 轮子是 CPU 版(无 nvidia-* 依赖),
# 必须走 PyTorch CUDA 索引;腾讯云镜像了整套 /whl/cu124/(实测 ~20 MB/s),别用官方站。
TORCH_VERSION = os.environ.get("MUSICMASTER_TORCH_VERSION", "2.6.0")
# audio-separator 依赖 onnx2torch -> torchvision;不一起装的话 pip 会回溯到 CPU 新版 torch。
TORCHVISION_VERSION = os.environ.get("MUSICMASTER_TORCHVISION_VERSION", "0.21.0")
PYPI_MIRROR = os.environ.get("MUSICMASTER_PYPI_MIRROR", "https://mirrors.cloud.tencent.com/pypi/simple/")
PYTORCH_MIRROR = os.environ.get(
    "MUSICMASTER_PYTORCH_MIRROR", "https://mirrors.cloud.tencent.com/pytorch-wheels/whl/{cuda}/")


def install_torch(run, cuda: str) -> None:
    """Windows 走腾讯云 PyTorch CUDA 镜像;其他平台回退 PyTorch 官方索引。"""
    if os.name == "nt":
        idx = PYTORCH_MIRROR.format(cuda=cuda)
        print(f"      PyTorch CUDA 镜像: {idx} (torch=={TORCH_VERSION}, torchvision=={TORCHVISION_VERSION})")
        run("install", f"torch=={TORCH_VERSION}", f"torchaudio=={TORCH_VERSION}",
            f"torchvision=={TORCHVISION_VERSION}",
            "--index-url", idx, "--timeout", "60", "--retries", "5")
    else:
        run("install", "torch", "torchaudio", "--index-url", f"https://download.pytorch.org/whl/{cuda}")


def venv_python(d: Path) -> Path:
    return d / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--venv", default=str(ROOT / "vendor" / ".venv-sep"))
    ap.add_argument("--cuda", default="cu124", help="PyTorch CUDA 轮子标签,如 cu124 / cu121")
    a = ap.parse_args()
    vdir = Path(a.venv)
    vdir.parent.mkdir(parents=True, exist_ok=True)

    if venv_python(vdir).exists():
        print(f"[1/4] 复用已有虚拟环境 {vdir}")
    else:
        print(f"[1/4] 建 {vdir}")
        venv.create(vdir, with_pip=True)
    py = str(venv_python(vdir))

    def run(*pip_args):
        subprocess.check_call([py, "-m", "pip", *pip_args])

    print("[2/4] 升级 pip")
    run("install", "-U", "pip")
    print(f"[3/4] 装 CUDA torch({a.cuda})")
    install_torch(run, a.cuda)
    print("[4/4] 装 audio-separator + 本包(供 -m musicmaster.separate.pipeline)")
    run("install", "-r", str(ROOT / "requirements" / "requirements-sep.txt"),
        "-i", PYPI_MIRROR, "--timeout", "60", "--retries", "5")
    run("install", "-e", str(ROOT), "--no-deps")

    print("\n[OK] 完成。把这个加进你的环境变量(GUI/CLI 用它找分离 venv):")
    print(f"    MUSICMASTER_SEP_PYTHON={py}")
    print("自测:")
    print(f'    "{py}" -m musicmaster.separate.pipeline 混音.wav --stages 1,2,3')


if __name__ == "__main__":
    main()
