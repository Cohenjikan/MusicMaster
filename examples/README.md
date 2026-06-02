# 示例

公有领域 / 合成的小样例,用于快速试跑(不含任何版权音频)。

| 文件 | 说明 |
|---|---|
| `twinkle.jianpu` | 《小星星》简谱文本(公有领域)。试互转:`python -m musicmaster.convert.convert examples/twinkle.jianpu --to musicxml --render` |

试扒谱(无需任何素材,合成一段音阶):

```bash
python - <<'PY'
import numpy as np, soundfile as sf, librosa
sr=16000; notes=[60,62,64,65,67,69,71,72]
y=np.concatenate([0.3*np.sin(2*np.pi*librosa.midi_to_hz(n)*np.arange(int(0.5*sr))/sr) for n in notes]).astype('float32')
sf.write('scale.wav', y, sr); print('wrote scale.wav')
PY
python -m musicmaster.transcribe.autopilot scale.wav --out scale_out --engine crepe --key C
```

> 修音 / 换音色需要你自己的清唱素材(原始清唱 + 去和声参考 + 你自己的音色锚),不附带示例。
