"""§3 接口契约的 Python 类型镜像(与 shared/schemas/*.schema.json 一一对应)。

JSON 是模块间的真理来源;这里的 TypedDict 仅供引擎内部类型标注/IDE 提示。
两者若改动,必须同步(并在调度日志 §9 留痕)。
"""
from __future__ import annotations
from typing import List, TypedDict


class _F0Required(TypedDict):
    hop_ms: float
    sr: int
    f0_hz: List[float]
    voiced: List[int]


class F0(_F0Required, total=False):
    """M3 输出:f0.schema.json(hop_ms/sr/f0_hz/voiced 必填,detector 可选)"""
    detector: str  # 可选


class Note(TypedDict):
    """target.schema.json 中的单个音符"""
    midi: int
    start_s: float
    end_s: float


class Target(TypedDict):
    """M4a/M4b 输出:target.schema.json"""
    tempo: float
    notes: List[Note]


class TimeMap(TypedDict):
    user_s: List[float]
    target_s: List[float]


class TargetF0Frames(TypedDict):
    """M5 输出:target_f0_frames.schema.json"""
    hop_ms: float
    target_f0_hz: List[float]
    time_map: TimeMap


class CorrectParams(TypedDict, total=False):
    """M6 入参:correct_params.schema.json"""
    strength: float            # 0..1,默认 1.0
    preserve_vibrato: bool     # 默认 True
