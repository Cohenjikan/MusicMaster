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
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_LOCAL = _REPO / "paths.local.json"


def _load() -> dict:
    if _LOCAL.is_file():
        try:
            return json.loads(_LOCAL.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


_CFG = _load()


def resolve(json_key: str, env: str | None = None, default=None):
    """按优先级返回路径字符串:环境变量 > paths.local.json[json_key] > default。"""
    if env and os.environ.get(env):
        return os.environ[env]
    v = _CFG.get(json_key)
    if v:
        return v
    return default
