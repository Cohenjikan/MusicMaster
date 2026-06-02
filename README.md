# 🎵 MusicMaster

> 一体化音乐处理工具:**人声分离** · **音频扒谱(五线谱/简谱)** · **简谱⇄五线谱互转** · **人声修音换音色**。
> 纯 Python + Gradio 单页 GUI,整合一线开源模型,开箱即用。

MusicMaster 把两套管线合到一个项目里:

| 能力 | 做什么 | 算力 |
|---|---|---|
| 🔊 **分离** | 混音 → 人声/伴奏 → 去和声纯主唱 → 降噪干净主唱 | GPU |
| 🎤 **扒谱** | 清唱/单旋律音频 → 五线谱 + 简谱 + **逐音可信度**(零调参,坏输入门口拦,不确定处自举手) | CPU |
| 🎼 **互转** | 简谱 `.jianpu` ⇄ 五线谱 `MusicXML/MIDI/ABC`,双向**无损**;支持导入 | CPU |
| 🎚️ **修音换音色** | 跑调清唱 → **在调 + 干净 + 仍是你本人音色**(两段式:修音准 → SVC 重生成) | GPU |

> 完整的「每一层用了哪些开源项目/模型」流程图见 **[docs/架构流程图.md](docs/架构流程图.md)**。

---

## ✨ 快速开始(CPU 部分:扒谱 + 互转 + GUI)

扒谱与互转是纯 CPU,开箱即用;分离与修音换音色需 GPU(见下文)。

> ⚠️ **不要直接双击 `app.py`** —— 它要在装好依赖的虚拟环境里跑,直接双击会用系统 Python 报 `No module named 'gradio'`。
> 正确姿势:先建环境(一次),之后**双击 `start_gui.bat`**(Windows)即可。

```bash
git clone <your-repo-url> musicmaster && cd musicmaster

# 1) 一键建主环境(CPU):建 .venv + 装 TensorFlow + crepe(--no-deps) + 其余依赖 + 本包
python scripts/setup_core.py

# 2) 启动 GUI
start_gui.bat            # Windows:双击它,或命令行运行
# ./start_gui.sh         # macOS/Linux
```

启动后浏览器开 `http://127.0.0.1:7860`,四个标签页对应四大能力;**互转 / 扒谱(crepe)**两页此刻即可用。

<details><summary>手动建环境(等价于 setup_core.py)</summary>

```bash
python -m venv .venv
.venv\Scripts\activate                      # Windows;macOS/Linux: source .venv/bin/activate
pip install tensorflow>=2.15,<2.16
pip install --no-deps crepe                 # 避免 crepe 降级 TensorFlow
pip install -r requirements-core.txt
pip install -e .
python app.py
```
</details>

> 引擎说明:GUI 扒谱默认 **crepe**(人声/单旋律最佳,纯 CPU)。`basic-pitch`(复音)需 **TensorFlow 2.15** 干净环境;`bytedance`(钢琴)需 GPU —— 用 `setup_core.py` 建的干净环境即含 TF 2.15,这两个引擎可用。

