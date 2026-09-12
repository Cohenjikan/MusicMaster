#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""建主环境(CPU):GUI + 扒谱 + 互转 + 渲染。

    python scripts/setup_core.py [--venv .venv]

做的事:建 venv → 装 TensorFlow → `--no-deps` 装 crepe(避免降级 TF;源码构建失败时自动改用本地构建)→ 装 requirements-core → 可编辑安装本包。
"""
from __future__ import annotations
import argparse, bz2, json, os, subprocess, sys, tarfile, tempfile, urllib.request, venv
from pathlib import Path

# 本脚本用【系统 Python】在默认 GBK 控制台运行;输出/重定向遇非 GBK 字符会 UnicodeEncodeError,统一切 UTF-8(防御式)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]


def venv_python(d: Path) -> Path:
    return d / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


CREPE_CAPS = ('tiny', 'small', 'medium', 'large', 'full')
# crepe 权重发布的真实位置;github.com/.../raw 会 302 到 raw.githubusercontent.com,后者通常更稳,故排在首位
CREPE_WEIGHT_MIRRORS = (
    'https://raw.githubusercontent.com/marl/crepe/models/',
    'https://github.com/marl/crepe/raw/models/',
)


def _open(url: str, timeout: int = 120):
    req = urllib.request.Request(url, headers={'User-Agent': 'curl/8'})
    return urllib.request.urlopen(req, timeout=timeout)


def _download(url: str, dst: Path, timeout: int = 120) -> None:
    with _open(url, timeout) as resp, open(dst, 'wb') as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)


def install_crepe(py: str) -> None:
    """装 crepe(上游只发源码包,没有 wheel),并绕开它在 Windows 上的两个坑:

      ① `setup.py` 里 `import pkg_resources` —— 新版 setuptools(≥81)已移除该模块,
         隔离构建必然 ModuleNotFoundError;
      ② `setup.py` 在构建时从 github.com/marl/crepe/raw/... 下 5 个权重文件,
         该地址在部分网络(含国内直连)会超时,构建随之失败。
    对策:自己取 sdist → 预置权重(走 raw.githubusercontent.com)
    → `--no-build-isolation` 在本地构建。全程 `--no-deps`,不动 TF 版本。
    """
    # 已装过就跳过(重跑本脚本不该白等);未装则直接本地构建,不去试不可靠的标准路径
    if subprocess.call([py, '-m', 'pip', 'show', 'crepe'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
        return
    print('     从 sdist 本地构建(上游 setup.py 需要 pkg_resources,且构建期要下权重)…')
    subprocess.check_call([py, '-m', 'pip', 'install', '-U', 'wheel'])  # 不隔离构建需要 wheel
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        with _open('https://pypi.org/pypi/crepe/json') as resp:
            meta = json.load(resp)
        sdist = next(u for u in meta['urls'] if u['packagetype'] == 'sdist')
        tgz = tmpdir / sdist['filename']
        print('     下载', sdist['filename'])
        _download(sdist['url'], tgz)
        with tarfile.open(tgz) as tf:
            try:
                tf.extractall(tmpdir, filter='data')  # Python 3.12+
            except TypeError:
                tf.extractall(tmpdir)
        src = next(p for p in tmpdir.iterdir() if p.is_dir() and p.name.startswith('crepe-'))
        for cap in CREPE_CAPS:
            h5 = src / 'crepe' / f'model-{cap}.h5'
            if h5.exists():
                continue
            bz = src / 'crepe' / f'model-{cap}.h5.bz2'
            for base in CREPE_WEIGHT_MIRRORS:
                try:
                    print('     取权重', f'model-{cap}.h5.bz2…')
                    _download(base + f'model-{cap}.h5.bz2', bz, timeout=600)
                    break
                except Exception as e:
                    print('     ', base, '不可用(', e, '),换下一个镜像')
            else:
                raise RuntimeError(f'权重 model-{cap}.h5.bz2 在所有镜像上均下载失败')
            with bz2.open(bz, 'rb') as f, open(h5, 'wb') as g:
                g.write(f.read())
        subprocess.check_call(
            [py, '-m', 'pip', 'install', '--no-deps', '--no-build-isolation', str(src)]
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--venv", default=str(ROOT / ".venv"))
    a = ap.parse_args()
    vdir = Path(a.venv)

    print(f"[1/5] 建虚拟环境 {vdir}")
    venv.create(vdir, with_pip=True)
    py = str(venv_python(vdir))

    def run(*pip_args):
        subprocess.check_call([py, "-m", "pip", *pip_args])

    print("[2/5] 升级 pip")
    run("install", "-U", "pip")
    print("[3/5] 装 TensorFlow(CREPE/basic-pitch 后端)")
    run("install", "tensorflow==2.15.0")  # 固定 2.15.0:唯一同时满足 basic-pitch(<2.15.1)与 crepe 的版本
    print("[4/5] 装 crepe(--no-deps,保 TF 不被降级)")
    install_crepe(py)
    print("[5/5] 装 requirements-core + 本包")
    run("install", "-r", str(ROOT / "requirements" / "requirements-core.txt"))
    run("install", "-e", str(ROOT))

    print("\n[OK] 完成。启动 GUI:双击 启动.bat")
    print(f"    (或命令行: {py} -m musicmaster.web.server)")
    print("简谱出图还需 LilyPond,并设 LILYPOND_EXE(见 README)。")


if __name__ == "__main__":
    main()
