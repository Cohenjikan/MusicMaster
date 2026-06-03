"""singer-engine CLI(骨架)。

安装后提供 `singer` 命令。当前 correct/transcribe 为骨架,
Phase0 可用的修音脚本见仓库 `spike/correct.py`。
"""
from __future__ import annotations
import argparse
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="singer", description="singer-engine 开源核心 CLI")
    sub = parser.add_subparsers(dest="cmd")

    p_c = sub.add_parser("correct", help="人声 + 目标旋律 → 修正人声")
    p_c.add_argument("vocal_wav")
    p_c.add_argument("target", help="MIDI/MusicXML 或 target.json")
    p_c.add_argument("-o", "--out")
    p_c.add_argument("--strength", type=float, default=1.0)

    p_t = sub.add_parser("transcribe", help="音频 → 谱(音符):out.mid + out.musicxml + notes.json")
    p_t.add_argument("audio_wav")
    p_t.add_argument("-o", "--out-dir", help="产物输出目录(默认 <stem>_transcribe/)")
    p_t.add_argument("--instrument", action="store_true",
                     help="通用乐器路径(basic-pitch);默认人声主旋律路径")

    p_r = sub.add_parser("render", help="MusicXML → 谱面:staff.svg(五线谱)+ jianpu.svg/pdf(简谱)")
    p_r.add_argument("musicxml")
    p_r.add_argument("-o", "--out-dir", help="产物输出目录(默认 <stem>_score/)")
    p_r.add_argument("--formats", default="staff,jianpu",
                     help="逗号分隔,取自 staff,jianpu(默认两者;best-effort)")
    p_r.add_argument("--jianpu-format", default="svg", choices=("svg", "pdf"),
                     help="简谱输出格式(默认 svg)")
    p_r.add_argument("--page", type=int, default=1, help="五线谱渲染页码(默认 1)")

    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 0

    if args.cmd == "transcribe":
        from .transcribe import transcribe_to_files
        out = transcribe_to_files(
            args.audio_wav, args.out_dir, instrument=args.instrument
        )
        print("[transcribe] 产物:")
        for k in ("midi", "musicxml", "notes"):
            print(f"  {k:9s} {out[k]}")
        return 0

    if args.cmd == "render":
        from .render import render
        fmts = [f.strip() for f in args.formats.split(",") if f.strip()]
        out = render(
            args.musicxml, args.out_dir,
            formats=fmts, page=args.page, jianpu_format=args.jianpu_format,
        )
        print("[render] 产物:")
        for k in ("staff", "jianpu", "jianpu_ly"):
            if k in out:
                print(f"  {k:9s} {out[k]}")
        if "jianpu" not in out and "jianpu" in fmts:
            print("  (简谱图未渲染:LilyPond 不在 PATH;已产出中间 jianpu.ly,安装 LilyPond 后可离线渲染)")
        return 0

    print(
        f"[singer-engine 骨架] 命令 '{args.cmd}' 尚未实现。\n"
        f"Phase0 修音打样请用: python spike/correct.py(TASK-001)。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
