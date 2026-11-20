#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""建修音换音色环境(GPU):

  • 拉取 DiffPitcher(修音准)与 Seed-VC(换音色)源码到 vendor/
  • 把验证过的修音脚本(run_qt4 / run_full)拷进 vendor/DiffPitcher/
  • 建两个独立 venv:vendor/.venv-neural(DiffPitcher)/ vendor/.venv-svc(Seed-VC)
  • 提示权重获取(DiffPitcher ckpts 需放到 vendor/DiffPitcher/ckpts/;Seed-VC 首次运行自动下)

    python scripts/setup_vocal.py [--cuda cu124] [--skip-clone] [--skip-venv]

注:Seed-VC 用它自己锁定版本的 requirements(torch==2.4 等),故与 DiffPitcher 分两个 venv。
"""
from __future__ import annotations
import argparse, os, shutil, subprocess, sys, venv
from pathlib import Path

# 系统 Python + GBK 控制台:输出/重定向遇非 GBK 字符会 UnicodeEncodeError,统一切 UTF-8(防御式)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"
DP_DIR = VENDOR / "DiffPitcher"
SVC_DIR = VENDOR / "seed-vc"
# 国内网络:git clone 走 GitHub 镜像(可用 MUSICMASTER_GH_MIRROR 覆盖);权重走 HF 镜像。
GH_MIRROR = os.environ.get("MUSICMASTER_GH_MIRROR", "https://mirror.mikus.ink/https://github.com/")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
DP_REPO = GH_MIRROR.rstrip("/") + "/haidog-yaqub/DiffPitcher"
SVC_REPO = GH_MIRROR.rstrip("/") + "/Plachtaa/seed-vc"
DP_SCRIPTS = ROOT / "musicmaster" / "vocal" / "diffpitcher_scripts"
# 国内加速:Windows 上 PyPI 的 torch 轮子是 CPU 版(无 nvidia-* 依赖),
# 必须走 PyTorch CUDA 索引;腾讯云镜像了整套 /whl/cu124/(实测 ~20 MB/s),别用官方站。
TORCH_VERSION = os.environ.get("MUSICMASTER_TORCH_VERSION", "2.6.0")
# Seed-VC 自带 requirements 锁 2.4.0 / 0.19.0 / 2.4.0,单独一套,别和 DiffPitcher 混。
SVC_TORCH_VERSION = os.environ.get("MUSICMASTER_SVC_TORCH_VERSION", "2.4.0")
SVC_TORCHVISION_VERSION = os.environ.get("MUSICMASTER_SVC_TORCHVISION_VERSION", "0.19.0")
SVC_TORCHAUDIO_VERSION = os.environ.get("MUSICMASTER_SVC_TORCHAUDIO_VERSION", "2.4.0")
PYPI_MIRROR = os.environ.get("MUSICMASTER_PYPI_MIRROR", "https://mirrors.cloud.tencent.com/pypi/simple/")
PYTORCH_MIRROR = os.environ.get(
    "MUSICMASTER_PYTORCH_MIRROR", "https://mirrors.cloud.tencent.com/pytorch-wheels/whl/{cuda}/")


def install_torch(run, cuda: str, torch_version: str | None = None,
                  torchaudio_version: str | None = None, with_vision: bool = False) -> None:
    """Windows 走腾讯云 PyTorch CUDA 镜像;其他平台回退 PyTorch 官方索引。"""
    tv = torch_version or TORCH_VERSION
    ta = torchaudio_version or tv
    pkgs = [f"torch=={tv}"]
    if with_vision:
        pkgs.append(f"torchvision=={SVC_TORCHVISION_VERSION}")
    pkgs.append(f"torchaudio=={ta}")
    if os.name == "nt":
        idx = PYTORCH_MIRROR.format(cuda=cuda)
        print(f"      [torch] CUDA 镜像: {idx} ({', '.join(pkgs)})")
        run("install", *pkgs, "--index-url", idx, "--timeout", "60", "--retries", "5")
    else:
        run("install", *pkgs, "--index-url", f"https://download.pytorch.org/whl/{cuda}")


def filtered_seedvc_requirements(req: Path) -> Path:
    """剔除 seed-vc requirements 开头的 nightly --index-url 行(会直连 download.pytorch.org 卡死)。

    保留其锁定的 torch==2.4.0 / torchvision==0.19.0 / torchaudio==2.4.0;
    这些会由 install_torch 先从腾讯云 PyTorch CUDA 镜像装好,pip 看到已满足就不会重下。
    """
    out = VENDOR / "_seed-vc-requirements-filtered.txt"
    lines = []
    for raw in req.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = raw.strip()
        if s.startswith(("torch ", "torchvision ", "torchaudio ")) and "--index-url" in s:
            print(f"[filter] 跳过 nightly 行: {s}")
            continue
        lines.append(raw)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def venv_python(d: Path) -> Path:
    return d / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cuda", default="cu124")
    ap.add_argument("--skip-clone", action="store_true")
    ap.add_argument("--skip-venv", action="store_true")
    a = ap.parse_args()
    VENDOR.mkdir(parents=True, exist_ok=True)

    if not a.skip_clone:
        if not DP_DIR.exists():
            print(f"[clone] {DP_REPO} → {DP_DIR}")
            subprocess.check_call(["git", "clone", "--depth", "1", DP_REPO, str(DP_DIR)])
        if not SVC_DIR.exists():
            print(f"[clone] {SVC_REPO} → {SVC_DIR}")
            subprocess.check_call(["git", "clone", "--depth", "1", SVC_REPO, str(SVC_DIR)])

    # 把验证过的修音脚本拷进 DiffPitcher(它们 import DiffPitcher 的 pitch_controller,需在仓库内)
    if DP_DIR.exists():
        for s in ("run_qt4.py", "run_full.py"):
            src = DP_SCRIPTS / s
            if src.is_file():
                shutil.copy(src, DP_DIR / s)
                print(f"[copy] {s} → vendor/DiffPitcher/")

    if not a.skip_venv:
        # ── DiffPitcher venv ──
        neural = VENDOR / ".venv-neural"
        print(f"[venv] {neural}")
        if not venv_python(neural).exists():
            venv.create(neural, with_pip=True)
        npy = str(venv_python(neural))
        subprocess.check_call([npy, "-m", "pip", "install", "-U", "pip"])
        install_torch(lambda *args: subprocess.check_call([npy, "-m", "pip", *args]), a.cuda)
        subprocess.check_call([npy, "-m", "pip", "install", "-r", str(ROOT / "requirements" / "requirements-vocal.txt")])

        # ── Seed-VC venv(用其自带 requirements,版本锁定)──
        svc = VENDOR / ".venv-svc"
        print(f"[venv] {svc}")
        if not venv_python(svc).exists():
            venv.create(svc, with_pip=True)
        spy = str(venv_python(svc))
        subprocess.check_call([spy, "-m", "pip", "install", "-U", "pip"])
        req = SVC_DIR / "requirements.txt"
        if req.is_file():
            # 先从国内镜像装锁定版 torch 三件套,再用过滤掉 nightly 行的 requirements
            install_torch(lambda *args: subprocess.check_call([spy, "-m", "pip", *args]), a.cuda,
                          SVC_TORCH_VERSION, SVC_TORCHAUDIO_VERSION, with_vision=True)
            req = filtered_seedvc_requirements(req)
            subprocess.check_call([spy, "-m", "pip", "install", "-r", str(req),
                                   "--timeout", "60", "--retries", "5"])

    print("\n-------- 权重 --------")
    print("- DiffPitcher:把 ckpts/ 放到 vendor/DiffPitcher/ckpts/:")
    print("    - world_fixed_40.pt")
    print("    - bigvgan_24khz_100band/g_05000000.pt + config.json")
    print("  (来源见 DiffPitcher 仓库 README / HF;MIT)")
    print("- Seed-VC:首次运行 inference.py 自动从 HuggingFace 下载(Plachta/Seed-VC、campplus、bigvgan_v2 等)。")

    print("\n[OK] 完成。设这些环境变量(GUI/CLI 用):")
    print(f"    MUSICMASTER_DIFFPITCHER_DIR={DP_DIR}")
    print(f"    MUSICMASTER_VOCAL_PYTHON={venv_python(VENDOR / '.venv-neural')}")
    print(f"    MUSICMASTER_SEEDVC_DIR={SVC_DIR}")
    print(f"    MUSICMASTER_SVC_PYTHON={venv_python(VENDOR / '.venv-svc')}")
    print("\n自测(三个输入:原始清唱 / 去和声参考 / 你自己的清唱):")
    print("    python -m musicmaster.vocal.pipeline --raw raw.wav --ref ref.wav --self self.wav --out out/")


if __name__ == "__main__":
    main()
