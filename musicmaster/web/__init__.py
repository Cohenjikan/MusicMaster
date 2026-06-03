# -*- coding: utf-8 -*-
"""MusicMaster Web —— 把设计稿(design/index.html)桥接到真实后端的 FastAPI 层。

四个页签(互译 / 记谱 / 拆声 / 重塑)经 HTTP 接到已验证的核心模块,
长任务(拆声 / 重塑)走「提交 → 轮询 → 下载」的异步任务模型(见 jobs.py)。
后端逻辑完全复用 musicmaster.{convert,transcribe,separate,vocal}(见 runners.py),
本层只做编排与序列化,不改任何已测算法(守「唯一验证配方」红线)。
"""
