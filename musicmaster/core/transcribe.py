"""M4b 扒谱/转录(开源核心的对外函数)。

§3 契约:`wav → { out.mid, out.musicxml, notes.json(同 target.json) }`。
`notes.json` / 返回的 Target 严格符合 `shared/schemas/target.schema.json`:
    { "tempo": <BPM>, "notes": [ {"midi": 0..127, "start_s": >=0, "end_s": >start_s}, ... ] }

两条路径(对应 §3 模块表 M4b):
  - **人声主旋律(默认,instrument=False)**:确定性 DSP 流水线
        M3 pyin 测 f0  →  音符分割(把 f0 轮廓量化成离散音符)  →
        pretty_midi 写 .mid  →  music21 写 .musicxml。
    全程**纯 CPU、无权重下载、无外部进程**;依赖 librosa(ISC)/pretty_midi(MIT)/music21(BSD-3),
    均为 §5 BOM 许可的宽松许可证。
  - **通用乐器(可选,instrument=True)**:basic-pitch(Apache-2.0,含权重)。
    basic-pitch 体量大(TensorFlow),按调度规则 6 **惰性导入 + 可用性自检**:
    不可用时 `is_transcription_available(instrument=True)` 返回 False、`transcribe(..., instrument=True)`
    抛 RuntimeError,测试 graceful skip,**绝不 hang / 绝不强制联网**。

合规红线(§5):
  - **人声主旋律别用 basic-pitch**(噪声下崩到 ~12%);故默认走自实现的 pyin+分割路径。
  - MAESTRO-CC-NC 钢琴权重**不进任何商用路径**(basic-pitch 自带权重为 Apache,允许)。
  - 不引入 psola/parselmouth(GPL);本模块不做重合成,天然无关。
  - LilyPond/ffmpeg 不在此 import/链接(MusicXML 由 music21 纯进程内生成,无需外部二进制)。

用法:
    from musicmaster.core import transcribe, transcribe_to_files
    target = transcribe("vocal.wav")                       # -> Target dict(同 target.json)
    out = transcribe_to_files("vocal.wav", "out_dir/")     # -> {"midi","musicxml","notes"}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Union

from .contracts import Note, Target

PathLike = Union[str, Path]

# --------------------------------------------------------------------------- #
# 人声路径 DSP 常量(与 spike/correct.py 的 f0 设定保持一致)。
# --------------------------------------------------------------------------- #
HOP_MS = 10.0  # f0 帧步长(毫秒)
DEFAULT_TEMPO = 120.0  # 无可靠节拍估计时的默认 BPM(契约要求 tempo>0)

# pyin 搜索音域(覆盖人声;C2~C7)。
_FMIN_NOTE = "C2"  # ~65 Hz
_FMAX_NOTE = "C7"  # ~2093 Hz

# 音符分割参数。
_MIN_NOTE_MS = 80.0  # 短于此的片段视为毛刺,丢弃/并入相邻
_MEDIAN_FILTER_FRAMES = 5  # 对量化后的 MIDI 序列做中值滤波,去抖
_SPLIT_SEMITONE = 0.5  # 同一音符内允许的音高漂移(半音);超过则切新音符


# --------------------------------------------------------------------------- #
# 可用性自检(遵循调度规则 6:不可用 → 上层 graceful skip,绝不 hang)。
# --------------------------------------------------------------------------- #
def _vocal_deps_importable() -> bool:
    import importlib.util as u

    return all(
        u.find_spec(m) is not None
        for m in ("librosa", "numpy", "pretty_midi", "music21", "soundfile")
    )


def _basic_pitch_importable() -> bool:
    import importlib.util as u

    return u.find_spec("basic_pitch") is not None


def is_available(*, instrument: bool = False) -> bool:
    """转录是否可用。

    Args:
        instrument: False=人声路径(librosa/pretty_midi/music21);
                    True=通用乐器路径(basic-pitch,Apache,含权重)。

    Returns:
        True 表示对应 `transcribe(..., instrument=...)` 可无阻塞跑完。
    """
    if instrument:
        # 通用路径需 basic-pitch;MusicXML 仍由 music21 落盘。
        return _basic_pitch_importable() and _vocal_deps_importable()
    return _vocal_deps_importable()


# --------------------------------------------------------------------------- #
# M3:f0 检测(人声路径)。pyin = librosa(ISC),零下载、零合规风险。
# 不下载 RMVPE 权重(合规 pending,调度规则 4)。
# --------------------------------------------------------------------------- #
def _detect_f0(y, sr: int):
    """返回 (f0_hz[np.ndarray], voiced[bool np.ndarray]),帧步长 = HOP_MS。"""
    import librosa
    import numpy as np

    hop = int(round(sr * HOP_MS / 1000.0))
    fmin = float(librosa.note_to_hz(_FMIN_NOTE))
    fmax = float(librosa.note_to_hz(_FMAX_NOTE))
    f0, vflag, _ = librosa.pyin(
        y, fmin=fmin, fmax=fmax, sr=sr, frame_length=2048, hop_length=hop
    )
    f0 = np.nan_to_num(f0, nan=0.0).astype(float)
    voiced = np.nan_to_num(vflag, nan=0.0).astype(bool) & (f0 > 0)
    return f0, voiced


# --------------------------------------------------------------------------- #
# 音符分割:把逐帧 f0 轮廓 → 离散音符列表(确定性)。
# 思路:f0→MIDI(浮点)→四舍五入到整数半音→中值滤波去抖→
#       按「连续同一 MIDI 的 voiced 段」聚合成音符→丢弃过短毛刺。
# --------------------------------------------------------------------------- #
def _segment_notes(f0, voiced, hop_s: float) -> List[Note]:
    import librosa
    import numpy as np

    n = len(f0)
    if n == 0 or not bool(np.any(voiced)):
        return []

    # f0(Hz) → MIDI(浮点);unvoiced 标记为 NaN。
    midi_f = np.full(n, np.nan, dtype=float)
    vf = voiced & (f0 > 0)
    midi_f[vf] = librosa.hz_to_midi(f0[vf])

    # 量化到整数半音(就近)。
    midi_q = np.full(n, -1, dtype=int)  # -1 = 静音/unvoiced
    midi_q[vf] = np.rint(midi_f[vf]).astype(int)

    # 对 voiced 段的整数 MIDI 做中值滤波去抖(只在 voiced 帧上,避免跨休止串味)。
    midi_q = _median_filter_voiced(midi_q, _MEDIAN_FILTER_FRAMES)

    # 聚合连续同一 MIDI 的 voiced 段为音符;
    # 额外按 f0 漂移切分:同段内浮点 MIDI 偏离起点 > _SPLIT_SEMITONE 也断开。
    notes: List[Note] = []
    i = 0
    min_frames = max(1, int(round(_MIN_NOTE_MS / 1000.0 / hop_s)))
    while i < n:
        if midi_q[i] < 0:
            i += 1
            continue
        cur = midi_q[i]
        j = i + 1
        anchor = midi_f[i] if not np.isnan(midi_f[i]) else float(cur)
        while j < n and midi_q[j] == cur:
            if not np.isnan(midi_f[j]) and abs(midi_f[j] - anchor) > _SPLIT_SEMITONE:
                break
            j += 1
        dur_frames = j - i
        if dur_frames >= min_frames:
            start_s = round(i * hop_s, 4)
            end_s = round(j * hop_s, 4)
            if end_s > start_s and 0 <= cur <= 127:
                notes.append({"midi": int(cur), "start_s": float(start_s), "end_s": float(end_s)})
        i = j

    return _merge_adjacent_same_pitch(notes)


def _median_filter_voiced(midi_q, win: int):
    """对 voiced(>=0)帧的整数 MIDI 序列做中值滤波;静音帧(-1)原样保留。

    只在连续 voiced 段内部滤波,避免把 -1 卷进中值导致音高被拉低。
    """
    import numpy as np
    from scipy.ndimage import median_filter

    if win <= 1:
        return midi_q
    out = midi_q.copy()
    n = len(midi_q)
    i = 0
    w = win | 1  # 取奇数
    while i < n:
        if midi_q[i] < 0:
            i += 1
            continue
        j = i
        while j < n and midi_q[j] >= 0:
            j += 1
        seg = midi_q[i:j].astype(float)
        if len(seg) >= 3:
            out[i:j] = np.rint(median_filter(seg, size=min(w, len(seg) | 1))).astype(int)
        i = j
    return out


def _merge_adjacent_same_pitch(notes: List[Note], gap_s: float = 0.03) -> List[Note]:
    """把音高相同、且间隔极小(<=gap_s)的相邻音符合并,消除分割碎片。"""
    if not notes:
        return notes
    merged: List[Note] = [dict(notes[0])]  # type: ignore[list-item]
    for nt in notes[1:]:
        prev = merged[-1]
        if nt["midi"] == prev["midi"] and (nt["start_s"] - prev["end_s"]) <= gap_s:
            prev["end_s"] = nt["end_s"]
        else:
            merged.append(dict(nt))  # type: ignore[arg-type]
    return merged


# --------------------------------------------------------------------------- #
# 通用乐器路径:basic-pitch(Apache,含权重)。惰性导入 + 友好报错。
# --------------------------------------------------------------------------- #
def _transcribe_instrument(audio_wav: Path) -> Target:
    if not _basic_pitch_importable():
        raise RuntimeError(
            "通用乐器转录需要 basic-pitch(Apache-2.0):请 `pip install basic-pitch`。"
            "人声主旋律请用 instrument=False(默认,自实现 pyin+音符分割路径)。"
        )
    # basic-pitch 自带 ICASSP-2022 模型权重(Apache),非 MAESTRO-CC-NC,商用可用。
    from basic_pitch import ICASSP_2022_MODEL_PATH
    from basic_pitch.inference import predict

    model_output, midi_data, note_events = predict(str(audio_wav), ICASSP_2022_MODEL_PATH)
    # basic-pitch 返回 pretty_midi.PrettyMIDI;复用统一的 PrettyMIDI→Target。
    return _pretty_midi_to_target(midi_data)


# --------------------------------------------------------------------------- #
# 公共转换:PrettyMIDI → Target(契约)。
# --------------------------------------------------------------------------- #
def _pretty_midi_to_target(pm) -> Target:
    notes: List[Note] = []
    for inst in pm.instruments:
        if getattr(inst, "is_drum", False):
            continue
        for nt in inst.notes:
            midi = int(max(0, min(127, nt.pitch)))
            start_s = float(max(0.0, nt.start))
            end_s = float(nt.end)
            if end_s > start_s:
                notes.append({"midi": midi, "start_s": round(start_s, 4), "end_s": round(end_s, 4)})
    notes.sort(key=lambda d: (d["start_s"], d["midi"]))
    tempo = _estimate_tempo_from_pm(pm)
    return {"tempo": tempo, "notes": notes}


def _estimate_tempo_from_pm(pm) -> float:
    try:
        _t, tempi = pm.get_tempo_changes()
        if len(tempi):
            t = float(tempi[0])
            if t > 0:
                return t
    except Exception:
        pass
    try:
        est = float(pm.estimate_tempo())
        if est > 0:
            return est
    except Exception:
        pass
    return DEFAULT_TEMPO


# --------------------------------------------------------------------------- #
# 落盘:Target → MIDI(pretty_midi)、MusicXML(music21)、notes.json。
# --------------------------------------------------------------------------- #
def target_to_pretty_midi(target: Target):
    """Target(契约)→ pretty_midi.PrettyMIDI(单一旋律音轨)。"""
    import pretty_midi

    tempo = float(target.get("tempo") or DEFAULT_TEMPO)
    pm = pretty_midi.PrettyMIDI(initial_tempo=tempo)
    inst = pretty_midi.Instrument(program=0)  # Acoustic Grand,主旋律占位
    for nt in target["notes"]:
        inst.notes.append(
            pretty_midi.Note(
                velocity=90,
                pitch=int(nt["midi"]),
                start=float(nt["start_s"]),
                end=float(nt["end_s"]),
            )
        )
    pm.instruments.append(inst)
    return pm


# MusicXML 时值量化栅格(四分音符 QL=1.0)。量化到 1/8(0.5)而非更细的 1/16:
# 实测 16th 栅格下转录碎音符会"跨小节线"导致 jianpu-ly(简谱)barcheck 失败;
# 1/8 既能让简谱/五线谱稳定生成,又足够表达清唱主旋律。
# 这是记谱产物的标准做法,**不改动** notes.json/MIDI 的精确秒级时间。
_MXL_QUANTUM_QL = 0.5  # 1/8 音符


def _snap_ql(ql: float, quantum: float = _MXL_QUANTUM_QL) -> float:
    """把 quarterLength 吸附到记谱栅格(最小一个 quantum,保证可记谱且非零)。"""
    steps = max(1, int(round(ql / quantum)))
    return steps * quantum


def _detect_key_name(notes: List[Note]):
    """时长加权 PC 直方图 + Krumhansl-Schmuckler → (tonicName, 'major'/'minor') 或 None。

    用于给 MusicXML 写正确调号:简谱按 1=主音、五线谱带正确升降号渲染,
    从根上消除"默认 1=C 把全部自然音显示成临时升降号"造成的伪「半音汤」。
    注:旋律(单声部)上 K-S 有大调/关系小调歧义,故 target_to_musicxml 支持 key_name 覆盖,
    已知调时(如《晴天》=G)务必显式传入。
    """
    if not notes:
        return None
    try:
        import numpy as np
    except Exception:
        return None
    names = ["C", "C#", "D", "E-", "E", "F", "F#", "G", "G#", "A", "B-", "B"]
    ks_maj = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
    ks_min = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
    hist = [0.0] * 12
    for n in notes:
        hist[int(n["midi"]) % 12] += max(1e-3, float(n["end_s"]) - float(n["start_s"]))
    h = np.array(hist)
    h = h / (h.sum() + 1e-9)
    best = (-2.0, None)
    for tonic in range(12):
        for prof, mode in ((ks_maj, "major"), (ks_min, "minor")):
            p = np.roll(np.array(prof) / sum(prof), tonic)
            r = float(np.corrcoef(h, p)[0, 1])
            if r > best[0]:
                best = (r, (names[tonic], mode))
    return best[1]


def target_to_musicxml(
    target: Target, out_path: PathLike, *, key_name: Optional[str] = None
) -> Path:
    """Target(契约)→ MusicXML 文件(music21,BSD-3;纯进程内,无外部二进制)。

    时值量化到 1/8 栅格 + 固定 4/4,按栅格顺序排放(极短音补成 1 格=minfill,不丢音符),
    再 makeNotation 排小节。既避免 music21 不可记谱时值报错,又能稳定通过 jianpu-ly 的
    barcheck(简谱)。秒级精确时间仍以 notes.json / out.mid 为准。

    key_name: 显式调名(如 "G"=G大调、"e"=e小调);None 时按音符分布自动检测。
              **正确调号 = 简谱 1=主音、五线谱正确升降号**,根治默认 1=C 的伪半音汤。
    """
    from music21 import duration, key as m21key, meter, note as m21note, stream, tempo as m21tempo

    bpm = float(target.get("tempo") or DEFAULT_TEMPO)
    spq = 60.0 / bpm
    grid = _MXL_QUANTUM_QL

    part = stream.Part()
    part.append(m21tempo.MetronomeMark(number=int(round(bpm))))  # 整数 BPM(jianpu-ly 不接受小数)
    # 调号(关键):显式优先,否则自动检测;写进 MusicXML 后 jianpu-ly / Verovio 据此渲染。
    try:
        if key_name:
            part.append(m21key.Key(key_name))
        else:
            det = _detect_key_name(target["notes"])
            if det:
                part.append(m21key.Key(det[0], det[1]))
    except Exception:
        pass  # 调号只是记谱辅助;失败不阻断出谱
    part.append(meter.TimeSignature("4/4"))

    prev = 0.0  # 已排到的栅格位置(quarterLength)
    for nt in target["notes"]:
        start_ql = round((float(nt["start_s"]) / spq) / grid) * grid
        units = round(((float(nt["end_s"]) - float(nt["start_s"])) / spq) / grid)
        if units < 1:
            units = 1  # 极短音补成 1 格(minfill;保留全部音符)
        dur_ql = units * grid
        if start_ql > prev + 1e-6:  # 间隙补休止(对齐栅格)
            r = m21note.Rest()
            r.duration = duration.Duration(quarterLength=start_ql - prev)
            part.append(r)
            prev = start_ql
        m = m21note.Note(int(nt["midi"]))
        m.duration = duration.Duration(quarterLength=dur_ql)
        part.append(m)
        prev += dur_ql

    # makeNotation:排小节/补休止/连音梁,产出规整可导出的记谱。
    score = stream.Score()
    score.append(part.makeNotation(inPlace=False))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = score.write("musicxml", fp=str(out_path))
    return Path(written) if written else out_path


# --------------------------------------------------------------------------- #
# 对外主函数。
# --------------------------------------------------------------------------- #
def transcribe(audio_wav: PathLike, *, instrument: bool = False) -> Target:
    """音频 → 目标旋律音符(target.schema.json)。

    Args:
        audio_wav:  输入音频 wav(任意采样率/声道;内部转单声道)。
        instrument: False=人声主旋律路径(pyin+音符分割,默认);
                    True=通用乐器路径(basic-pitch,Apache)。

    Returns:
        Target dict —— 严格符合 target.schema.json(== notes.json)。

    Raises:
        FileNotFoundError: 输入不存在。
        RuntimeError:      所需依赖不可用(应先 is_available() 判定并 skip)。
    """
    src = Path(audio_wav)
    if not src.exists():
        raise FileNotFoundError(f"输入音频不存在: {src}")

    if instrument:
        return _transcribe_instrument(src)

    # ── 人声主旋律路径(确定性) ──
    if not _vocal_deps_importable():
        raise RuntimeError(
            "人声转录需要 librosa/pretty_midi/music21/soundfile(均为宽松许可)。"
        )
    import librosa
    import numpy as np

    y, sr = librosa.load(str(src), sr=None, mono=True)
    y = np.ascontiguousarray(y, dtype=np.float64)
    if y.size == 0:
        return {"tempo": DEFAULT_TEMPO, "notes": []}

    f0, voiced = _detect_f0(y, sr)
    hop_s = HOP_MS / 1000.0
    notes = _segment_notes(f0, voiced, hop_s)

    # 节拍估计(librosa,失败回退默认 BPM;契约要求 tempo>0)。
    tempo = _estimate_tempo_vocal(y, sr)
    return {"tempo": tempo, "notes": notes}


def _estimate_tempo_vocal(y, sr: int) -> float:
    try:
        import importlib

        import librosa
        import numpy as np

        # librosa 0.10+ 把 tempo 移到 feature.rhythm(直接 import 子模块避开
        # librosa.beat.tempo 的 FutureWarning 别名);旧版回退 beat.tempo。
        try:
            tempo_fn = importlib.import_module("librosa.feature.rhythm").tempo
        except Exception:
            tempo_fn = librosa.beat.tempo
        bpm = tempo_fn(y=y, sr=sr)
        val = float(np.atleast_1d(bpm)[0])
        if val > 0:
            return round(val, 3)
    except Exception:
        pass
    return DEFAULT_TEMPO


def transcribe_to_files(
    audio_wav: PathLike,
    out_dir: Optional[PathLike] = None,
    *,
    instrument: bool = False,
    midi_name: str = "out.mid",
    musicxml_name: str = "out.musicxml",
    notes_name: str = "notes.json",
) -> Dict[str, Path]:
    """§3 M4b 完整契约:wav → { out.mid, out.musicxml, notes.json(同 target.json) }。

    Args:
        audio_wav:   输入音频 wav。
        out_dir:     输出目录;None → 输入文件同目录下的 `<stem>_transcribe/`。
        instrument:  False=人声路径(默认);True=通用乐器(basic-pitch)。
        *_name:      三个产物的文件名(契约约定 out.mid / out.musicxml / notes.json)。

    Returns:
        {"midi": Path, "musicxml": Path, "notes": Path} —— 三个写出的产物路径。
        其中 notes.json 的内容**完全等于** transcribe() 返回的 Target。
    """
    src = Path(audio_wav)
    if not src.exists():
        raise FileNotFoundError(f"输入音频不存在: {src}")

    if out_dir is None:
        out_dir = src.parent / f"{src.stem}_transcribe"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    target = transcribe(src, instrument=instrument)

    # notes.json(== target.json)
    notes_path = out_dir / notes_name
    notes_path.write_text(
        json.dumps(target, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # out.mid(pretty_midi)
    midi_path = out_dir / midi_name
    target_to_pretty_midi(target).write(str(midi_path))

    # out.musicxml(music21,纯进程内)
    musicxml_path = out_dir / musicxml_name
    written = target_to_musicxml(target, musicxml_path)

    return {"midi": midi_path, "musicxml": Path(written), "notes": notes_path}
