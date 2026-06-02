"""扒谱:音频 → 音符 → 五线谱 + 简谱,带去专家化外壳。

子模块(按需 import,各自懒加载 crepe/tensorflow/torch 等重依赖):
  transcribe2     转录核心:CREPE/pyin f0 → 音符级中位数赋音高 + 孤立毛刺平滑 → target dict
  entry           生产入口:清唱 wav(可切段)→ notes/MIDI/MusicXML(带正确调号)+ 五线谱 + 简谱
  autopilot       一键多引擎外壳:L1 质量门 → 选引擎(crepe/basic-pitch/bytedance)→ 转录 → L3 可信度 → 报告
  quality         L1 入口质量门:从频谱测真实有效带宽,烂输入门口拦截 + 给修法
  confidence      L3 逐音可信度:成串调外 × 双算法分歧 × 跟踪自信 × L1 折扣 → 存疑段标注
  piano_bytedance ByteDance 高分辨率钢琴复音转录(MAESTRO 权重,仅干净 44.1k 真钢琴)
  tools           normalize_notes(前奏/间奏规整)/ compare(移调无关相似度评测)/ show(简谱级数查看)
"""
