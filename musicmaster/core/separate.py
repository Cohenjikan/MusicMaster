"""M2 人声分离(开源核心的对外函数)。

§3 契约:`wav → {vocal.wav, accomp.wav}`(纯文件产物,无 JSON schema)。

实现:封装 **Demucs**(MIT,§5 BOM 许可)的 `htdemucs` 4-stem 模型,按
`--two-stems=vocals` 语义产出两路:
    - vocal  = vocals 分轨
    - accomp = 其余分轨(drums+bass+other)之和(即 demucs 的 "no_vocals")

合规红线(§5):
  - 仅用 pyworld 做重合成(本模块不涉及重合成,故无关);
  - 不引入 psola/parselmouth(GPL);
  - Demucs 为 MIT,权重(htdemucs)随 demucs 发布,非 MAESTRO-CC-NC,商用可用。

工程约束:
  - CPU-only 可跑(torch CPU 版即可);默认 device="cpu"。
  - 写盘用 soundfile,**绕开** torchaudio.save(torchaudio 2.9 会拉 torchcodec 依赖)。
  - 可用性自检 `is_available()`:demucs 可导入 **且** htdemucs 权重已缓存/可获取;
    否则上层(测试)应 graceful skip,绝不 hang。

用法:
    from musicmaster.core import separate, is_separation_available
    if is_separation_available():
        out = separate("mix.wav")          # {"vocal": Path(...), "accomp": Path(...)}
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Dict, Optional, Union

PathLike = Union[str, Path]

# Demucs 4-stem 默认模型(htdemucs,MIT,权重随 demucs 发布)。
DEFAULT_MODEL = "htdemucs"
# 模型固有采样率(htdemucs = 44100)。分离在此采样率进行。
_MODEL_SR_FALLBACK = 44100


# --------------------------------------------------------------------------- #
# 可用性自检(import 守卫 + 权重可获取)。绝不在探测时强制联网下载。
# --------------------------------------------------------------------------- #
def _demucs_importable() -> bool:
    import importlib.util

    return (
        importlib.util.find_spec("demucs") is not None
        and importlib.util.find_spec("torch") is not None
    )


@functools.lru_cache(maxsize=4)
def _model_weights_cached(model_name: str = DEFAULT_MODEL) -> bool:
    """权重是否已在本地缓存(torch hub checkpoints)→ 可离线分离。

    只查缓存,**不触发下载**;用于 is_available() 的快速、无副作用探测。
    """
    try:
        from pathlib import Path as _P

        import torch  # noqa: F401

        # demucs 通过 torch hub 缓存 .th 权重;htdemucs 是 bag of models。
        hub = _P(torch.hub.get_dir()) / "checkpoints"
        if not hub.exists():
            return False
        # htdemucs 由若干 .th 组成;只要目录非空且有 .th 即视为可离线加载。
        return any(hub.glob("*.th"))
    except Exception:
        return False


def is_available(model_name: str = DEFAULT_MODEL, *, allow_download: bool = False) -> bool:
    """M2 是否可用。

    Args:
        model_name:     demucs 模型名(默认 htdemucs)。
        allow_download: True 时允许"权重可下载"也算可用(联网环境);
                        默认 False = 仅当权重已缓存才算可用(离线/测试友好)。

    Returns:
        True 表示 `separate()` 可以无阻塞地跑完。
    """
    if not _demucs_importable():
        return False
    if _model_weights_cached(model_name):
        return True
    return bool(allow_download)


# --------------------------------------------------------------------------- #
# 模型加载(惰性 + 缓存)。
# --------------------------------------------------------------------------- #
@functools.lru_cache(maxsize=2)
def _load_model(model_name: str = DEFAULT_MODEL):
    """加载并缓存 demucs 模型到 CPU eval 模式。

    Raises:
        RuntimeError: demucs 不可导入,或权重无法获取(离线且未缓存)。
    """
    if not _demucs_importable():
        raise RuntimeError(
            "demucs 不可用:请 `pip install demucs`(MIT)。M2 人声分离需要它。"
        )
    try:
        from demucs.pretrained import get_model
    except Exception as e:  # pragma: no cover - 防御性
        raise RuntimeError(f"无法导入 demucs.pretrained.get_model: {e}") from e

    try:
        model = get_model(model_name)  # 已缓存则离线;否则尝试下载
    except Exception as e:
        raise RuntimeError(
            f"无法获取 demucs 模型 '{model_name}'(权重未缓存且下载失败?): {e}"
        ) from e

    model.cpu().eval()
    return model


# --------------------------------------------------------------------------- #
# 核心分离。
# --------------------------------------------------------------------------- #
def separate(
    input_wav: PathLike,
    out_dir: Optional[PathLike] = None,
    *,
    model_name: str = DEFAULT_MODEL,
    device: str = "cpu",
    overlap: float = 0.25,
    mono: bool = False,
    max_seconds: Optional[float] = None,
    vocal_name: str = "vocal.wav",
    accomp_name: str = "accomp.wav",
) -> Dict[str, Path]:
    """人声分离:混音 wav → {vocal.wav, accomp.wav}(§3 M2 契约)。

    Args:
        input_wav:   输入混音 wav(任意采样率/声道;内部重采样到模型 sr)。
        out_dir:     输出目录;None → 输入文件同目录下的 `<stem>_stems/`。
        model_name:  demucs 模型(默认 htdemucs)。
        device:      "cpu"(默认,CPU-only 可跑)或 "cuda"。
        overlap:     分块重叠(0..1);越大越准越慢。CPU 默认 0.25。
        mono:        True → 输出单声道(取均值);False → 保留模型声道(立体声)。
        max_seconds: 仅处理前 N 秒(运行时上界,测试/CPU 友好);None=全长。
        vocal_name / accomp_name: 输出文件名(契约约定 vocal.wav / accomp.wav)。

    Returns:
        {"vocal": Path, "accomp": Path} —— 两个写出的 wav 路径。

    Raises:
        FileNotFoundError: 输入不存在。
        RuntimeError:      demucs/权重不可用(应先 is_available() 判定并 skip)。
    """
    import numpy as np
    import librosa
    import soundfile as sf
    import torch

    src = Path(input_wav)
    if not src.exists():
        raise FileNotFoundError(f"输入音频不存在: {src}")

    if out_dir is None:
        out_dir = src.parent / f"{src.stem}_stems"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    model = _load_model(model_name)
    sr_model = int(getattr(model, "samplerate", _MODEL_SR_FALLBACK))
    sources_order = list(model.sources)  # e.g. ['drums','bass','other','vocals']
    if "vocals" not in sources_order:
        raise RuntimeError(
            f"模型 {model_name} 的分轨 {sources_order} 不含 'vocals',无法做人声分离。"
        )

    # 载入为立体声(channels, samples),按模型采样率重采样。
    y, _ = librosa.load(str(src), sr=sr_model, mono=False)
    y = np.asarray(y, dtype=np.float32)
    if y.ndim == 1:
        y = np.stack([y, y])  # mono → fake stereo,满足模型 2ch 输入
    if max_seconds is not None and max_seconds > 0:
        n_max = int(round(max_seconds * sr_model))
        y = y[:, :n_max]

    if y.shape[-1] == 0:
        raise RuntimeError(f"输入音频为空: {src}")

    wav = torch.from_numpy(y).float()  # (channels, samples)
    # demucs 推荐的逐样本标准化:按混音均值/方差归一,推理后还原。
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std()
    wav_norm = (wav - mean) / (std + 1e-8)

    from demucs.apply import apply_model

    with torch.no_grad():
        # apply_model: (batch, channels, samples) → (batch, stems, channels, samples)
        est = apply_model(
            model,
            wav_norm[None],
            device=device,
            split=True,      # 分块,降低 CPU 内存峰值
            overlap=overlap,
            progress=False,
        )[0]
    est = est * std + mean  # 还原幅度

    vidx = sources_order.index("vocals")
    vocal = est[vidx]                                   # (channels, samples)
    # two-stems=vocals 语义:accomp = 其余所有分轨之和(= no_vocals)。
    accomp = sum(est[i] for i in range(est.shape[0]) if i != vidx)

    vocal_np = vocal.cpu().numpy()
    accomp_np = accomp.cpu().numpy()
    if mono:
        vocal_np = vocal_np.mean(0)
        accomp_np = accomp_np.mean(0)
    else:
        # soundfile 期望 (samples, channels)
        vocal_np = vocal_np.T
        accomp_np = accomp_np.T

    vocal_path = out_dir / vocal_name
    accomp_path = out_dir / accomp_name
    sf.write(str(vocal_path), vocal_np.astype(np.float32), sr_model, subtype="PCM_16")
    sf.write(str(accomp_path), accomp_np.astype(np.float32), sr_model, subtype="PCM_16")

    return {"vocal": vocal_path, "accomp": accomp_path}


# --------------------------------------------------------------------------- #
# 质量自检(轻量,无外部依赖)。给出"分离是否合理"的量化信号。
# --------------------------------------------------------------------------- #
def separation_quality(
    input_wav: PathLike,
    vocal_wav: PathLike,
    accomp_wav: PathLike,
) -> Dict[str, float]:
    """对分离结果做轻量自检(不评感知质量,只查工程合理性)。

    返回指标:
        mix_rms / vocal_rms / accomp_rms:  各路 RMS 能量。
        recon_err_db:  (vocal+accomp) 对原混音的重构误差(dB);越低越像 v+a≈mix。
        n_samples:     对齐后的样本数。
    """
    import numpy as np
    import librosa

    def _load_mono(p: PathLike):
        y, sr = librosa.load(str(p), sr=None, mono=True)
        return np.asarray(y, dtype=np.float64), sr

    mix, sr_mix = _load_mono(input_wav)
    voc, sr_v = _load_mono(vocal_wav)
    acc, sr_a = _load_mono(accomp_wav)

    # 若混音采样率与分轨不同(分轨在模型 sr),重采样混音到分轨 sr 再比。
    if sr_mix != sr_v:
        mix = librosa.resample(mix, orig_sr=sr_mix, target_sr=sr_v)
    n = min(len(mix), len(voc), len(acc))
    mix, voc, acc = mix[:n], voc[:n], acc[:n]

    def _rms(x):
        return float(np.sqrt(np.mean(x ** 2) + 1e-12))

    recon = voc + acc
    err = recon - mix
    recon_err_db = float(20.0 * np.log10((_rms(err) + 1e-12) / (_rms(mix) + 1e-12)))

    return {
        "mix_rms": _rms(mix),
        "vocal_rms": _rms(voc),
        "accomp_rms": _rms(acc),
        "recon_err_db": recon_err_db,
        "n_samples": float(n),
    }
