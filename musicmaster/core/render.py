"""M8 谱面渲染(开源核心的对外函数)。

§3 契约:`musicxml → { jianpu.svg/pdf, staff.svg }`。
输入是一份 MusicXML 文件(来自 M4a 谱解析 / M4b 扒谱,见 transcribe.target_to_musicxml),
输出两类谱面图:

  - **五线谱 staff.svg** —— 用 **Verovio**(LGPL-3.0,作为「已加载的 Python 库」使用,§5 允许)。
    Verovio 把 MusicXML 直接渲染成 SVG,**纯进程内、无外部二进制、无权重下载**。

  - **简谱 jianpu.svg / jianpu.pdf** —— 用 **jianpu-ly**(Apache-2.0)把 MusicXML
    转成 LilyPond `.ly` 源(纯 Python,**不需要 LilyPond 二进制**),
    再调 **LilyPond CLI** 把 `.ly` 渲染成 SVG/PDF。

合规红线(§5,务必守住):
  - **LilyPond 只当独立 CLI 子进程调用**(`subprocess.run([lilypond, ...])`),
    **绝不 import / 链接**进本引擎进程 —— 这样输出谱子不受 LilyPond 的 GPL copyleft 传染。
    本模块对 LilyPond 的全部依赖,仅限于 `shutil.which("lilypond")` 探测 + `subprocess` 调用。
  - **Verovio 作为库加载是安全的**(LGPL,动态加载/调用,不静态链接)。
  - **不引入 psola/parselmouth(GPL)**;本模块不做音频重合成,天然无关。
  - **ffmpeg** 本模块不需要;如将来需要,亦只当独立 CLI 子进程。
  - jianpu-ly 内部生成 `.ly` 是纯 Python;**渲染 `.ly` 才需 LilyPond**——
    因此 LilyPond 缺失时:`.ly`、`.svg(staff)` 仍可产出,只有简谱图渲染这一步被跳过。

工程约束(遵循调度规则 5/6):
  - CPU-only;每次 LilyPond 子进程都带超时上界(默认 120s),绝不 hang。
  - LilyPond 不可用 → `is_available(jianpu=True)` 返回 False,`render(..., formats=("jianpu",))`
    抛清晰 RuntimeError;测试 graceful skip,**绝不联网、绝不卡死**。

用法:
    from musicmaster.core import render, is_render_available
    out = render("score.musicxml", "out_dir/")            # 默认尽力出 staff + jianpu
    # out == {"staff": Path(...staff.svg), "jianpu": Path(...jianpu.svg)}  (jianpu 视 LilyPond 而定)
    if is_render_available(jianpu=True):                   # LilyPond 在 PATH 上才为 True
        out = render("score.musicxml", "out_dir/", formats=("jianpu",), jianpu_format="pdf")
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Union

PathLike = Union[str, Path]

# 默认产出两类谱面。
DEFAULT_FORMATS = ("jianpu", "staff")
# LilyPond 子进程默认超时(秒);CPU-only,单页谱足够,绝不无界等待。
DEFAULT_LILYPOND_TIMEOUT = 120.0
# 简谱默认输出格式(契约允许 svg 或 pdf)。
DEFAULT_JIANPU_FORMAT = "svg"
# 产物默认文件名(契约约定 staff.svg / jianpu.<svg|pdf>)。
_STAFF_NAME = "staff.svg"
_JIANPU_STEM = "jianpu"
_JIANPU_LY_NAME = "jianpu.ly"  # 中间 LilyPond 源(始终产出,便于调试/离线渲染)


# --------------------------------------------------------------------------- #
# 可用性自检(import 守卫 + LilyPond CLI 探测)。绝不在探测时联网/下载。
# --------------------------------------------------------------------------- #
def _verovio_importable() -> bool:
    import importlib.util

    return importlib.util.find_spec("verovio") is not None


def _jianpu_ly_importable() -> bool:
    import importlib.util

    return importlib.util.find_spec("jianpu_ly") is not None


def lilypond_executable() -> Optional[str]:
    """查找 LilyPond 可执行文件;找不到返回 None。

    顺序:① 环境变量 `LILYPOND_EXE`(指向 lilypond.exe 全路径)——便于在不污染全局 PATH
    的前提下使用(Windows 上 LilyPond 的 bin DLL 若进全局 PATH 会与 librosa/numpy/TF 冲突);
    ② `shutil.which("lilypond")`(PATH 探测)。
    **只做探测**,不调用、不下载;真正使用时 subprocess 调用本函数返回值(§5:独立 CLI)。
    """
    import os

    exe = os.environ.get("LILYPOND_EXE")
    if exe and Path(exe).is_file():
        return exe
    return shutil.which("lilypond")


def is_available(*, staff: bool = True, jianpu: bool = False) -> bool:
    """谱面渲染是否可用(按需查不同后端)。

    Args:
        staff:  需要五线谱渲染(Verovio,LGPL,纯库)。
        jianpu: 需要简谱渲染(jianpu-ly 生成 .ly + LilyPond CLI 渲染图)。
                注意:仅生成 `.ly` 不需要 LilyPond;**渲染成图**才需要 LilyPond,
                故 jianpu=True 时本函数要求 LilyPond 在 PATH 上。

    Returns:
        True 表示请求的后端均可无阻塞跑完对应渲染。
    """
    ok = True
    if staff:
        ok = ok and _verovio_importable()
    if jianpu:
        ok = ok and _jianpu_ly_importable() and (lilypond_executable() is not None)
    return ok


def is_jianpu_source_available() -> bool:
    """是否能把 MusicXML 转成 LilyPond `.ly` 源(纯 Python,不需 LilyPond)。

    用于区分「能生成 .ly」(jianpu-ly importable)与「能渲染成图」(还需 LilyPond)。
    """
    return _jianpu_ly_importable()


# --------------------------------------------------------------------------- #
# 输入读取。
# --------------------------------------------------------------------------- #
def _read_musicxml_text(musicxml: PathLike) -> str:
    src = Path(musicxml)
    if not src.exists():
        raise FileNotFoundError(f"输入 MusicXML 不存在: {src}")
    # MusicXML 是 UTF-8 文本;.mxl(压缩)不在本契约范围(契约入参是 musicxml 文本)。
    return src.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 五线谱:Verovio(LGPL,作为库加载)→ SVG。纯进程内,无外部二进制。
# --------------------------------------------------------------------------- #
def _ascii_verovio_resource_dir() -> Optional[str]:
    """返回纯 ASCII 的 Verovio 资源(字体/bounding box)目录。

    Verovio 的 C++ 字体加载器无法从含非 ASCII 字符的路径读取字体数据
    (项目位于 "…/singer - 副本/…" 时会报 "font resources are not available")。
    若包内 data 路径非纯 ASCII,则复制到一个 ASCII 临时目录并返回。
    """
    import os
    import shutil
    import tempfile
    import verovio

    pkg_data = os.path.join(os.path.dirname(verovio.__file__), "data")
    try:
        pkg_data.encode("ascii")
        return pkg_data
    except UnicodeEncodeError:
        pass
    base = tempfile.gettempdir()
    try:
        base.encode("ascii")
    except UnicodeEncodeError:
        base = "C:\\tmp"
    dst = os.path.join(base, "verovio_data_ascii")
    if not os.path.isdir(dst) or not os.listdir(dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copytree(pkg_data, dst, dirs_exist_ok=True)
    return dst


def render_staff_svg(musicxml: PathLike, out_svg: PathLike, *, page: int = 1) -> Path:
    """MusicXML → 五线谱 SVG(Verovio)。

    Args:
        musicxml: 输入 MusicXML 文件路径。
        out_svg:  输出 SVG 路径。
        page:     渲染第几页(从 1 起);Verovio 按 paper 设置自动分页,默认取第 1 页。

    Returns:
        写出的 SVG 路径。

    Raises:
        FileNotFoundError: 输入不存在。
        RuntimeError:      verovio 不可用,或加载/渲染失败。
    """
    if not _verovio_importable():
        raise RuntimeError(
            "五线谱渲染需要 verovio(LGPL,作为库加载):请 `pip install verovio`。"
        )
    import verovio

    xml_text = _read_musicxml_text(musicxml)
    tk = verovio.toolkit(False)  # 不自动加载资源
    _rp = _ascii_verovio_resource_dir()
    if _rp:
        tk.setResourcePath(_rp)  # 规避非 ASCII 项目路径导致 Verovio 字体加载失败
    # 用 MusicXML 作为输入格式显式加载(Verovio 会自动识别,但显式更稳)。
    try:
        tk.setInputFrom("musicxml")  # 老/新版本 API 名一致;失败则走自动识别
    except Exception:
        pass
    if not tk.loadData(xml_text):
        raise RuntimeError("Verovio 无法解析该 MusicXML(loadData 返回 False)。")

    page_count = int(tk.getPageCount() or 1)
    pg = max(1, min(int(page), page_count))
    svg = tk.renderToSVG(pg)
    if not svg or "<svg" not in svg[:200]:
        raise RuntimeError("Verovio 渲染结果不是有效 SVG。")

    out_svg = Path(out_svg)
    out_svg.parent.mkdir(parents=True, exist_ok=True)
    out_svg.write_text(svg, encoding="utf-8")
    return out_svg


# --------------------------------------------------------------------------- #
# 简谱:MusicXML → jianpu-ly → LilyPond .ly 源(纯 Python)。
# --------------------------------------------------------------------------- #
def _sanitize_musicxml_tempo(xml_text: str) -> str:
    """把 MusicXML 里的非整数 BPM 取整,避免 jianpu-ly 解析崩溃。

    jianpu-ly 的 process_input 遇到小数 BPM(如 117.188)会抛
    'Unrecognised command 4=117.188'。精确时长保存在 notes.json/MIDI,
    记谱 BPM 取整对本管线无损;仅改写含小数点的 tempo,整数原样保留。
    """

    def _round(m):
        try:
            return f"{m.group(1)}{int(round(float(m.group(2))))}{m.group(3)}"
        except ValueError:
            return m.group(0)

    xml_text = re.sub(r"(<per-minute>)\s*(\d+\.\d+)\s*(</per-minute>)", _round, xml_text)
    xml_text = re.sub(r'(\btempo=")(\d+\.\d+)(")', _round, xml_text)
    return xml_text


def musicxml_to_lilypond(musicxml: PathLike) -> str:
    """MusicXML → LilyPond `.ly` 源文本(jianpu-ly,纯 Python,**不需 LilyPond**)。

    步骤:`jianpu_ly.xml2jianpu(xml)`(MusicXML→简谱中间格式)
          → `jianpu_ly.process_input(...)`(简谱中间格式→完整 .ly,含简谱记法)。

    Raises:
        FileNotFoundError: 输入不存在。
        RuntimeError:      jianpu-ly 不可用,或转换失败。
    """
    if not _jianpu_ly_importable():
        raise RuntimeError(
            "简谱转换需要 jianpu-ly(Apache-2.0):请 `pip install jianpu-ly`。"
        )
    import jianpu_ly

    xml_text = _read_musicxml_text(musicxml)
    xml_text = _sanitize_musicxml_tempo(xml_text)  # jianpu-ly 不接受非整数 BPM,先就地取整
    try:
        jianpu_src = jianpu_ly.xml2jianpu(xml_text)
        ly = jianpu_ly.process_input(jianpu_src)
    except Exception as e:  # 转换失败给出可诊断信息,不静默
        raise RuntimeError(f"jianpu-ly 转换 MusicXML→LilyPond 失败: {e}") from e
    if not ly or "\\score" not in ly:
        raise RuntimeError("jianpu-ly 生成的 .ly 源不含 \\score,可能输入不被支持。")
    return ly


def _run_lilypond(
    ly_path: Path,
    out_basename: Path,
    fmt: str,
    timeout: float,
) -> Path:
    """调 LilyPond CLI 把 `.ly` 渲染成 SVG/PDF(**独立子进程,§5 合规**)。

    Args:
        ly_path:      `.ly` 源文件路径。
        out_basename: 输出基名(不含扩展名);LilyPond 以 `-o` 接基名,自动补扩展名。
        fmt:          "svg" 或 "pdf"。
        timeout:      子进程超时(秒)。

    Returns:
        渲染出的图文件路径(out_basename + .svg/.pdf)。

    Raises:
        RuntimeError:               LilyPond 不在 PATH。
        subprocess.TimeoutExpired:  超时(由调用方决定如何处理)。
        RuntimeError:               LilyPond 返回非零或未产出预期文件。
    """
    lily = lilypond_executable()
    if lily is None:
        raise RuntimeError(
            "LilyPond 未安装或不在 PATH:简谱渲染需要 LilyPond CLI(GPL,**仅作独立进程调用**)。"
            "请安装 LilyPond 后重试,或改用 staff(Verovio)。"
        )

    fmt = fmt.lower()
    if fmt not in ("svg", "pdf"):
        raise ValueError(f"jianpu 输出格式只支持 svg/pdf,收到: {fmt}")

    # LilyPond 的后端选择:--svg 出 SVG;默认(--pdf)出 PDF。
    # -dno-point-and-click 去掉指向用户输入路径的链接(production 友好)。
    backend_args = ["-dbackend=svg", "--svg"] if fmt == "svg" else ["--pdf"]
    cmd = [
        lily,
        "-dno-point-and-click",
        "-s",  # silent(减少 stdout 噪声)
        *backend_args,
        "-o",
        str(out_basename),
        str(ly_path),
    ]
    # 显式独立子进程;不继承交互;带超时上界(CPU-only 防卡死)。
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        cwd=str(ly_path.parent),
    )
    produced = out_basename.with_suffix(f".{fmt}")
    # LilyPond 多页 SVG 会写成 <base>-1.svg/<base>-page1.svg;单页通常直接 <base>.svg。
    if not produced.exists():
        cands = sorted(out_basename.parent.glob(f"{out_basename.name}*.{fmt}"))
        if cands:
            produced = cands[0]
    if proc.returncode != 0 and not produced.exists():
        err = (proc.stderr or b"").decode("utf-8", "replace")[-600:]
        raise RuntimeError(
            f"LilyPond 渲染失败(returncode={proc.returncode}):\n{err}"
        )
    if not produced.exists():
        raise RuntimeError(
            f"LilyPond 未产出预期 {fmt} 文件(基名 {out_basename})。"
        )
    return produced


def render_jianpu(
    musicxml: PathLike,
    out_dir: PathLike,
    *,
    jianpu_format: str = DEFAULT_JIANPU_FORMAT,
    lilypond_timeout: float = DEFAULT_LILYPOND_TIMEOUT,
    keep_ly: bool = True,
) -> Dict[str, Path]:
    """MusicXML → 简谱图(SVG/PDF)。生成 `.ly`(纯 Python)+ LilyPond CLI 渲染。

    始终写出中间 `.ly`(keep_ly=True);若 LilyPond 可用则进一步渲染成图。

    Returns:
        {"ly": Path, "jianpu": Path}  —— `.ly` 源 + 渲染出的图。
        若仅生成 `.ly`(此函数要求渲染,故 LilyPond 缺失会抛错,见下)。

    Raises:
        FileNotFoundError: 输入不存在。
        RuntimeError:      jianpu-ly 不可用 / LilyPond 不可用 / 渲染失败。
        subprocess.TimeoutExpired: LilyPond 超时。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ly_text = musicxml_to_lilypond(musicxml)
    ly_path = out_dir / _JIANPU_LY_NAME
    ly_path.write_text(ly_text, encoding="utf-8")

    out_base = out_dir / _JIANPU_STEM
    produced = _run_lilypond(ly_path, out_base, jianpu_format, lilypond_timeout)

    result: Dict[str, Path] = {"jianpu": produced}
    if keep_ly:
        result["ly"] = ly_path
    elif ly_path.exists():
        ly_path.unlink()
    return result


