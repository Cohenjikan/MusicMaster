#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分部·简谱 ⇄ 五线谱 双向转换(数据级,非仅渲染)。

五线谱侧 = **MusicXML**(各记谱软件 MuseScore/Finale/Sibelius 通用导出格式,首选标准),
          也支持 MIDI / ABC(经 music21 解析)。
简谱侧   = 下方定义的**纯文本简谱迷你格式**(.jianpu),带调号/拍号/节奏型/升降号。
枢轴     = music21.stream.Score(复用引擎的渲染:staff.svg / jianpu.pdf)。

================ 简谱文本格式(.jianpu)================
表头(音符前,每行一条;可省走默认):
    1=G            主音(调);默认 C。   (1=G 时 1234567 = G4 A4 B4 C5 D5 E5 F#5)
    4/4            拍号;默认 4/4。
    tempo=88       BPM;默认 90。
正文(空白分隔的 token;`|` 为小节线,可选,仅作可读/校验):
    音符 token 文法:  [#|b]? 数字 [' | ,]*  [_]*  [.]?
      数字:0=休止;1-7=音级
      升降:前缀 # 升、b 降(如 #4 b7)
      八度:后缀 ' 升八度(可叠)、, 降八度(可叠)
      时值:基准 = 四分音符(1 拍)
            _ = 减半(八分),__ = 十六分;  . = 附点(×1.5)
            单独的 `-` token = 把前一音延长 1 拍(连音/长音);如 `5 - -` = 3 拍
    例:`1=G 4/4 tempo=88 | 3 2 4 3 1 5 7 1' 7 1 1 | 5_ 5_ 6 5 - |`

用法:
  PY jianpu_convert.py IN [--out OUT] [--to jianpu|musicxml|auto] [--key G] [--render]
  - IN 为 .jianpu/.txt → 默认转 MusicXML(+ --render 出 staff.svg)
  - IN 为 .musicxml/.xml/.mxl/.mid/.abc → 默认转 .jianpu 文本(+ --render 出 jianpu.pdf)
统一导入接口:load_any(path) → music21 Score(新格式按 _LOADERS 注册即可扩展)。
"""
from __future__ import annotations
import argparse, importlib, re, sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PC = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
DEG2REL = {1:0,2:2,3:4,4:5,5:7,6:9,7:11}
REL2DEG = {0:"1",1:"#1",2:"2",3:"#2",4:"3",5:"4",6:"#4",7:"5",8:"#5",9:"6",10:"b7",11:"7"}


# ----------------------------- 简谱文本 → Score ----------------------------- #
def parse_jianpu(text: str):
    from music21 import duration, key as m21key, meter, note as m21note, stream, tempo as m21tempo
    tonic_name, ts_str, bpm = "C", "4/4", 90.0
    body_tokens = []
    for raw in text.splitlines():
        line = raw.split("#编码")[0] if "#编码" in raw else raw
        s = line.strip()
        if not s or s.startswith("//"):
            continue
        m = re.fullmatch(r"1\s*=\s*([A-Ga-g][#b]?)", s)
        if m:
            g = m.group(1); tonic_name = g[0].upper() + g[1:]; continue
        if re.fullmatch(r"\d+\s*/\s*\d+", s):
            ts_str = s.replace(" ", ""); continue
        mt = re.fullmatch(r"tempo\s*=\s*(\d+(?:\.\d+)?)", s, re.I)
        if mt:
            bpm = float(mt.group(1)); continue
        # 正文行:收集 token(去掉小节线)
        body_tokens += [t for t in s.split() if t != "|"]

    tonic_pc = _tonic_pc(tonic_name)
    part = stream.Part()
    part.append(m21tempo.MetronomeMark(number=int(round(bpm))))
    try:
        part.append(m21key.Key(tonic_name))
    except Exception:
        part.append(m21key.KeySignature(0))
    part.append(meter.TimeSignature(ts_str))

    last_note = None
    for tok in body_tokens:
        if tok == "-":
            if last_note is not None:
                last_note.duration.quarterLength += 1.0
            continue
        parsed = _parse_note_token(tok)
        if parsed is None:
            continue
        is_rest, midi, ql = parsed(tonic_pc)
        if is_rest:
            r = m21note.Rest(); r.duration = duration.Duration(quarterLength=ql)
            part.append(r); last_note = r
        else:
            n = m21note.Note(midi); n.duration = duration.Duration(quarterLength=ql)
            part.append(n); last_note = n

    score = stream.Score()
    score.append(part.makeNotation(inPlace=False))
    return score


def _norm_pc(name):
    name = name.strip()
    return {"DB":"C#","EB":"D#","GB":"F#","AB":"G#","BB":"A#"}.get(name.upper(), name[0].upper()+name[1:])


def _tonic_pc(name) -> int:
    """调名 → pitch class(0-11)。用 music21 解析,天然支持全部 enharmonic 拼写
    (Cb/Fb/E#/B#/Db/Bb…);本格式用 'b' 表示降号(music21 用 '-')。
    空串/非法调名 → 回退 C(0)并告警,绝不抛裸 ValueError/IndexError(修审计 H1/H2/M1)。"""
    s = (name or "").strip()
    if s:
        letter, acc = s[0].upper(), s[1:].replace("b", "-")  # b=降 → music21 的 -
        try:
            from music21 import pitch as m21pitch
            return m21pitch.Pitch(letter + acc).pitchClass
        except Exception:
            pass
    print(f"[convert] 警告:无法识别调名 {name!r},按 C 大调处理"
          f"(合法调名如 C/G/D/A/E/B/F#/Bb/Eb/Cb 等)。", file=sys.stderr)
    return 0


def _parse_note_token(tok):
    m = re.fullmatch(r"([#b]?)([0-7])([',]*)(_*)(\.?)", tok)
    if not m:
        return None
    acc, digit, octs, unders, dot = m.groups()
    d = int(digit)
    n_up = octs.count("'"); n_dn = octs.count(",")
    ql = 1.0 / (2 ** len(unders))
    if dot:
        ql *= 1.5

    def build(tonic_pc):
        if d == 0:
            return True, None, ql
        rel = DEG2REL[d] + (1 if acc == "#" else -1 if acc == "b" else 0)
        midi = tonic_pc + 60 + rel + 12 * (n_up - n_dn)
        return False, midi, ql
    return build


# ----------------------------- Score → 简谱文本 ----------------------------- #
def score_to_jianpu(score, key_name=None) -> str:
    from music21 import key as m21key, meter, tempo as m21tempo
    flat = score.flatten()
    # 调
    tonic_name = key_name
    if tonic_name is None:
        k = flat.getElementsByClass(m21key.Key)
        if k:
            tonic_name = k[0].tonic.name.replace("-", "b")
        else:
            ks = flat.getElementsByClass(m21key.KeySignature)
            tonic_name = (ks[0].asKey("major").tonic.name.replace("-", "b")) if ks else "C"
    tonic_pc = _tonic_pc(tonic_name)
    # 拍号/速度
    tss = flat.getElementsByClass(meter.TimeSignature)
    ts = tss[0] if tss else None
    bar_ql = (ts.numerator * 4.0 / ts.denominator) if ts else 4.0
    mm = flat.getElementsByClass(m21tempo.MetronomeMark)
    bpm = int(round(mm[0].number)) if mm and mm[0].number else 90

    header = f"1={tonic_name}\n{ts.ratioString if ts else '4/4'}\ntempo={bpm}\n"
    out, cur_bar = [], 0
    # 跨小节连音(makeNotation 产生的 tie)先 stripTies 合并,避免被还原成重复发声(伪重发音头)
    try:
        noteflat = score.stripTies(inPlace=False).flatten()
    except Exception:
        noteflat = flat
    for e in noteflat.notesAndRests:
        off = float(e.offset)
        bar_idx = int(off / bar_ql + 1e-6)  # 先真除再补容差(原 // 后加 eps 无效)
        while bar_idx > cur_bar:
            out.append("|"); cur_bar += 1
        out.append(_note_to_token(e, tonic_pc))
    out.append("|")
    return header + " ".join(out) + "\n"


def _note_to_token(e, tonic_pc):
    suf, dashes = _dur_tokens(float(e.duration.quarterLength))
    if e.isRest:
        head = "0" + suf
    else:
        # 兼容 Chord(无 .pitch):简谱是单声部,取最高音(旋律线);Note 则取其唯一音
        midi = int(max(p.midi for p in e.pitches))
        rel = (midi - tonic_pc) % 12
        deg = REL2DEG[rel]
        oct4 = tonic_pc + 60 + rel
        marks = int(round((midi - oct4) / 12))
        omk = "'" * marks if marks > 0 else "," * (-marks)
        # 升降号在最前,八度点在数字后、时值前
        if deg[0] in "#b":
            head = deg[0] + deg[1] + omk + suf
        else:
            head = deg + omk + suf
    return head + (" " + " ".join(["-"] * dashes) if dashes else "")


def _dur_tokens(ql):
    """quarterLength → (音符后缀, 延长dash数)。支持任意长音(>4拍 多 dash)与半拍余数(2.5/3.5→附点+dash),
    不再封顶 4.0 丢拍。<1拍走下划线/附点网格。"""
    if ql < 0.95:  # 不足一拍:十六分/八分/附点八分
        sub = [(0.25, "__"), (0.5, "_"), (0.75, "_.")]
        return min(sub, key=lambda g: abs(g[0] - ql))[1], 0
    beats = round(ql * 2) / 2.0          # 吸附到 0.5 拍网格
    whole = int(beats); half = beats - whole
    if half >= 0.5 and whole >= 1:       # x.5 拍 = 附点音头(1.5拍) + (whole-1) dash
        return ".", whole - 1
    return "", max(0, whole - 1)          # 整数拍 = 音头(1拍) + (whole-1) dash


# ----------------------------- 统一导入接口 ----------------------------- #
_SCORE_EXTS = (".musicxml", ".xml", ".mxl", ".mid", ".midi", ".abc")


def load_any(path):
    """path → music21 Score。.jianpu/.jp/.txt 走简谱解析;五线谱家族(MusicXML/MIDI/ABC)交给 music21。

    未知扩展名 / 文件不存在 / 空或畸形文件 → 抛带本工具上下文的清晰错误(而非裸的 music21
    底层异常,修审计 M2)。GUI 与 CLI 上层再把它转成友好提示。"""
    p = Path(path); ext = p.suffix.lower()
    if not p.is_file():
        raise FileNotFoundError(f"找不到文件:{p}")
    if ext in (".jianpu", ".jp", ".txt"):
        return parse_jianpu(p.read_text(encoding="utf-8"))
    if ext not in _SCORE_EXTS:
        raise ValueError(
            f"不支持的扩展名 {ext!r}。简谱用 .jianpu/.jp/.txt;"
            f"五线谱用 {' / '.join(_SCORE_EXTS)}。")
    from music21 import converter
    try:
        return converter.parse(str(p))
    except Exception as e:
        raise ValueError(f"无法解析 {p.name}:文件可能为空或格式损坏({type(e).__name__}: {e})") from e


_LOADERS = {".jianpu": "parse_jianpu", ".jp": "parse_jianpu", ".txt": "parse_jianpu"}  # 预留:未来格式按此注册


# ----------------------------- CLI ----------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description="简谱 ⇄ 五线谱(MusicXML)双向转换")
    ap.add_argument("input")
    ap.add_argument("--out")
    ap.add_argument("--to", default="auto", choices=["auto", "jianpu", "musicxml"])
    ap.add_argument("--key", default=None, help="生成简谱时的主音(如 G);默认读乐谱调号")
    ap.add_argument("--render", action="store_true", help="另出图:五线谱→staff.svg / 简谱→jianpu.pdf")
    a = ap.parse_args(argv)

    inp = Path(a.input); ext = inp.suffix.lower()
    direction = a.to
    if direction == "auto":
        direction = "musicxml" if ext in (".jianpu", ".jp", ".txt") else "jianpu"

    if direction == "musicxml":
        score = load_any(inp)
        out = Path(a.out) if a.out else inp.with_suffix(".musicxml")
        score.write("musicxml", fp=str(out))
        print(f"OK 简谱→五线谱 MusicXML: {out}")
        if a.render:
            _render(out, "staff")
    else:
        score = load_any(inp)
        text = score_to_jianpu(score, key_name=a.key)
        out = Path(a.out) if a.out else inp.with_suffix(".jianpu")
        out.write_text(text, encoding="utf-8")
        print(f"OK 五线谱→简谱文本: {out}")
        print(text)
        if a.render:
            # 需先有 musicxml 给 jianpu-ly
            mxl = out.with_suffix(".musicxml"); score.write("musicxml", fp=str(mxl))
            _render(mxl, "jianpu")
    return 0


def _render(musicxml_path, fmt):
    eng_r = importlib.import_module("musicmaster.core.render")
    try:
        res = eng_r.render(str(musicxml_path), str(Path(musicxml_path).parent),
                           formats=(fmt,), jianpu_format="pdf")
        print("RENDER", {k: str(v) for k, v in res.items()})
    except Exception as e:
        # 简谱图需 LilyPond;未装时优雅降级(与 autopilot/GUI 行为一致),不让 CLI 崩(修审计 M16)
        print(f"[convert] 渲染跳过({type(e).__name__}: {e})。"
              f"五线谱无需外部程序;简谱图需装 LilyPond 并设 LILYPOND_EXE——未装时已产出中间 .ly,可后续手动渲染。")


if __name__ == "__main__":
    raise SystemExit(main())
