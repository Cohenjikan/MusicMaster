# -*- coding: utf-8 -*-
"""进程内异步任务管理器(无外部依赖)。

为什么需要它:拆声(分钟级)和重塑(整首高精度可能 20+ 分钟)远超一次 HTTP 请求的合理时长,
所以采用「提交 → 拿 job_id → 轮询状态 → 完成后取结果/下载」模型。互译/记谱虽快,也走同一套
统一路径(首次轮询即 done),前端逻辑因此对四个部门完全一致。

设计要点:
- 线程池执行;runner 函数在 worker 线程里跑(同步调用核心模块或子进程,均可阻塞线程)。
- runner 拿到 Job 句柄,可随时更新 stage/progress(粗粒度,GPU 子进程内部不透明)。
- runner 正常返回 dict = 结果(含自定义 ok 标志,区分「成功」与「环境未就绪等软失败」);
  抛异常 = 真崩溃,捕获为 status=error。两者前端都渲染成一条消息。
- 线程安全:所有对 _jobs / Job 字段的读写都过锁。
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass
class Job:
    id: str
    kind: str                              # convert / transcribe / separate / vocal
    job_dir: str
    status: str = "queued"                 # queued / running / done / error
    progress: float = 0.0                  # 0..1,尽力而为(很多阶段不透明)
    stage: str = "排队中"                   # 人类可读的当前阶段
    result: Optional[dict] = None          # 完成时的结构化结果
    error: Optional[str] = None            # 出错时的人话消息
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set_stage(self, stage: str, progress: Optional[float] = None) -> None:
        with self._lock:
            self.stage = stage
            if progress is not None:
                self.progress = max(0.0, min(1.0, float(progress)))
            self.updated_at = time.time()

    def to_public(self) -> dict:
        """序列化给前端的安全视图(不含锁等内部字段)。"""
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "status": self.status,
                "progress": round(self.progress, 3),
                "stage": self.stage,
                "result": self.result,
                "error": self.error,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
            }


class JobManager:
    """提交 / 查询任务。max_workers 控制并发:本地单用户工具,GPU 任务本就该一次一个
    (显存有限),故默认很小;CPU 的互译/记谱可并发。压测会探并发边界。"""

    def __init__(self, output_root: Path, max_workers: int = 2) -> None:
        self.output_root = Path(output_root)
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._ex = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="mm-job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def _new_job_dir(self, kind: str) -> Path:
        d = self.output_root / f"{kind}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def submit(self, kind: str, runner: Callable[..., dict], *args: Any, **kwargs: Any) -> Job:
        """创建任务并排入线程池。runner 签名:runner(job, *args, **kwargs) -> dict。"""
        job_dir = self._new_job_dir(kind)
        job = Job(id=uuid.uuid4().hex, kind=kind, job_dir=str(job_dir))
        with self._lock:
            self._jobs[job.id] = job
        self._ex.submit(self._run, job, runner, args, kwargs)
        return job

    def _run(self, job: Job, runner: Callable[..., dict], args: tuple, kwargs: dict) -> None:
        with job._lock:
            job.status = "running"
            job.stage = "处理中"
            job.updated_at = time.time()
        try:
            result = runner(job, *args, **kwargs)
            with job._lock:
                job.result = result if isinstance(result, dict) else {"ok": True, "value": result}
                job.status = "done"
                job.progress = 1.0
                job.stage = "完成"
                job.updated_at = time.time()
        except (Exception, SystemExit) as e:  # 含 SystemExit:runner 内 argparse 等误抛不致让任务永久卡 running(修压测 M)
            tb = traceback.format_exc()
            with job._lock:
                job.status = "error"
                job.error = f"{type(e).__name__}: {e}"
                job.stage = "出错"
                job.updated_at = time.time()
            # 同时打到服务端控制台,便于排查(服务端已 reconfigure utf-8)
            print(f"[job {job.id} {job.kind}] 失败:\n{tb}", flush=True)

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def file_path(self, job_id: str, name: str) -> Optional[Path]:
        """把 (job_id, 文件名) 解析为产物路径,并防目录穿越。"""
        job = self.get(job_id)
        if job is None:
            return None
        base = Path(job.job_dir).resolve()
        # 只允许 job_dir 内的文件(name 可能含子目录,如分离产物);拒绝 .. 穿越。
        # resolve() 也纳入 try:畸形名(如含 NUL 字节)在 os.stat 时抛 ValueError/OSError,
        # 必须接住返回 None(干净 404)而非冒泡成 500(修压测 L:NUL 字节 → 500)。
        try:
            target = (base / name).resolve()
            target.relative_to(base)
        except (ValueError, OSError):
            return None
        return target if target.is_file() else None
