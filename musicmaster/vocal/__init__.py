"""修音 + 换音色(GPU,子进程包装,保留验证过的配方)。

两段式:
  correct  修音准:跑调清唱 + 去和声参考 → 在调(DiffPitcher run_qt4,torchcrepe 目标 f0 + BigVGAN)
  voice    换音色:修音输出 + 用户自己清唱(身份锚)→ 在调 + 干净 + 仍是本人(Seed-VC)

两者均以子进程调用各自 GPU venv 中验证过的脚本(run_qt4.py / seed-vc inference.py),
不改其内部逻辑(守「唯一验证配方,勿重建」红线)。配置见 musicmaster/vocal/config 与环境变量。
"""
from .correct import correct  # noqa: F401
from .voice import voice  # noqa: F401

__all__ = ["correct", "voice"]
