#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""建主环境(CPU):GUI + 扒谱 + 互转 + 渲染。

    python scripts/setup_core.py [--venv .venv]

做的事:建 venv → 装 TensorFlow → 本地离线装 crepe(只带 full 权重)→ 装 requirements-core → 可编辑安装本包。

crepe 安装说明:它的 setup.py 会在构建时从 GitHub 下载 5 个权重文件,国内网络容易卡死;
这里改为从 PyPI 取源码包、只保留运行时真正用到的 model-full.h5,并优先走 GitHub 镜像
(可用 MUSICMASTER_GH_MIRROR 覆盖,默认 https://mirror.mikus.ink/https://github.com/)。
"""
from __future__ import annotations
import argparse, bz2, os, shutil, subprocess, sys, tarfile, tempfile, urllib.request, venv
from pathlib import Path

# 本脚本用【系统 Python】在默认 GBK 控制台运行;输出/重定向遇非 GBK 字符会 UnicodeEncodeError,统一切 UTF-8(防御式)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
GH_MIRROR = os.environ.get("MUSICMASTER_GH_MIRROR", "https://mirror.mikus.ink/https://github.com/")
CREPE_WEIGHT_URL = GH_MIRROR.rstrip("/") + "/marl/crepe/raw/models/model-full.h5.bz2"
CREPE_WEIGHT_FALLBACK = "https://github.com/marl/crepe/raw/models/model-full.h5.bz2"


def venv_python(d: Path) -> Path:
    return d / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _download(url: str, dest: Path, timeout: int = 120) -> None:
    """下载单个文件;走系统代理设置,失败时由调用方决定回退。"""
    with urllib.request.urlopen(url, timeout=timeout) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)


def _install_crepe(run) -> None:
    """crepe 0.0.16 的 setup.py 用 pkg_resources(setuptools>=81 已移除),且会下载全部 5 个权重。

    这里先把源码包下到本地、裁剪成只带 full 权重,再 --no-build-isolation 安装,
    避免新版 pip 构建隔离拉到无 pkg_resources 的 setuptools,也避免 GitHub 直连卡死。
    """
    tmp = Path(tempfile.mkdtemp(prefix="musicmaster-crepe-"))
    try:
        print("[4/5] 装 crepe(本地离线构建,只带 full 权重)")
        run("install", "setuptools<81")

        with urllib.request.urlopen("https://pypi.org/pypi/crepe/json", timeout=30) as r:
            import json
            meta = json.load(r)
        sdist_url = next(u["url"] for u in meta["urls"] if u["packagetype"] == "sdist")
        sdist = tmp / "crepe.tar.gz"
        print(f"      下载 crepe 源码: {sdist_url}")
        _download(sdist_url, sdist)

        with tarfile.open(sdist, "r:gz") as tar:
            tar.extractall(tmp)
        src = next(p for p in tmp.iterdir()
                   if p.is_dir() and (p / "crepe" / "core.py").exists())

        setup_py = src / "setup.py"
        text = setup_py.read_text(encoding="utf-8")
        text = text.replace(
            "weight_files = ['model-{}.h5'.format(cap) for cap in model_capacities]",
            "weight_files = ['model-full.h5']  # 只带运行时实际使用的 full 权重",
        )
        setup_py.write_text(text, encoding="utf-8")

        weight_h5 = src / "crepe" / "model-full.h5"
        if not weight_h5.exists():
            compressed = tmp / "model-full.h5.bz2"
            try:
                print(f"      下载 full 权重(镜像): {CREPE_WEIGHT_URL}")
                _download(CREPE_WEIGHT_URL, compressed, timeout=300)
            except Exception as e:
                print(f"      镜像失败({e}),回退 GitHub 直连...")
                _download(CREPE_WEIGHT_FALLBACK, compressed, timeout=300)
            with bz2.open(compressed, "rb") as z, open(weight_h5, "wb") as out:
                shutil.copyfileobj(z, out)

        run("install", "--no-deps", "--no-build-isolation", str(src))
        # crepe 声明了 imageio 依赖,但 requirements-core 未列出,补装以免 pip check 报错
        run("install", "imageio")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--venv", default=str(ROOT / ".venv"))
    a = ap.parse_args()
    vdir = Path(a.venv)

    if venv_python(vdir).exists():
        print(f"[1/5] 复用已有虚拟环境 {vdir}")
    else:
        print(f"[1/5] 建虚拟环境 {vdir}")
        venv.create(vdir, with_pip=True)
    py = str(venv_python(vdir))

    def run(*pip_args):
        subprocess.check_call([py, "-m", "pip", *pip_args])

    print("[2/5] 升级 pip")
    run("install", "-U", "pip")
    print("[3/5] 装 TensorFlow(CREPE/basic-pitch 后端)")
    run("install", "tensorflow==2.15.0")  # 固定 2.15.0:唯一同时满足 basic-pitch(<2.15.1)与 crepe 的版本
    _install_crepe(run)
    print("[5/5] 装 requirements-core + 本包")
    # requirements-core 里仍写着 crepe,直接安装会再次触发它的 setup.py 下载权重;
    # 这里过滤掉该行,crepe 已由 _install_crepe 离线装好。
    req = ROOT / "requirements" / "requirements-core.txt"
    lines = [ln for ln in req.read_text(encoding="utf-8").splitlines()
             if not ln.strip().lower().startswith("crepe")]
    filtered = Path(tempfile.gettempdir()) / "musicmaster-requirements-core.txt"
    filtered.write_text("\n".join(lines), encoding="utf-8")
    run("install", "-r", str(filtered))
    run("install", "--no-deps", "-e", str(ROOT))

    print("\n[OK] 完成。启动 GUI:双击 启动.bat")
    print(f"    (或命令行: {py} -m musicmaster.web.server)")
    print("简谱出图还需 LilyPond,并设 LILYPOND_EXE(见 README)。")


if __name__ == "__main__":
    main()
