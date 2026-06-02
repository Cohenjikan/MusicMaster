"""三段式人声分离(audio-separator,GPU)。

  1 分离   BS-RoFormer       混音 → 人声 + 伴奏
  2 去和声 Karaoke gabox_v2  人声 → 纯主唱(去 backing vocals)
  3 降噪   UVR DeEcho-DeReverb 纯主唱 → 干净主唱

入口: `python -m musicmaster.separate.pipeline 输入.wav [--stages 1,2,3]`
或    `from musicmaster.separate.pipeline import main`(在含 audio-separator 的 GPU venv 中)。
"""
