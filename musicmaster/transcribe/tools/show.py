#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 notes.json 以【指定调】的简谱级数打印出来 + 统计(调外音占比、音符数、中位时值)。
用法: PY show.py notes.json [--key G]"""
import argparse, json, sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
PC=["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
# G大调级数映射:pc -> 简谱符号(相对主音)
def deg_str(midi, tonic_pc):
    rel=(midi - tonic_pc) % 12
    table={0:"1",2:"2",4:"3",5:"4",7:"5",9:"6",11:"7",
           1:"#1",3:"#2",6:"#4",8:"#5",10:"b7"}  # 6=#4(F#是7), 5=4(C), 注意F自然=b7
    # 修正:G大调里 5=F? 不,pc5=F=本位7的降(b7);pc6=F#=7。重写:
    table={0:"1",2:"2",4:"3",5:"4",6:"#4",7:"5",9:"6",11:"7",
           1:"#1",3:"#2",8:"#5",10:"b7",5:"4"}
    # octave dot:相对主音的八度
    oct_off=(midi - tonic_pc)//12 - 4  # 以 tonic 第4八度区为中
    base=table.get(rel,"?")
    if oct_off>0: base+="^"*oct_off
    elif oct_off<0: base+="_"*(-oct_off)
    return base, rel

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("notes"); ap.add_argument("--key",default="G")
    a=ap.parse_args()
    tonic=PC.index(a.key)
    d=json.loads(Path(a.notes).read_text(encoding="utf-8"))
    ns=sorted(d.get("notes",[]),key=lambda x:x["start_s"])
    if not ns: print("空"); return
    scale={0,2,4,5,7,9,11}
    degs=[]; acc_cnt=0; acc_dur=0.0; tot=0.0
    for n in ns:
        s,rel=deg_str(int(n["midi"]),tonic)
        degs.append(s)
        du=n["end_s"]-n["start_s"]; tot+=du
        if rel not in scale: acc_cnt+=1; acc_dur+=du
    durs=sorted(n["end_s"]-n["start_s"] for n in ns)
    print(f"调=1={a.key}  音符数={len(ns)}  tempo={d.get('tempo')}  中位时值={durs[len(durs)//2]*1000:.0f}ms")
    print(f"调外音: 计数 {acc_cnt}/{len(ns)} ({acc_cnt/len(ns)*100:.1f}%)  时长 {acc_dur/tot*100:.1f}%")
    print("简谱级数序列:")
    print("  "+" ".join(degs))

if __name__=="__main__": main()
