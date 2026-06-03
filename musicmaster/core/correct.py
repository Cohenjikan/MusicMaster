"""M3+M5+M6 修音整合(开源核心的对外函数)。

骨架阶段:真正的 DSP 在 Phase0 打样(`spike/correct.py`,TASK-001)中验证;
G0 验收通过后,把其中的 f0 检测 / DTW 对齐 / WORLD 重合成升入本模块。
契约:见 shared/schemas/{f0,target,target_f0_frames,correct_params}.schema.json
合规红线(§5):重合成只用 pyworld;禁止把 psola(GPL)链接进本引擎。
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional, Union

from .contracts import Target, CorrectParams

PathLike = Union[str, Path]


def correct(
    vocal_wav: PathLike,
    target: Target,
    params: Optional[CorrectParams] = None,
    out_wav: Optional[PathLike] = None,
) -> Path:
    """把人声修向目标旋律,返回修正后的 wav 路径。

    Args:
        vocal_wav: 用户人声 wav 路径(mono 优先)。
        target:    目标旋律(target.schema.json)。
        params:    修音参数(correct_params.schema.json);None 用默认。
        out_wav:   输出路径;None 则在输入旁生成 *_corrected.wav。

    Pipeline(规划):f0 检测(pyin/crepe/rmvpe) → DTW 对齐到 target →
                    WORLD 分析/替换 f0/重合成 → 写出。
    """
    raise NotImplementedError(
        "musicmaster.core.correct 尚未实现。Phase0 打样见 spike/correct.py;"
        "G0 验收通过后将其 DSP 升入本函数。"
    )