# --------------------------------------------------------------------------- #
# 对外主函数:musicxml → { jianpu.svg/pdf, staff.svg }(§3 M8 契约)。
# --------------------------------------------------------------------------- #
def render(
    musicxml: PathLike,
    out_dir: Optional[PathLike] = None,
    *,
    formats: Iterable[str] = DEFAULT_FORMATS,
    page: int = 1,
    jianpu_format: str = DEFAULT_JIANPU_FORMAT,
    lilypond_timeout: float = DEFAULT_LILYPOND_TIMEOUT,
    require_all: bool = False,
) -> Dict[str, Path]:
    """谱面渲染:MusicXML → {staff.svg(Verovio), jianpu.svg/pdf(jianpu-ly+LilyPond)}。

    §3 M8 契约。两类谱面相互独立;默认**尽力而为**(best-effort):
    某后端不可用时跳过该格式而非整体失败(除非 require_all=True 或显式只点该格式)。

    Args:
        musicxml:         输入 MusicXML 文件路径。
        out_dir:          输出目录;None → 输入文件同目录下的 `<stem>_score/`。
        formats:          需要的产物子集,取自 {"staff","jianpu"};默认两者都要。
        page:             五线谱渲染页码(Verovio,从 1 起)。
        jianpu_format:    简谱输出格式 "svg"(默认)或 "pdf"。
        lilypond_timeout: LilyPond 子进程超时(秒)。
        require_all:      True → 任一请求格式不可用即抛错;
                          False(默认)→ best-effort,跳过不可用格式。
                          注意:若 `formats` 只含单一格式,则该格式**必须**成功
                          (否则调用方拿不到任何东西),此时按"必需"处理。

    Returns:
        Dict[str, Path],键 ⊆ {"staff","jianpu"}(可能还含 "jianpu_ly" 指向中间 .ly)。
        - "staff":     staff.svg 路径(Verovio)。
        - "jianpu":    jianpu.<svg|pdf> 路径(LilyPond 渲染成功时)。
        - "jianpu_ly": jianpu.ly 中间源路径(只要 jianpu-ly 可用就有,即便 LilyPond 缺失)。

    Raises:
        FileNotFoundError: 输入不存在。
        ValueError:        formats 为空或含未知项。
        RuntimeError:      "必需"的后端不可用 / 渲染失败(见 require_all 语义)。
    """
    src = Path(musicxml)
    if not src.exists():
        raise FileNotFoundError(f"输入 MusicXML 不存在: {src}")

    fmts: List[str] = [f.lower() for f in formats]
    if not fmts:
        raise ValueError("formats 不能为空;至少要 'staff' 或 'jianpu' 其一。")
    unknown = [f for f in fmts if f not in ("staff", "jianpu")]
    if unknown:
        raise ValueError(f"未知 formats 项 {unknown};仅支持 'staff' / 'jianpu'。")

    if out_dir is None:
        out_dir = src.parent / f"{src.stem}_score"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 单一格式时该格式必需;多格式时遵循 require_all。
    single = len(set(fmts)) == 1
    result: Dict[str, Path] = {}

    # ── 五线谱(Verovio) ──
    if "staff" in fmts:
        staff_required = require_all or single
        if _verovio_importable():
            staff_path = render_staff_svg(src, out_dir / _STAFF_NAME, page=page)
            result["staff"] = staff_path
        elif staff_required:
            raise RuntimeError(
                "请求了 staff 但 verovio 不可用:请 `pip install verovio`(LGPL,作为库加载)。"
            )
        # else: best-effort,跳过。

    # ── 简谱(jianpu-ly + LilyPond) ──
    if "jianpu" in fmts:
        jianpu_required = require_all or single
        if _jianpu_ly_importable():
            # `.ly` 源可纯 Python 生成,始终先落盘(即便 LilyPond 缺失也给用户中间产物)。
            # best-effort:多格式时简谱 .ly 生成失败(如不被支持的 MusicXML)
            # 不得拖垮已成功的 staff —— 故包裹在 try 内,非必需时跳过而非整体抛出。
            try:
                ly_text = musicxml_to_lilypond(src)
            except Exception:
                if jianpu_required:
                    raise
                ly_text = None
            if ly_text is not None:
                ly_path = out_dir / _JIANPU_LY_NAME
                ly_path.write_text(ly_text, encoding="utf-8")
                result["jianpu_ly"] = ly_path

                if lilypond_executable() is not None:
                    try:
                        produced = _run_lilypond(
                            ly_path, out_dir / _JIANPU_STEM, jianpu_format, lilypond_timeout
                        )
                        result["jianpu"] = produced
                    except subprocess.TimeoutExpired as e:
                        if jianpu_required:
                            raise RuntimeError(
                                f"LilyPond 渲染简谱超时(>{lilypond_timeout}s)。"
                            ) from e
                        # best-effort:超时则只保留 .ly。
                elif jianpu_required:
                    raise RuntimeError(
                        "请求了 jianpu 图但 LilyPond 不在 PATH:简谱图渲染需要 LilyPond CLI"
                        "(GPL,**仅作独立进程调用**)。已产出中间 .ly;安装 LilyPond 后可离线渲染。"
                    )
                # else: best-effort,保留 .ly,跳过渲染。
        elif jianpu_required:
            raise RuntimeError(
                "请求了 jianpu 但 jianpu-ly 不可用:请 `pip install jianpu-ly`(Apache-2.0)。"
            )
        # else: best-effort,跳过。

    return result
