#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分部 · 三段式人声处理流水线(各段用户自选,按序级联)。

三段:
  1 分离      mix → 人声(vocal) + 伴奏(accomp)        [bs_roformer 默认,GPU,保真最佳]
  2 去和声    人声 → 纯主唱(lead,去 backing vocals)   [karaoke gabox_v2 默认]
  3 降噪提纯  纯主唱 → 干净主唱                          [dereverb 默认(老板选定:稳/不闷) / deecho]

级联:第 N 段默认吃第 N-1 段的产物;若跳过某段,自动用最近一段产物或原始输入。

用法(在分离 venv 里跑,它有 audio-separator + GPU):
  python -m musicmaster.separate.pipeline 输入.wav                 # 默认全跑 1,2,3
  "$PY" 分部/pipeline.py 输入.wav --stages 1            # 只分离(出 人声+伴奏)
  "$PY" 分部/pipeline.py 输入.wav --stages 1,2          # 分离 + 去和声
  "$PY" 分部/pipeline.py 输入.wav --stages 2,3 --denoise deecho
产物默认写到 分部/输出/<输入名>/{1_分离,2_去和声,3_降噪}/,并复制最终结果为 最终_<输入名>.wav。

合规:副本内不计许可(走向开源,用最强方案)。所有重活走 audio-separator(GPU 顺序,不抢爆)。
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

# audio-separator 模型缓存目录:可用环境变量 MUSICMASTER_SEP_MODELS 覆盖(权重首次自动下载至此)
MODEL_DIR = os.environ.get("MUSICMASTER_SEP_MODELS") or r"C:\tmp\audio-separator-models"

# 各段可选模型(默认 = 老板/对比选定项)
SEP_MODELS = {
    "bs_roformer": "model_bs_roformer_ep_317_sdr_12.9755.ckpt",  # 默认:GPU,保真最佳
}
DEHARMONY_MODELS = {
    "gabox":    "mel_band_roformer_karaoke_gabox_v2.ckpt",                    # 默认(选定)
    "becruily": "mel_band_roformer_karaoke_becruily.ckpt",
    "aufr33":   "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt",   # 实测会抽水,不建议
}
DENOISE_MODELS = {
    "dereverb": "UVR-DeEcho-DeReverb.pth",   # 默认(老板选定:音量稳、不闷;取 (No Reverb) 干声轨)
    "deecho":   "UVR-De-Echo-Normal.pth",    # 备选(取 (No Echo) 轨)
}


def _separator(out_dir: Path):
    from audio_separator.separator import Separator
    return Separator(
        output_dir=str(out_dir),
        model_file_dir=MODEL_DIR,
        output_format="WAV",
        log_level=logging.ERROR,
    )


def _run_model(model_filename: str, input_wav: Path, work_dir: Path) -> list[Path]:
    """跑一个 audio-separator 模型,返回该 work_dir 下新产出的 wav 列表。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    before = set(work_dir.glob("*.wav"))
    sep = _separator(work_dir)
    sep.load_model(model_filename=model_filename)
    sep.separate(str(input_wav))
    return sorted(set(work_dir.glob("*.wav")) - before)


def _pick(files: list[Path], *needles: str) -> Path | None:
    for f in files:
        name = f.name.lower()
        if all(n.lower() in name for n in needles):
            return f
    return None


def _save(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(str(src), str(dst))
    return dst


def stage_separate(inp: Path, model_key: str, out_dir: Path, tmp: Path) -> dict:
    files = _run_model(SEP_MODELS[model_key], inp, tmp / "s1")
    out: dict = {}
    voc = _pick(files, "vocals")
    acc = _pick(files, "instrumental")
    if voc:
        out["vocal"] = _save(voc, out_dir / "vocal.wav")
    if acc:
        out["accomp"] = _save(acc, out_dir / "accomp.wav")
    return out


def stage_deharmony(inp: Path, model_key: str, out_dir: Path, tmp: Path) -> dict:
    files = _run_model(DEHARMONY_MODELS[model_key], inp, tmp / "s2")
    out: dict = {}
    lead = _pick(files, "vocals")  # karaoke 模型的主唱轨 = (Vocals)
    if lead:
        out["lead"] = _save(lead, out_dir / "lead.wav")
    return out


def stage_denoise(inp: Path, method: str, out_dir: Path, tmp: Path) -> dict:
    files = _run_model(DENOISE_MODELS[method], inp, tmp / "s3")
    needle = "no reverb" if method == "dereverb" else "no echo"
    out: dict = {}
    clean = _pick(files, needle)
    if clean:
        out["clean"] = _save(clean, out_dir / f"clean_{method}.wav")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="分部三段式人声处理(分离/去和声/降噪,可选)")
    ap.add_argument("input", help="输入音频(混音 wav/mp3/...)")
    ap.add_argument("--stages", default="1,2,3", help="要跑的段,逗号分隔,取自 1,2,3(默认全跑)")
    ap.add_argument("--sep-model", default="bs_roformer", choices=list(SEP_MODELS))
    ap.add_argument("--deharmony-model", default="gabox", choices=list(DEHARMONY_MODELS))
    ap.add_argument("--denoise", default="dereverb", choices=list(DENOISE_MODELS))
    ap.add_argument("--out-dir", default=None, help="输出目录(默认 分部/输出/<输入名>/)")
    args = ap.parse_args(argv)

    inp = Path(args.input).resolve()
    if not inp.exists():
        ap.error(f"输入不存在: {inp}")
    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    if not stages or any(s not in ("1", "2", "3") for s in stages):
        ap.error("--stages 仅支持 1/2/3 的子集,如 1 / 1,2 / 1,2,3 / 2,3")

    base = Path(args.out_dir).resolve() if args.out_dir else (Path.cwd() / "output" / inp.stem)
    base.mkdir(parents=True, exist_ok=True)

    produced: dict = {}
    cur = inp  # 级联输入(随每段更新)
    with tempfile.TemporaryDirectory(prefix="fenbu-") as td:
        tmp = Path(td)
        if "1" in stages:
            print(f"【1 分离】模型={args.sep_model}  输入={cur.name}")
            r = stage_separate(cur, args.sep_model, base / "1_分离", tmp)
            produced.update({f"1_{k}": v for k, v in r.items()})
            if "vocal" in r:
                cur = r["vocal"]
        if "2" in stages:
            print(f"【2 去和声】模型={args.deharmony_model}  输入={cur.name}")
            r = stage_deharmony(cur, args.deharmony_model, base / "2_去和声", tmp)
            produced.update({f"2_{k}": v for k, v in r.items()})
            if "lead" in r:
                cur = r["lead"]
        if "3" in stages:
            print(f"【3 降噪提纯】方法={args.denoise}  输入={cur.name}")
            r = stage_denoise(cur, args.denoise, base / "3_降噪", tmp)
            produced.update({f"3_{k}": v for k, v in r.items()})
            if "clean" in r:
                cur = r["clean"]

    # 最终结果复制到顶层,便于取用
    final = base / f"最终_{inp.stem}.wav"
    if cur != inp:
        _save(cur, final)

    print("\n产物:")
    for k, v in produced.items():
        print(f"  {k:10s} {v}")
    if cur != inp:
        print(f"\n最终输出: {final}")
    else:
        print("\n(未选任何有效段,或各段未产出)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
