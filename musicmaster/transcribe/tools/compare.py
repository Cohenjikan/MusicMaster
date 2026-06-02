#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""移调无关的扒谱↔参考相似度评分(第三方AI建议的数据法)。
参考可为:① ref.json(含 rel_semitones 或 degrees);② 另一份 notes.json(--ref-notes)。
指标:
  - 音符数对比(过分割/半音汤指示)
  - 最佳整数移调下的 DTW 对齐:音高匹配率(±0/±1 半音)、平均绝对偏差(半音)
  - Parsons 轮廓(上行/平/下行)匹配率(对八度/微调最稳)
  - 候选调外音占比(时长 & 计数)
综合相似度 = 0.6*pitch_match(±1) + 0.4*contour_match。
用法: PY compare.py --cand notes.json (--ref ref.json | --ref-notes other.json) [--key auto]
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
import numpy as np

PC=["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
KS_MAJ=np.array([6.35,2.23,3.48,2.33,4.38,4.09,2.52,5.19,2.39,3.66,2.29,2.88])
KS_MIN=np.array([6.33,2.68,3.52,5.38,2.60,3.53,2.54,4.75,3.98,2.69,3.34,3.17])
MAJ=[0,2,4,5,7,9,11]; MIN=[0,2,3,5,7,8,10]

def degrees_to_rel(degs):
    out=[]
    for d in degs:
        octv=(d-1)//7; step=(d-1)%7
        out.append(MAJ[step]+12*octv)
    return out

def load_seq_from_notes(p):
    d=json.loads(Path(p).read_text(encoding="utf-8"))
    ns=sorted(d.get("notes",[]),key=lambda x:x["start_s"])
    return [float(n["midi"]) for n in ns], d

def detect_key(midis,durs):
    h=np.zeros(12)
    for m,du in zip(midis,durs): h[int(round(m))%12]+=max(du,1e-3)
    h/=h.sum()+1e-9; best=(-2,None)
    for t in range(12):
        for prof,mode,steps in ((KS_MAJ,"maj",MAJ),(KS_MIN,"min",MIN)):
            r=np.corrcoef(h,np.roll(prof/prof.sum(),t))[0,1]
            if r>best[0]: best=(r,(t,mode,steps))
    return best

def dtw(a,b):
    na,nb=len(a),len(b); INF=1e18
    D=np.full((na+1,nb+1),INF); D[0,0]=0
    for i in range(1,na+1):
        ai=a[i-1]
        for j in range(1,nb+1):
            c=abs(ai-b[j-1])
            m=D[i-1,j];
            if D[i,j-1]<m: m=D[i,j-1]
            if D[i-1,j-1]<m: m=D[i-1,j-1]
            D[i,j]=c+m
    i,j=na,nb; path=[]
    while i>0 and j>0:
        path.append((i-1,j-1))
        diag,up,left=D[i-1,j-1],D[i-1,j],D[i,j-1]
        if diag<=up and diag<=left: i,j=i-1,j-1
        elif up<=left: i-=1
        else: j-=1
    path.reverse()
    return D[na,nb],path

def parsons(seq):
    return [0 if abs(seq[k+1]-seq[k])<0.5 else (1 if seq[k+1]>seq[k] else -1) for k in range(len(seq)-1)]

def main(argv=None):
    ap=argparse.ArgumentParser()
    ap.add_argument("--cand",required=True)
    ap.add_argument("--ref"); ap.add_argument("--ref-notes")
    ap.add_argument("--key",default="auto")
    a=ap.parse_args(argv)
    cand,cdoc=load_seq_from_notes(a.cand)
    cdurs=[n["end_s"]-n["start_s"] for n in sorted(cdoc["notes"],key=lambda x:x["start_s"])]
    if a.ref:
        r=json.loads(Path(a.ref).read_text(encoding="utf-8"))
        rs=r.get("rel_semitones_from_tonic")          # 显式 None 判定(空列表 [] 是 falsy,不能用 or)
        if rs is None: rs=degrees_to_rel(r["degrees"])
        ref=[float(x) for x in rs]
        ref_name=r.get("name","ref")
    else:
        ref,_=load_seq_from_notes(a.ref_notes); ref_name=Path(a.ref_notes).parent.name
    if not cand:
        print("候选为空"); return 0
    if not ref:
        print("参考为空"); return 0
    # 最佳整数移调:把搜索区间居中到两序列中位数之差(参考是相对音级,候选是绝对MIDI)
    coarse=int(round(float(np.median(ref))-float(np.median(cand))))
    best=None
    for t in range(coarse-8,coarse+9):
        cost,path=dtw([c+t for c in cand],ref)
        norm=cost/max(len(path),1)
        if best is None or norm<best[1]:
            best=(t,norm,cost,path)
    t,norm,cost,path=best
    devs=[abs((cand[i]+t)-ref[j]) for i,j in path]
    m0=np.mean([d<0.5 for d in devs])*100
    m1=np.mean([d<1.5 for d in devs])*100
    mad=float(np.mean(devs))
    # contour
    pc_c=parsons([cand[i] for i,_ in path]); pc_r=parsons([ref[j] for _,j in path])
    L=min(len(pc_c),len(pc_r))
    cmatch=(np.mean([pc_c[k]==pc_r[k] for k in range(L)])*100) if L else 0
    # 候选调外音
    if a.key=="auto":
        kr,(kt,kmode,ksteps)=detect_key(cand,cdurs); kname=f"{PC[kt]} {kmode}"
    else:
        parts=a.key.split(":"); kt=PC.index(parts[0])
        is_min=len(parts)>1 and parts[1].lower().startswith("min")
        ksteps=MIN if is_min else MAJ; kname=f"{a.key} (指定)"
    sset={(kt+s)%12 for s in ksteps}
    tot=sum(cdurs)+1e-9
    acc_dur=sum((c%12 not in sset)*du for c,du in zip(cand,cdurs))/tot*100
    acc_cnt=np.mean([c%12 not in sset for c in cand])*100
    sim=0.6*m1+0.4*cmatch
    print(f"== 候选[{Path(a.cand).parent.name}] vs 参考[{ref_name}] ==")
    print(f"  音符数: 候选{len(cand)} / 参考{len(ref)}  (比值{len(cand)/len(ref):.2f}; >1.3 多为半音汤过分割)")
    print(f"  最佳移调 t={t:+d} 半音   DTW均代价={norm:.3f}")
    ac=[int(round(cand[i]+t)) for i,_ in path]; ar=[int(round(ref[j])) for _,j in path]
    print(f"  对齐相对半音 cand: {ac}")
    print(f"  对齐相对半音 ref : {ar}")
    print(f"  音高匹配率: ±1半音内 {m1:5.1f}%   严格 {m0:5.1f}%   平均偏差 {mad:.2f}半音")
    print(f"  轮廓(Parsons)匹配率: {cmatch:5.1f}%")
    print(f"  候选调≈{kname}  调外音: 时长{acc_dur:.1f}% 计数{acc_cnt:.1f}%")
    print(f"  >>> 综合相似度 = {sim:.1f}/100  (0.6*音高±1 + 0.4*轮廓)")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
