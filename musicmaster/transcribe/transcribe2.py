#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""改进版人声扒谱(修"半音汤"):
核心修复 = **音符级中位数赋音高**(而非逐帧四舍五入)+ **调内吸附** + 丢极短毛刺。
- f0:pyin(可约束 fmax 到男声,降八度错);可选置信度门限。
- 分割:unvoiced 断开;voiced 段内偏离"运行中位数">jump 半音也断开(切真实换音,不被颤音切碎)。
- 赋音高:每个音符 = 其帧 f0(MIDI浮点)的中位数;若在 snap_tol 内贴近调内音级则吸附,否则就近取整(保留真半音)。
- 输出 notes.json(契约 target.schema)。可 --offset/--dur 只扒某段。

用法:
  PY=".../.venv/Scripts/python.exe"
  PYTHONUTF8=1 "$PY" transcribe2.py IN.wav --out OUTDIR [--offset 42 --dur 7]
     [--fmin C2 --fmax C5] [--conf 0.0] [--min-ms 90] [--jump 0.7]
     [--snap auto|off|G:maj|D:maj ...] [--snap-tol 0.65] [--bpm 0]
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
import numpy as np
import librosa

PC = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
KS_MAJ = np.array([6.35,2.23,3.48,2.33,4.38,4.09,2.52,5.19,2.39,3.66,2.29,2.88])
KS_MIN = np.array([6.33,2.68,3.52,5.38,2.60,3.53,2.54,4.75,3.98,2.69,3.34,3.17])
MAJ_STEPS = [0,2,4,5,7,9,11]
MIN_STEPS = [0,2,3,5,7,8,10]

def detect_key(note_midis, note_durs):
    hist = np.zeros(12)
    for m,d in zip(note_midis, note_durs):
        hist[int(round(m))%12] += max(d,1e-3)
    h = hist/(hist.sum()+1e-9)
    best=(-2.0,None)
    for tonic in range(12):
        for prof,mode,steps in ((KS_MAJ,"maj",MAJ_STEPS),(KS_MIN,"min",MIN_STEPS)):
            r=np.corrcoef(h, np.roll(prof/prof.sum(), tonic))[0,1]
            if np.isnan(r): r=-2.0
            if r>best[0]: best=(r,(tonic,mode,steps))
    if best[1] is None:  # 全 NaN(理论极端)兜底:C 大调
        best=(0.0,(PC.index("C"),"maj",MAJ_STEPS))
    return best  # (r,(tonic,mode,steps))

def scale_set(tonic, steps):
    return {(tonic+s)%12 for s in steps}

