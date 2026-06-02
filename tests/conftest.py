import sys
from pathlib import Path

# 让 `import musicmaster` 在未 pip install 时也能跑(仓库根加入 path)。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