> **简谱出图(jianpu.pdf)** 需额外装 [LilyPond](https://lilypond.org)(GPL,仅作独立子进程):
> 装好后设环境变量 `LILYPOND_EXE` 指向 `lilypond.exe` 全路径(**不要**把它的 bin 放进全局 PATH,其 DLL 会和 librosa/TF 冲突)。
> 没装 LilyPond 也能用——五线谱(Verovio)正常出图,简谱只产出中间 `.ly`。

---

## ⚡ 命令行(不开 GUI 也能用)

```bash
# 扒谱(一键:质量门 → 引擎 → 定调 → 渲染 → 可信度报告)
python -m musicmaster.transcribe.autopilot 清唱.wav --out 输出目录 --engine crepe --key C

# 互转
python -m musicmaster.convert.convert 某.jianpu --to musicxml --render
python -m musicmaster.convert.convert 某.musicxml --to jianpu --key G --render

# 渲染(MusicXML → 五线谱 + 简谱)
python -m musicmaster.core.cli render 某.musicxml -o 输出目录

# 分离(需 GPU venv,见下)
python -m musicmaster.separate.pipeline 混音.wav --stages 1,2,3

# 修音换音色(需 GPU venv,见下)
python -m musicmaster.vocal.pipeline --raw 清唱.wav --ref 去和声参考.wav --self 你的清唱.wav --out 输出目录
```

---

## 🎮 GPU 功能(分离 / 修音换音色)

这两类用一线 GPU 模型,各有独立 venv(依赖互相冲突,**不要混装**)。推荐用安装脚本:

```bash
# 分离环境(.venv-sep:audio-separator + CUDA torch)
python scripts/setup_sep.py

# 修音换音色环境(.venv-neural:DiffPitcher;.venv-svc:Seed-VC)+ 拉取模型仓库与权重
python scripts/setup_vocal.py
```

脚本会在 `vendor/` 下拉取 DiffPitcher / Seed-VC 源码、建对应 venv、下载权重。完成后,
GUI / CLI 通过环境变量找到它们(脚本会打印,可写进你的 shell 配置):

| 环境变量 | 指向 |
|---|---|
| `MUSICMASTER_SEP_PYTHON` | 分离 venv 的 python(.venv-sep) |
| `MUSICMASTER_DIFFPITCHER_DIR` / `MUSICMASTER_VOCAL_PYTHON` | DiffPitcher 目录 / 修音 venv 的 python |
| `MUSICMASTER_SEEDVC_DIR` / `MUSICMASTER_SVC_PYTHON` | Seed-VC 目录 / 换音色 venv 的 python |
| `MUSICMASTER_SEP_MODELS` | (可选)分离模型缓存目录 |

**修音换音色的输入**(三个):① 你的原始清唱 ② 目标旋律的**去和声**干净参考(可用「分离」页从原曲分出)③ 你自己的一段干净清唱(作音色锚)。
显存建议 ≥ 8GB;30s 片段单次约 1–2 分钟。

> 体验提示:目标参考**必须去和声**(否则高音发哑);换音色目标设成**你自己**(不是明星)→ 成品是「还是你,只是变好听了」。

---

## 📁 项目结构

```
musicmaster/
├─ app.py                       # Gradio 单页 GUI(主入口)
├─ musicmaster/
│  ├─ core/                     # 共享底座:契约 + 谱面渲染(Verovio/jianpu-ly+LilyPond)
│  ├─ separate/                 # 三段式人声分离(audio-separator,GPU)
│  ├─ transcribe/              # 扒谱:转录核心/多引擎 autopilot/L1 质量门/L3 可信度/钢琴
│  ├─ convert/                 # 简谱 ⇄ 五线谱 互转与导入
│  └─ vocal/                   # 修音(DiffPitcher)+ 换音色(Seed-VC)子进程包装
│      └─ diffpitcher_scripts/  # 验证过的修音配方(run_qt4 / run_full)
├─ scripts/                     # setup_core / setup_sep / setup_vocal
├─ examples/                    # 示例(twinkle.jianpu 等,公有领域)
├─ tests/                       # pytest(契约/渲染/扒谱/互转)
├─ docs/                        # 架构流程图.md + 合并开发日志.md
├─ requirements-core.txt        # CPU 依赖(GUI/扒谱/互转/渲染)
├─ requirements-sep.txt         # GPU 分离依赖
├─ requirements-vocal.txt       # GPU 修音换音色依赖
├─ pyproject.toml · LICENSE · NOTICE
```

---

## 🙏 致谢与许可

MusicMaster 本体以 **Apache-2.0** 开源。它**站在巨人的肩膀上**,集成了众多优秀开源项目与模型 ——
分离:[audio-separator](https://github.com/nomadkaraoke/python-audio-separator) · BS-RoFormer · Mel-Band RoFormer Karaoke · UVR · [Demucs](https://github.com/facebookresearch/demucs);
扒谱:[CREPE](https://github.com/marl/crepe) · [basic-pitch](https://github.com/spotify/basic-pitch) · [ByteDance 钢琴转录](https://github.com/qiuqiangkong/piano_transcription_inference) · [music21](https://github.com/cuthbertLab/music21) · [librosa](https://github.com/librosa/librosa);
渲染:[Verovio](https://github.com/rism-digital/verovio) · [jianpu-ly](https://github.com/ssb22/jianpu-ly) · [LilyPond](https://lilypond.org);
修音换音色:[DiffPitcher](https://github.com/haidog-yaqub/DiffPitcher) · [BigVGAN](https://github.com/NVIDIA/BigVGAN) · [Seed-VC](https://github.com/Plachtaa/seed-vc) · [pyworld](https://github.com/JeremyCCHsu/Python-Wrapper-for-World-Vocoder)。

完整组件清单、许可证与角色见 **[NOTICE](NOTICE)** 与 **[docs/架构流程图.md](docs/架构流程图.md)**。

⚠️ **商用提示**:部分**模型权重**为非商用/研究许可(Karaoke RoFormer、Demucs(MUSDB18)、ByteDance(MAESTRO)等)。
个人 / 研究自由使用;若要闭源商用,请把这几项替换为可商用权重或单独取得授权(代码本体不受影响)。

---

## ✅ 已验证(本机)

- 互转往返**无损**(简谱→MusicXML→简谱 逐字一致)。
- 扒谱端到端(合成音阶逐音精确;真实《晴天》副歌 vs 权威谱 87.8/100,来自原项目)。
- 五线谱(Verovio)渲染正常。
- 修音换音色两段式 GPU 全链路跑通(DiffPitcher → Seed-VC,RTX 4060 8GB)。
- GUI 四标签页正常加载。

环境:Windows / Python 3.11 / RTX 4060 Laptop 8GB(CUDA 12.4)。