def transcribe(path, *, offset=0.0, dur=None, fmin="C2", fmax="C5",
               conf=0.0, min_ms=90.0, jump=0.7, snap="auto", snap_tol=0.65,
               smooth=False, smooth_max_ms=90.0, simplify=False, f0_method="pyin",
               crepe_conf=0.5, hop_ms=10.0):
    lo=float(librosa.note_to_hz(fmin)); hi=float(librosa.note_to_hz(fmax))
    if f0_method=="crepe":
        import crepe
        y, sr = librosa.load(str(path), sr=16000, mono=True, offset=offset, duration=dur)
        if y.size==0: return {"tempo":120.0,"notes":[]}, {"info":"empty"}
        _t, fz, cf, _ = crepe.predict(np.ascontiguousarray(y,dtype="float32"), 16000,
                                      viterbi=False, step_size=int(round(hop_ms)), verbose=0)
        fz=np.asarray(fz,dtype=float); cf=np.asarray(cf,dtype=float)
        midi_f = librosa.hz_to_midi(np.where(fz<=0,1.0,fz))
        voiced = (cf>=crepe_conf) & (fz>=lo) & (fz<=hi)
        hop_s = hop_ms/1000.0
    else:
        y, sr = librosa.load(str(path), sr=None, mono=True, offset=offset, duration=dur)
        y = np.ascontiguousarray(y, dtype=np.float64)
        if y.size == 0: return {"tempo":120.0,"notes":[]}, {"info":"empty"}
        hop = int(round(sr*hop_ms/1000.0))
        f0, vflag, vprob = librosa.pyin(y, fmin=lo, fmax=hi, sr=sr,
                                        frame_length=2048, hop_length=hop)
        midi_f = librosa.hz_to_midi(np.where(np.isnan(f0), 1.0, f0))
        voiced = np.nan_to_num(vflag, nan=0.0).astype(bool) & ~np.isnan(f0)
        if conf>0:
            voiced &= (np.nan_to_num(vprob,nan=0.0) >= conf)
        hop_s=hop/sr
    n=len(midi_f)
    min_frames=max(1,int(round(min_ms/1000.0/hop_s)))
    # ---- 分割 + 音符级中位数 ----
    raw=[]  # (i_start, i_end_excl, median_float)
    i=0
    while i<n:
        if not voiced[i]: i+=1; continue
        j=i; vals=[midi_f[i]]; med=midi_f[i]
        while j+1<n and voiced[j+1] and abs(midi_f[j+1]-med)<=jump:
            vals.append(midi_f[j+1]); med=float(np.median(vals)); j+=1
        if (j-i+1)>=min_frames:
            raw.append((i, j+1, float(np.median(vals))))
        i=j+1
    # ---- 调检测 ----
    durs=[(b-a)*hop_s for a,b,_ in raw]; meds=[m for *_,m in raw]
    key_info=None
    snap_set=None
    if snap!="off" and raw:
        if snap=="auto":
            r,(tonic,mode,steps)=detect_key(meds,durs)
            key_info=(PC[tonic],mode,r)
            snap_set=(tonic, set(scale_set(tonic,steps)))
        else:
            # 健壮解析:支持降号(Bb/Eb..)、小写、min/minor/m;解析失败回退C大调不崩。
            parts=snap.split(":"); tname=parts[0].strip(); mode=(parts[1].strip().lower() if len(parts)>1 else "maj")
            FLAT={"DB":"C#","EB":"D#","FB":"E","GB":"F#","AB":"G#","BB":"A#","CB":"B"}
            tn=(tname[0].upper()+tname[1:]) if tname else "C"
            tn=FLAT.get(tn.upper(),tn)
            try: tonic=PC.index(tn)
            except ValueError: tonic=PC.index("C")
            is_min = mode.startswith("min") or mode=="m"
            steps=MIN_STEPS if is_min else MAJ_STEPS
            key_info=(PC[tonic],"min" if is_min else "maj",None)
            snap_set=(tonic, set(scale_set(tonic,steps)))
    # ---- 赋音高(吸附 or 取整) ----
    notes=[]; n_snapped=0; n_accidental=0
    for a,b,med in raw:
        if snap_set:
            base=int(round(med))
            cands=[m for m in range(base-2,base+3) if m%12 in snap_set[1]]
            nearest=min(cands,key=lambda m:abs(m-med)) if cands else base
            if abs(nearest-med)<=snap_tol:
                pitch=nearest
                if base!=nearest: n_snapped+=1
            else:
                pitch=base;
                if base%12 not in snap_set[1]: n_accidental+=1
        else:
            pitch=int(round(med))
        # 时间相对于本段起点(offset 只用于定位读音频,不写进时间标签;
        # 否则切片扒谱会在谱面前面留出 = offset 的大段空小节)。
        notes.append({"midi":int(pitch),
                      "start_s":round(a*hop_s,4),
                      "end_s":round(b*hop_s,4)})
    # ---- 合并相邻同音(小间隙) ----
    def remerge(ns):
        out=[]
        for nt in ns:
            if out and nt["midi"]==out[-1]["midi"] and nt["start_s"]-out[-1]["end_s"]<=0.06:
                out[-1]["end_s"]=nt["end_s"]
            else: out.append(dict(nt))
        return out
    merged=remerge(notes)
    # ---- 孤立短毛刺平滑(X Y X -> X X X):只动"短 + 两侧同音 + 自己不同"的音,
    #      专杀颤音/滑音抖出的相邻半音单音,不碰真实旋律进行 ----
    n_smoothed=0
    if smooth:
        changed=True
        while changed:
            changed=False
            for k in range(1,len(merged)-1):
                du=merged[k]["end_s"]-merged[k]["start_s"]
                ln=merged[k-1]["end_s"]-merged[k-1]["start_s"]
                rn=merged[k+1]["end_s"]-merged[k+1]["start_s"]
                # 默认(忠实):中间音须"短 且 远短于两侧"(<0.5)才判毛刺,避免误伤等时值真实短经过音。
                # simplify(简谱lead-sheet风):去掉相对约束,激进合并 melisma 抖动以贴近出版简谱(会吞掉部分真实短音)。
                if (du<smooth_max_ms/1000.0 and (simplify or du < 0.5*min(ln,rn))
                        and merged[k-1]["midi"]==merged[k+1]["midi"]
                        and merged[k]["midi"]!=merged[k-1]["midi"]):
                    merged[k]["midi"]=merged[k-1]["midi"]; n_smoothed+=1; changed=True
            merged=remerge(merged)
    # ---- tempo ----
    try:
        import importlib
        tempo_fn=importlib.import_module("librosa.feature.rhythm").tempo
        bpm=float(np.atleast_1d(tempo_fn(y=y,sr=sr))[0])
    except Exception:
        bpm=120.0
    target={"tempo":round(bpm,3) if bpm>0 else 120.0,"notes":merged}
    info={"raw_segments":len(raw),"final_notes":len(merged),"snapped":n_snapped,
          "accidentals_kept":n_accidental,"key":key_info,"sr":sr,
          "voiced_frac":round(float(voiced.mean()),3)}
    return target, info

