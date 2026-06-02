# -*- coding: utf-8 -*-
"""MusicMaster 端到端冒烟测试(CPU,无需权重)。

覆盖:契约导入 / 简谱⇄五线谱往返 / 合成音频扒谱 / 五线谱渲染。
GPU 部分(分离/修音换音色)需独立 venv 与权重,不在此 CI 范围。
"""
import numpy as np
import pytest
import soundfile as sf
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples"


def test_contracts_import():
    from musicmaster.core import contracts
    assert hasattr(contracts, "Target")
    assert hasattr(contracts, "Note")
    assert hasattr(contracts, "F0")


def test_convert_roundtrip_lossless():
    """简谱 → music21 Score → 简谱:音级序列往返一致。"""
    from musicmaster.convert import convert as cv

    score = cv.load_any(EXAMPLES / "twinkle.jianpu")
    back = cv.score_to_jianpu(score, key_name="C")
    seq = " ".join(t for t in back.replace("|", " ").split() if t and not t.startswith(("1=", "4/", "tempo")))
    # 小星星开头 1 1 5 5 6 6 5
    assert seq.split()[:7] == ["1", "1", "5", "5", "6", "6", "5"]


def test_transcribe_synthetic_scale(tmp_path):
    """合成 C 大调音阶 → pyin 扒谱 → 精确还原音高。"""
    import librosa
    from musicmaster.transcribe import transcribe2

    sr = 16000
    notes = [60, 62, 64, 65, 67]
    y = np.concatenate([
        0.3 * np.sin(2 * np.pi * librosa.midi_to_hz(n) * np.arange(int(0.5 * sr)) / sr)
        for n in notes
    ]).astype("float32")
    wav = tmp_path / "scale.wav"
    sf.write(str(wav), y, sr)

    target, info = transcribe2.transcribe(str(wav), f0_method="pyin", fmax="C6")
    midis = [n["midi"] for n in target["notes"]]
    assert midis[:5] == notes


def test_render_staff_svg(tmp_path):
    """MusicXML → 五线谱 SVG(Verovio)。verovio 缺失则跳过。"""
    from musicmaster.core.render import render as render_fn, is_available

    if not is_available(staff=True):
        pytest.skip("verovio 不可用")
    from musicmaster.convert import convert as cv

    score = cv.load_any(EXAMPLES / "twinkle.jianpu")
    mxl = tmp_path / "t.musicxml"
    score.write("musicxml", fp=str(mxl))
    out = render_fn(str(mxl), str(tmp_path), formats=("staff",))
    assert out["staff"].is_file()
    assert out["staff"].stat().st_size > 1000


def test_transcribe_synthetic_scale_crepe(tmp_path):
    """合成 C 大调音阶 → 默认引擎 CREPE 扒谱 → 还原音高(覆盖默认引擎安装链/predict 路径)。"""
    import librosa
    from musicmaster.transcribe import transcribe2

    sr = 16000
    notes = [60, 62, 64, 65, 67]
    y = np.concatenate([
        0.3 * np.sin(2 * np.pi * librosa.midi_to_hz(n) * np.arange(int(0.5 * sr)) / sr)
        for n in notes
    ]).astype("float32")
    wav = tmp_path / "scale.wav"
    sf.write(str(wav), y, sr)
    target, info = transcribe2.transcribe(str(wav), f0_method="crepe", fmax="C6")
    midis = [n["midi"] for n in target["notes"]]
    assert midis[:5] == notes


def test_vocal_redlines_locked():
    """锁定人声「验证配方」红线:默认参数不被悄悄改(CI 无 GPU,只查签名默认值)。"""
    import importlib
    import inspect
    # vocal/__init__ 把 correct/voice 绑成包属性函数,取模块必须 import_module(避开同名陷阱)
    pipeline = importlib.import_module("musicmaster.vocal.pipeline")
    correct_mod = importlib.import_module("musicmaster.vocal.correct")
    voice_mod = importlib.import_module("musicmaster.vocal.voice")

    p = inspect.signature(pipeline.two_stage).parameters
    assert p["shift"].default == -12, "八度锁 shift 必须 -12"
    assert p["correct_steps"].default == 150
    assert p["voice_steps"].default == 50
    assert p["voice_cfg"].default == 0.7
    assert inspect.signature(correct_mod.correct).parameters["shift"].default == -12

    vp = inspect.signature(voice_mod.voice).parameters
    assert vp["auto_f0_adjust"].default is False, "不自动移调(否则身份偏)"
    assert vp["semi_tone_shift"].default == 0
    assert vp["f0_condition"].default is True
    assert vp["inference_cfg_rate"].default == 0.7
    assert inspect.signature(voice_mod.resample).parameters["sr"].default == 44100, "Seed-VC f0 模型要 44.1k"
