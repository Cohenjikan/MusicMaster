"""MusicMaster — 一体化音乐处理工具(开源整合版)。

整合两条管线:
  • 修音 + 换音色(vocal):跑调清唱 → 在调 + 干净 + 仍是本人音色(DiffPitcher + Seed-VC,GPU)。
  • 分离 + 扒谱 + 互转(separate / transcribe / convert):
      - separate:  混音 → 人声/伴奏 → 去和声 → 降噪(BS-RoFormer / Karaoke RoFormer / UVR,GPU)。
      - transcribe:清唱 → 五线谱 + 简谱(CREPE 多引擎 + L1 质量门 + L3 逐音可信度)。
      - convert:   简谱 ⇄ 五线谱 双向无损互转 / 导入(music21)。

子包:
  musicmaster.core        共享底座:契约 + 谱面渲染(Verovio 五线谱 / jianpu-ly+LilyPond 简谱)
  musicmaster.separate    三段式人声分离(audio-separator,GPU,独立 venv)
  musicmaster.transcribe  扒谱:转录核心 / 多引擎 autopilot / 质量门 / 可信度 / 钢琴
  musicmaster.convert     简谱 ⇄ 五线谱 互转与导入
  musicmaster.vocal       修音(DiffPitcher)+ 换音色(Seed-VC)子进程包装(GPU,独立 venv)

注:重依赖(torch/tensorflow/crepe/audio-separator 等)按需在各子模块内懒加载,
   import musicmaster 本身保持轻量。
"""

__version__ = "0.1.0"