def main(argv=None):
    ap=argparse.ArgumentParser()
    ap.add_argument("input"); ap.add_argument("--out",required=True)
    ap.add_argument("--offset",type=float,default=0.0); ap.add_argument("--dur",type=float,default=None)
    ap.add_argument("--fmin",default="C2"); ap.add_argument("--fmax",default="C5")
    ap.add_argument("--conf",type=float,default=0.0); ap.add_argument("--min-ms",type=float,default=90.0)
    ap.add_argument("--jump",type=float,default=0.7)
    ap.add_argument("--snap",default="auto"); ap.add_argument("--snap-tol",type=float,default=0.65)
    ap.add_argument("--smooth",action="store_true"); ap.add_argument("--smooth-max-ms",type=float,default=90.0)
    ap.add_argument("--simplify",action="store_true",help="lead-sheet风:激进合并melisma抖动贴近出版简谱")
    ap.add_argument("--f0",dest="f0_method",default="pyin",choices=["pyin","crepe"])
    ap.add_argument("--crepe-conf",type=float,default=0.5)
    ap.add_argument("--bpm",type=float,default=0.0)
    a=ap.parse_args(argv)
    target,info=transcribe(a.input,offset=a.offset,dur=a.dur,fmin=a.fmin,fmax=a.fmax,
                           conf=a.conf,min_ms=a.min_ms,jump=a.jump,snap=a.snap,snap_tol=a.snap_tol,
                           smooth=a.smooth,smooth_max_ms=a.smooth_max_ms,simplify=a.simplify,
                           f0_method=a.f0_method,crepe_conf=a.crepe_conf)
    if a.bpm>0: target["tempo"]=a.bpm
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    (out/"notes.json").write_text(json.dumps(target,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"OUT={out/'notes.json'}")
    print(f"INFO {json.dumps(info,ensure_ascii=False)}")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
