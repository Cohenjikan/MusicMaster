"""简谱 ⇄ 五线谱 双向无损互转与导入(数据级,以 music21.Score 为枢轴)。

  parse_jianpu(text)            简谱文本(.jianpu) → music21 Score
  score_to_jianpu(score, key)   music21 Score → 简谱文本
  load_any(path)                统一导入:.jianpu / .musicxml / .mid / .abc → music21 Score

CLI: `python -m musicmaster.convert.convert IN [--to jianpu|musicxml] [--key G] [--render]`
"""
from .convert import parse_jianpu, score_to_jianpu, load_any  # noqa: F401

__all__ = ["parse_jianpu", "score_to_jianpu", "load_any"]
