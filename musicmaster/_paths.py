# -*- coding: utf-8 -*-
"""集中解析「机器相关路径」(各 GPU venv 的 python / 第三方仓库目录)。

优先级:环境变量 > <repo>/paths.local.json > 调用方给的默认值。

为什么需要它:分离 / 修音 / 换音色是 GPU 重依赖,各有独立 venv 与权重,
路径因机器而异。把它们写进 `paths.local.json`(已 gitignore,不入库),
GUI/CLI 即可自动找到,无需用户每次手动设环境变量。模板见 `paths.local.json.example`。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_LOCAL = _REPO / "paths.local.json"


def _load() -> dict:
    if not _LOCAL.is_file():
        return {}
    try:
        data = json.loads(_LOCAL.read_text(encoding="utf-8"))
    except Exception as e:
        # 别静默吞:最常见是 Windows 路径用了单反斜杠(\U/\n 被当转义)导致 JSON 解析失败(修审计 M8)
        print(f"[musicmaster] 警告:{_LOCAL} 不是合法 JSON,已忽略本文件配置({e})。"
              f"常见原因:Windows 路径用了单反斜杠 —— 请改成正斜杠 / 或双反斜杠 \\\\。",
              file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        # 合法但非对象(字符串/数组/null)会让下游 _CFG.get 抛 AttributeError(修审计 H7)
        print(f"[musicmaster] 警告:{_LOCAL} 顶层必须是 JSON 对象 {{...}},"
              f"实际是 {type(data).__name__},已忽略。", file=sys.stderr)
        return {}
    return data


_CFG = _load()


def resolve(json_key: str, env: str | None = None, default=None):
    """按优先级返回路径字符串:环境变量 > paths.local.json[json_key] > default。
    空串/纯空白一律视为「未设置」并回退(避免空/垃圾路径被当真值返回)。"""
    if env:
        ev = os.environ.get(env)
        if ev and ev.strip():
            return ev
    v = _CFG.get(json_key)
    if isinstance(v, str) and v.strip():
        return v
    return default
