"""singer-engine — 开源核心(Apache-2.0)。

对外稳定面:
    separate(input_wav)                 -> {vocal.wav, accomp.wav}(M2 人声分离)
    correct(vocal_wav, target, params)  -> 修正后的人声(贴向目标旋律)
    transcribe(audio_wav)               -> 目标旋律音符(扒谱)
    render(musicxml)                    -> {staff.svg, jianpu.svg/pdf}(M8 谱面渲染)

契约定义见 `musicmaster.core.contracts` 与仓库 `shared/schemas/`。
当前 correct 为骨架:核心 DSP 在 Phase0 打样(`spike/correct.py`,TASK-001)验收(G0)后升入本包。
M2 分离(separate)已落地(TASK-002,Demucs/MIT)。
M4b 转录(transcribe / transcribe_to_files)已落地(TASK-003;人声 pyin+音符分割,
通用路径 basic-pitch 可选),输出同 target.schema.json。
M8 渲染(render)已落地(TASK-004;五线谱 Verovio/LGPL 作为库,
简谱 jianpu-ly/Apache 生成 .ly + LilyPond CLI 独立子进程渲染)。
"""
# ⚠️ 同名陷阱:下面把 render/transcribe/separate/correct 等【函数】绑成了本包属性。
#    因此 `from musicmaster.core import render` 拿到的是【函数】,再 `render.render(...)` 会 AttributeError。
#    要拿【子模块】请用 `importlib.import_module("musicmaster.core.render")` 或 `from musicmaster.core.render import render`。
#    (合并日志 Entry#11 曾因此崩过一次。)
from .contracts import F0, Note, Target, TargetF0Frames, CorrectParams
from .correct import correct
from .transcribe import (
    transcribe,
    transcribe_to_files,
    is_available as is_transcription_available,
)
from .separate import (
    separate,
    separation_quality,
    is_available as is_separation_available,
)
from .render import (
    render,
    render_staff_svg,
    render_jianpu,
    musicxml_to_lilypond,
    lilypond_executable,
    is_available as is_render_available,
)

__all__ = [
    "separate",
    "separation_quality",
    "is_separation_available",
    "correct",
    "transcribe",
    "transcribe_to_files",
    "is_transcription_available",
    "render",
    "render_staff_svg",
    "render_jianpu",
    "musicxml_to_lilypond",
    "lilypond_executable",
    "is_render_available",
    "F0",
    "Note",
    "Target",
    "TargetF0Frames",
    "CorrectParams",
    "__version__",
]
__version__ = "0.0.1"
