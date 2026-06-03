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
DP_REPO = "https://github.com/haidog-yaqub/DiffPitcher"
SVC_REPO = "https://github.com/Plachtaa/seed-vc"
DP_SCRIPTS = ROOT / "musicmaster" / "vocal" / "diffpitcher_scripts"


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
        venv.create(neural, with_pip=True)
        npy = str(venv_python(neural))
        subprocess.check_call([npy, "-m", "pip", "install", "-U", "pip"])
        subprocess.check_call([npy, "-m", "pip", "install", "torch", "torchaudio",
                               "--index-url", f"https://download.pytorch.org/whl/{a.cuda}"])
        subprocess.check_call([npy, "-m", "pip", "install", "-r", str(ROOT / "requirements-vocal.txt")])

        # ── Seed-VC venv(用其自带 requirements,版本锁定)──
        svc = VENDOR / ".venv-svc"
        print(f"[venv] {svc}")
        venv.create(svc, with_pip=True)
        spy = str(venv_python(svc))
        subprocess.check_call([spy, "-m", "pip", "install", "-U", "pip"])
        req = SVC_DIR / "requirements.txt"
        if req.is_file():
            subprocess.check_call([spy, "-m", "pip", "install", "-r", str(req)])

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
