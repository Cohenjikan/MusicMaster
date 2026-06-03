/* MusicMaster — 共享前端客户端(window.MM)。
 *
 * 四个部门模块(convert/transcribe/separate/vocal.js)都建立在这些 helper 之上,
 * 因此交互一致、各模块很薄。后端契约见 musicmaster/web/server.py 与 runners.py:
 *
 *   POST /api/<fn>            multipart 表单 → { job_id }
 *   GET  /api/job/<id>        → { status:queued|running|done|error, progress, stage, result, error }
 *   GET  /api/file/<id>/<name>  下载/预览某任务产物
 *
 * runner 结果(job.result)各部门字段见各自模块顶部注释;通用约定:
 *   result.ok        : 业务是否成功(false=环境未就绪等软失败,要展示 result.message)
 *   result.message   : 人话消息
 *   result.downloads : [{ name, size_kb, kind }]  —— 链接由 MM.fileUrl(jobId, name) 拼
 */
(function () {
  'use strict';
  var MM = {};

  // 注入 toast 所需样式(自包含,免改设计稿 CSS)
  (function injectCss() {
    var css = '#mm-toasts{position:fixed;right:18px;bottom:18px;z-index:9999;display:flex;flex-direction:column;gap:10px;max-width:min(92vw,420px)}'
      + '.mm-toast{font-family:var(--sans,system-ui);font-size:13.5px;line-height:1.5;color:#f6f2ec;background:rgba(22,19,27,.96);'
      + 'border:1px solid rgba(255,255,255,.14);border-left:3px solid #a78bfa;border-radius:12px;padding:12px 15px;'
      + 'box-shadow:0 14px 40px rgba(0,0,0,.5);opacity:0;transform:translateY(8px);transition:.28s;white-space:pre-wrap;word-break:break-word}'
      + '.mm-toast.show{opacity:1;transform:none}.mm-toast.err{border-left-color:#fb7185}.mm-toast.ok{border-left-color:#4ade80}'
      + '.cast[aria-busy="true"]{cursor:progress}'
      // ── 任务进度条(四部门共用;注入式,免改设计稿)──
      + '.mm-prog{margin-top:14px;max-height:0;opacity:0;overflow:hidden;transition:opacity .3s,max-height .3s}'
      + '.mm-prog.show{max-height:60px;opacity:1}'
      + '.mm-prog-bar{position:relative;height:8px;border-radius:99px;background:rgba(255,255,255,.10);overflow:hidden}'
      + '.mm-prog-fill{position:absolute;left:0;top:0;bottom:0;width:0;border-radius:99px;'
      + 'background:var(--accent,#a78bfa);transition:width .35s ease}'
      + '.mm-prog-fill::after{content:"";position:absolute;inset:0;'
      + 'background:linear-gradient(90deg,transparent,rgba(255,255,255,.5),transparent);'
      + 'transform:translateX(-100%);animation:mmprogsh 1.2s linear infinite}'
      + '.mm-prog.done .mm-prog-fill,.mm-prog.err .mm-prog-fill{transition:width .25s ease}'
      + '.mm-prog.done .mm-prog-fill::after,.mm-prog.err .mm-prog-fill::after{display:none}'
      + '.mm-prog.done .mm-prog-fill{background:#4ade80}.mm-prog.err .mm-prog-fill{background:#fb7185}'
      + '.mm-prog-meta{display:flex;justify-content:space-between;gap:10px;margin-top:7px;'
      + 'font-size:12px;line-height:1.4;color:var(--t2,#c4bdc6)}'
      + '.mm-prog-pct{font-variant-numeric:tabular-nums;opacity:.85;flex:none}'
      + '@keyframes mmprogsh{to{transform:translateX(100%)}}'
      // ── 清空 / 重新开始 按钮 ──
      + '.mm-clear{display:block;margin:14px auto 0;padding:7px 18px;font-family:var(--sans,system-ui);'
      + 'font-size:12.5px;color:var(--t2,#c4bdc6);background:transparent;'
      + 'border:1px solid var(--border,rgba(255,255,255,.16));border-radius:99px;cursor:pointer;transition:.18s}'
      + '.mm-clear:hover{color:var(--t0,#f6f2ec);border-color:rgba(255,255,255,.34);background:rgba(255,255,255,.05)}';
    var s = document.createElement('style'); s.textContent = css; document.head.appendChild(s);
  })();

  // ── DOM 小工具 ──
  MM.panel = function (fn) { return document.getElementById('p-' + fn); };
  MM.esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  MM.fmtTime = function (s) {
    s = Math.max(0, Math.round(s || 0));
    return Math.floor(s / 60) + ':' + ('0' + (s % 60)).slice(-2);
  };
  MM.fmtKB = function (kb) {
    kb = +kb || 0;
    return kb >= 1024 ? (kb / 1024).toFixed(1) + ' MB' : kb.toFixed(1) + ' KB';
  };

  // ── 文件下载 URL(逐段编码,保留子目录结构,兼容 CJK/特殊字符)──
  MM.fileUrl = function (jobId, name) {
    var enc = String(name).split('/').map(encodeURIComponent).join('/');
    return '/api/file/' + encodeURIComponent(jobId) + '/' + enc;
  };

  // ── 提交 + 轮询 ──
  MM.submit = function (endpoint, formData) {
    return fetch('/api/' + endpoint, { method: 'POST', body: formData })
      .then(function (r) {
        if (!r.ok) return r.text().then(function (t) { throw new Error('提交失败 HTTP ' + r.status + ' ' + t); });
        return r.json();
      })
      .then(function (j) {
        if (!j.job_id) throw new Error('服务端未返回 job_id');
        return j.job_id;
      });
  };

  MM.poll = function (jobId, onProgress) {
    var INTERVAL = 800;
    return new Promise(function (resolve, reject) {
      function tick() {
        fetch('/api/job/' + encodeURIComponent(jobId))
          .then(function (r) {
            if (!r.ok) throw new Error('查询任务失败 HTTP ' + r.status);
            return r.json();
          })
          .then(function (job) {
            if (typeof onProgress === 'function') { try { onProgress(job); } catch (_) {} }
            if (job.status === 'done' || job.status === 'error') resolve(job);
            else setTimeout(tick, INTERVAL);
          })
          .catch(reject);
      }
      tick();
    });
  };

  /* 一站式:提交 → 轮询。onProgress(job) 每拍回调(可更新阶段文案)。
     返回 Promise<job>(done 或 error 都 resolve;调用方看 job.status / job.result.ok)。 */
  MM.run = function (endpoint, formData, onProgress) {
    return MM.submit(endpoint, formData).then(function (id) { return MM.poll(id, onProgress); });
  };

  // ── 忙碌态:接管某 .cast 按钮(禁用 + 显示阶段),返回 restore() ──
  MM.setBusy = function (btn, stageText) {
    if (!btn) return function () {};
    if (!btn.dataset.label) {
      // 记住按钮的文字部分(末尾文本节点),便于还原
      btn.dataset.label = (btn.textContent || '').trim();
    }
    btn.setAttribute('aria-busy', 'true');
    btn.style.pointerEvents = 'none';
    btn.style.opacity = '.75';
    MM.setBusyText(btn, stageText || '处理中…');
    return function restore() {
      btn.removeAttribute('aria-busy');
      btn.style.pointerEvents = '';
      btn.style.opacity = '';
      MM.setBusyText(btn, btn.dataset.label || '');
    };
  };
  MM.setBusyText = function (btn, text) {
    // .cast 内有 <span class="shine"> 和 <svg> + 文本节点;只替换末尾文本节点
    var last = btn.lastChild;
    while (last && last.nodeType !== 3) last = last.previousSibling; // 找文本节点
    if (last) last.textContent = ' ' + text;
    else btn.appendChild(document.createTextNode(' ' + text));
  };

  // ── 轻量 toast(设计稿无全局通知区,这里补一个)──
  MM.toast = function (msg, type) {
    var box = document.getElementById('mm-toasts');
    if (!box) {
      box = document.createElement('div'); box.id = 'mm-toasts'; document.body.appendChild(box);
    }
    var t = document.createElement('div');
    t.className = 'mm-toast' + (type === 'error' ? ' err' : type === 'ok' ? ' ok' : '');
    t.textContent = msg;
    box.appendChild(t);
    setTimeout(function () { t.classList.add('show'); }, 10);
    setTimeout(function () { t.classList.remove('show'); setTimeout(function () { t.remove(); }, 300); },
      type === 'error' ? 7000 : 4000);
  };

  // ── 文件类型 → 徽标(对应设计稿 .dl .ft 的 score/midi/audio/data 配色)──
  MM.badge = function (name) {
    var ext = (name.split('.').pop() || '').toLowerCase();
    var map = {
      musicxml: ['score', 'XML'], xml: ['score', 'XML'], mxl: ['score', 'XML'],
      mid: ['midi', 'MIDI'], midi: ['midi', 'MIDI'], pdf: ['midi', 'PDF'], ly: ['data', 'LY'],
      wav: ['audio', 'WAV'], mp3: ['audio', 'MP3'], flac: ['audio', 'FLAC'], ogg: ['audio', 'OGG'],
      json: ['data', 'JSON'], svg: ['data', 'SVG'], txt: ['data', 'TXT'], jianpu: ['data', '简谱']
    };
    return map[ext] || ['data', ext.toUpperCase().slice(0, 4) || 'FILE'];
  };

  /* 渲染「带走」下载网格。gridEl 是 .dl-grid 容器;items=[{name,size_kb,kind}]。
     文件名只显示 basename(去掉上传/子目录前缀),链接指向真实产物。 */
  MM.renderDownloads = function (gridEl, jobId, items) {
    if (!gridEl) return;
    gridEl.innerHTML = '';
    (items || []).forEach(function (it) {
      var base = it.name.split('/').pop();
      var dl = it.download || base;          // 干净英文「另存为」名
      var label = it.label || base;          // 易懂标题
      var b = MM.badge(dl);
      var a = document.createElement('a');
      a.className = 'dl';
      // ?as= 把友好英文名告诉服务端,让 Content-Disposition 与 <a download> 一致(否则同源响应会覆盖)
      a.href = MM.fileUrl(jobId, it.name) + '?as=' + encodeURIComponent(dl);
      a.setAttribute('download', dl);
      a.title = it.desc || label;
      a.innerHTML =
        '<span class="ft ' + b[0] + '">' + MM.esc(b[1]) + '</span>' +
        '<span class="dl-mid"><div class="nm">' + MM.esc(label) + '</div>' +
        (it.desc ? '<div class="ds">' + MM.esc(it.desc) + '</div>' : '') +
        '<div class="sz">' + MM.esc(dl) + ' · ' + MM.fmtKB(it.size_kb) + '</div></span>' +
        '<span class="arr">↓</span>';
      gridEl.appendChild(a);
    });
    if (!(items || []).length) {
      gridEl.innerHTML = '<div class="help" style="grid-column:1/-1">(暂无可下载产物)</div>';
    }
  };

  // ── 切换某面板的「产出」子标签到第 idx 个(复用设计稿已绑定的 .out-tab 点击)──
  MM.switchOut = function (panel, idx) {
    var tabs = panel.querySelectorAll('[data-outtabs] .out-tab');
    if (tabs[idx]) tabs[idx].click();
  };

  /* 裁掉真实五线谱(music21/Verovio 生成)整页 SVG 的大片白边:注入后把 viewBox 收到内容包围盒。
     谱面 pane 可能处于隐藏子标签(display:none → getBBox 失效)→ 退而用离屏可见探针克隆测量。
     纯前端,不碰渲染配方。convert / transcribe 注入 staff_svg 后调用。 */
  MM.fitSheetSvg = function (container) {
    var svg = container && container.querySelector('svg');
    if (!svg) return;
    // 真实谱面是 Verovio 整页(短曲只占约 1/3,上下全白)。坑:改 svg 的 viewBox/width、或把 svg
    // 移到新容器,都会触发内部嵌套 svg 重排,墨迹位置随之失效 → 怎么裁都对不上。可靠做法:
    // **完全不动 svg 的属性与父节点**,只在 svg 上加一个 CSS transform(平移+缩放)把音符区
    // 推到左上并放大,再把容器 .sheet 的高度收到那条 + overflow:hidden 裁掉其余 —— 纯视觉,不重排。
    // 墨迹只量音符图元(path/use/...),**排除标题文字**,免得把「Music21 Fragment」和它下方空白算进来。
    function fitOnce() {
      if (svg.getAttribute('data-mm-fitted')) return true;
      var r = svg.getBoundingClientRect();
      if (r.width < 30 || r.height < 30) return false;            // 隐藏/未布局 → 等
      // Verovio 把每行乐谱包成 <g class="system"> —— 优先只量它,天然排除标题与「Verovio」页脚;
      // 拿不到则退回量音符图元(仍排除 <text>)。
      var systems = svg.querySelectorAll('g.system');
      var useSys = systems.length > 0;
      var nodes = useSys ? systems : svg.querySelectorAll('path,use,ellipse,line,polyline,rect');
      var top = Infinity, bot = -Infinity, left = Infinity, right = -Infinity, cnt = 0;
      for (var i = 0; i < nodes.length; i++) {
        var b = nodes[i].getBoundingClientRect();
        if (b.width > 0 && b.height > 0 && b.height < r.height * 0.9 && b.width < r.width * 1.6) {
          if (b.top < top) top = b.top; if (b.bottom > bot) bot = b.bottom;
          if (b.left < left) left = b.left; if (b.right > right) right = b.right; cnt++;
        }
      }
      if (cnt < (useSys ? 1 : 3)) return false;
      var ix = left - r.left, iy = top - r.top, iw = right - left, ih = bot - top;
      if (iw <= 0 || ih <= 0) return false;
      if (iy < r.height * 0.04 && (iy + ih) > r.height * 0.94) { svg.setAttribute('data-mm-fitted', '1'); return true; }  // 已紧凑
      var padX = Math.max(iw * 0.03, 8), padY = Math.max(ih * 0.22, 18);
      var cx = Math.max(0, ix - padX), cy = Math.max(0, iy - padY);
      var cw = Math.min(r.width - cx, iw + padX * 2), ch = ih + padY * 2;
      var Wc = r.width;                                            // 目标宽 = 当前谱面宽
      var Hc = Math.max(200, Math.round((window.innerHeight || 800) * 0.6));
      var s = Wc / cw; if (s * ch > Hc) s = Hc / ch;               // 先填宽,过高则改填高
      // 就地变换(不移动 svg、不改其属性):把裁剪框左上角移到 (0,0) 并按 s 缩放
      svg.style.transformOrigin = 'top left';
      svg.style.transform = 'translate(' + (-cx * s) + 'px,' + (-cy * s) + 'px) scale(' + s + ')';
      // 容器收高 + 裁掉溢出(svg 布局盒仍很高,这里只露出顶部裁出来的那条)
      container.style.overflow = 'hidden';
      container.style.height = Math.round(ch * s) + 'px';
      container.style.padding = '0';
      svg.setAttribute('data-mm-fitted', '1');
      return true;
    }
    if (fitOnce()) return;
    var n = 0;
    (function retry() {                                          // 等可见的若干帧内补裁
      if (svg.getAttribute('data-mm-fitted') || fitOnce()) return;
      if (n++ < 40 && window.requestAnimationFrame) requestAnimationFrame(retry);
    })();
    // 切到「谱面」子标签时再裁。钩子**每面板只绑一次**(_mmSheetHook 守卫),且不持有旧 svg ——
    // 点击时对当前 DOM 里仍未裁的谱面 svg 调 MM.fitSheetSvg,避免每次出谱都叠加监听器/定时器(修 QA#5)。
    var panel = container.closest ? container.closest('.panel') : null;
    if (panel && !panel._mmSheetHook) {
      panel._mmSheetHook = true;
      var tabs = panel.querySelectorAll('[data-outtabs] .out-tab');
      Array.prototype.forEach.call(tabs, function (t) {
        t.addEventListener('click', function () {
          var sheets = panel.querySelectorAll('.sheet');
          Array.prototype.forEach.call(sheets, function (sh) {
            if (sh.querySelector('svg:not([data-mm-fitted])')) {
              setTimeout(function () { MM.fitSheetSvg(sh); }, 80);
              setTimeout(function () { MM.fitSheetSvg(sh); }, 360);
            }
          });
        });
      });
    }
  };

  /* 任务进度条控制器:在提交按钮(btn)下方注入一条进度条,四部门通用。
     opts.comfort=true 开「安慰模式」(给记谱/重塑这类日志不即时、真实进度只有粗档的长任务):
       真实值不动时极慢爬升(0.01%/s,过 85% 后 0.01%/30s,上限 0.97),2 位小数让数字肉眼在动;
       轮播 opts.messages 里的技术文案走一遍架构,产生「仍在运作」的安全感;
       真实进度一到(日志同步)即缓动跳向真值 —— 自动切回真实进度条。
     普通模式:trickle 缓动 + 流光,后端进度粗也绝不显卡死,但不虚报(上限 target+0.12 / 0.92)。
     start/update(job)/done/fail;每个 btn 复用同一条(幂等)。 */
  MM.progress = function (btn, opts) {
    var noop = { start: function () {}, update: function () {}, done: function () {}, fail: function () {} };
    if (!btn) return noop;
    opts = opts || {};
    if (btn._mmProg) { btn._mmProg._reset(opts); return btn._mmProg; }

    var el = document.createElement('div');
    el.className = 'mm-prog';
    el.setAttribute('role', 'progressbar');
    el.innerHTML = '<div class="mm-prog-bar"><span class="mm-prog-fill"></span></div>'
      + '<div class="mm-prog-meta"><span class="mm-prog-stage"></span><span class="mm-prog-pct"></span></div>';
    if (btn.parentNode) btn.parentNode.insertBefore(el, btn.nextSibling);

    var fill = el.querySelector('.mm-prog-fill'),
        stageEl = el.querySelector('.mm-prog-stage'),
        pctEl = el.querySelector('.mm-prog-pct');
    var displayed = 0, target = 0, timer = null, finished = false, hideTimer = null;
    var comfort = !!opts.comfort, messages = opts.messages || [], msgIdx = 0, secCount = 0;

    function render() {
      fill.style.width = (displayed * 100).toFixed(2) + '%';
      pctEl.textContent = (finished && displayed >= 1) ? '100%'
        : (comfort ? (displayed * 100).toFixed(2) : Math.round(displayed * 100)) + '%';
    }
    function tickNormal() {
      if (finished) return;
      var ceil = Math.min(0.92, target + 0.12);
      if (displayed < target) displayed += (target - displayed) * 0.25;
      else if (displayed < ceil) displayed += 0.006;
      if (displayed > 0.999) displayed = 0.999;
      render();
    }
    function tickComfort() {
      if (finished) return;
      secCount++;
      if (target > displayed + 0.0005) {
        displayed += (target - displayed) * 0.35;          // 真实进度到了 → 缓动跳向真值
      } else {                                             // 真实值不动 → 极慢假进度(纯安慰)
        var perSec = displayed < 0.85 ? 0.0001 : (0.0001 / 30);  // 0.01%/s;过 85 后 0.01%/30s
        var cap = displayed < 0.85 ? 0.85 : 0.97;
        if (displayed < cap) displayed = Math.min(cap, displayed + perSec);
      }
      if (displayed > 0.999) displayed = 0.999;
      if (messages.length && secCount % 4 === 0) {         // 每 ~4s 换一条技术安慰文案
        stageEl.textContent = messages[msgIdx % messages.length]; msgIdx++;
      }
      render();
    }
    function startTimer() {
      if (timer) return;
      timer = setInterval(comfort ? tickComfort : tickNormal, comfort ? 1000 : 200);
    }
    function stopTimer() { if (timer) { clearInterval(timer); timer = null; } }

    var ctrl = {
      el: el,
      _reset: function (o) {
        o = o || {};
        finished = false; displayed = 0; target = 0; msgIdx = 0; secCount = 0;
        comfort = !!o.comfort; messages = o.messages || [];
        stopTimer();
        el.classList.remove('done', 'err'); stageEl.textContent = ''; render();
      },
      start: function () {
        // 每次开始都清零状态(transcribe 的 prog 在 init 建一次、重复提交不会再 _reset → 必须在这清)
        if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }  // 取消上一轮排队的收起,免得隐藏新条
        finished = false; displayed = 0; target = 0; msgIdx = 0; secCount = 0;
        el.classList.remove('done', 'err'); render();
        el.classList.add('show');
        if (comfort && messages.length) { stageEl.textContent = messages[0]; msgIdx = 1; }
        startTimer();
      },
      update: function (job) {
        if (!job) return;
        if (typeof job.progress === 'number' && isFinite(job.progress)) {
          target = Math.max(target, Math.min(1, Math.max(0, job.progress)));
        }
        if (!comfort && job.stage) stageEl.textContent = job.stage;  // 安慰模式由轮播文案主导
      },
      done: function () {
        finished = true; stopTimer();
        el.classList.add('done'); displayed = 1; render();
        if (hideTimer) clearTimeout(hideTimer);
        hideTimer = setTimeout(function () { el.classList.remove('show'); hideTimer = null; }, 750);
      },
      fail: function () {
        finished = true; stopTimer();
        el.classList.add('err'); render();
        if (hideTimer) clearTimeout(hideTimer);
        hideTimer = setTimeout(function () { el.classList.remove('show'); hideTimer = null; }, 1500);
      },
      hide: function () {                 // 清空时即时收起(不闪红/绿)
        if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
        finished = true; stopTimer();
        el.classList.remove('show', 'done', 'err');
        displayed = 0; target = 0; stageEl.textContent = ''; render();
      }
    };
    btn._mmProg = ctrl;
    return ctrl;
  };

  /* 在某面板的提交按钮所在卡片底部注入「清空 / 重新开始」按钮:点了调 onClear()
     (各模块自己重置输入文件 / 播放器 / 结果 / 下载 / 进度条),降低「拖新文件覆盖」的学习成本。
     每面板只建一个(幂等)。 */
  MM.clearButton = function (panel, onClear) {
    var btn = panel && panel.querySelector('.cast');
    if (!btn) return null;
    if (btn._mmClear) return btn._mmClear;
    var c = document.createElement('button');
    c.type = 'button';
    c.className = 'mm-clear';
    c.textContent = '清空 / 重新开始';
    (btn.parentNode || panel).appendChild(c);   // 卡片底部,顺序确定
    c.addEventListener('click', function () {
      try { if (typeof onClear === 'function') onClear(); } catch (e) {}
      MM.toast('已清空,可以开始新的了。', 'ok');
    });
    btn._mmClear = c;
    return c;
  };

  /* 把一个 .drop <label>(内含 <input type=file>)接成可用上传:监听 change + 拖放,
     选中后更新 .t 文案显示文件名,并回调 onFile(file)。返回 () => 当前 File|null。 */
  MM.makeDrop = function (label, onFile) {
    var input = label.querySelector('input[type=file]');
    var titleEl = label.querySelector('.t');
    var origTitle = titleEl ? titleEl.textContent : '';
    var current = null;
    function set(file) {
      current = file || null;
      if (titleEl) titleEl.textContent = current ? current.name : origTitle;
      if (typeof onFile === 'function') onFile(current);
    }
    if (input) input.addEventListener('change', function () { set(input.files[0]); });
    label.addEventListener('drop', function (e) {
      e.preventDefault();
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) set(e.dataTransfer.files[0]);
    });
    var get = function () { return current; };
    get.clear = function () { if (input) input.value = ''; set(null); };
    return get;
  };

  /* 把一个 [data-player] 唱机接成「上传 + 真实预览」控件:
     点唱机封面(.deck)打开文件选择;选中后唱机直接用真实 <audio> 播放该文件
     (播放/暂停、可拖动进度、真实倍速),文件名写进 .pname,并回调 onFile(file)。
     返回 () => 当前 File|null。设计稿里这些唱机本是无声 mockup —— 这里换成真声。 */
  MM.makeUploadPlayer = function (player, accept, onFile) {
    var input = document.createElement('input');
    input.type = 'file'; input.hidden = true; if (accept) input.accept = accept;
    player.appendChild(input);
    var nameEl = player.querySelector('.pname');
    var sub = nameEl ? nameEl.querySelector('.psub') : null;
    var subText = sub ? sub.outerHTML : '';
    var deck = player.querySelector('.deck') || player;
    deck.style.cursor = 'pointer';
    deck.title = '点击选择音频文件';

    // 真实音频:先把控件接好(剥离 mockup 假播放),之后每选一个文件只换 src 即可反复预览。
    var audio = new Audio(); audio.preload = 'metadata';
    wireAudio(player, audio);

    var current = null, objUrl = null;
    function set(file) {
      current = file || null;
      if (objUrl) { try { URL.revokeObjectURL(objUrl); } catch (_) {} objUrl = null; }
      try { audio.pause(); } catch (_) {}
      if (current) {
        objUrl = URL.createObjectURL(current);
        audio.src = objUrl; try { audio.load(); } catch (_) {}
        if (nameEl) nameEl.innerHTML = MM.esc(current.name) + ' ' + subText;
      } else {
        audio.removeAttribute('src'); try { audio.load(); } catch (_) {}
        if (nameEl) nameEl.innerHTML = MM.esc('点击唱机选择音频') + ' ' + subText;
      }
      if (typeof onFile === 'function') onFile(current);
    }
    deck.addEventListener('click', function () { input.click(); });
    input.addEventListener('change', function () { set(input.files[0]); });
    player.addEventListener('drop', function (e) {
      e.preventDefault();
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) set(e.dataTransfer.files[0]);
    });
    player.addEventListener('dragover', function (e) { e.preventDefault(); });
    set(null);
    var get = function () { return current; };
    get.clear = function () { if (input) input.value = ''; set(null); };
    return get;
  };

  var PLAY = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
  var PAUSE = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>';

  /* 内核:用真实 <audio> 驱动一个唱机的所有控件。
     先克隆 .pplay/.pbar/.pspeed 以剥离设计稿 mockup 的假监听(假播放/假拖动/假倍速),
     再绑定真实播放:播放/暂停、进度可点击+可拖动 seek、真实时间、真实倍速、转盘随播放转。
     audio 会被挂到 player 下(隐藏、无 controls)以存活并便于调试。返回该 <audio>。 */
  function wireAudio(player, audio) {
    var play = player.querySelector('.pplay');
    if (play) { var np = play.cloneNode(true); play.parentNode.replaceChild(np, play); play = np; }
    var bar = player.querySelector('.pbar');
    if (bar) { var nb = bar.cloneNode(true); bar.parentNode.replaceChild(nb, bar); bar = nb; }
    var speed = player.querySelector('.pspeed');
    if (speed) { var ns = speed.cloneNode(true); speed.parentNode.replaceChild(ns, speed); speed = ns; }

    var fill = bar ? bar.querySelector('.pfill') : null;
    var thumb = bar ? bar.querySelector('.pthumb') : null;
    var time = player.querySelector('.ptime');
    var vinyl = player.querySelector('.vinyl');

    // 初始统一为「未播放」(showpiece 设计稿默认带 is-playing,这里收回,避免无声却在转)
    if (play) play.innerHTML = PLAY;
    player.classList.remove('is-playing');
    if (vinyl) vinyl.classList.remove('spin');
    try { audio.style.display = 'none'; player.appendChild(audio); } catch (_) {}

    function render() {
      var d = audio.duration; if (!isFinite(d) || d <= 0) d = 0;
      var c = audio.currentTime || 0, p = d ? c / d : 0;
      if (fill) fill.style.width = (p * 100).toFixed(1) + '%';
      if (thumb) thumb.style.left = (p * 100).toFixed(1) + '%';
      if (time) time.textContent = MM.fmtTime(c) + ' / ' + MM.fmtTime(d);
    }
    if (play) play.addEventListener('click', function () {
      if (audio.paused) { var pr = audio.play(); if (pr && pr.catch) pr.catch(function () {}); }
      else audio.pause();
    });
    audio.addEventListener('play', function () { if (play) play.innerHTML = PAUSE; player.classList.add('is-playing'); });
    audio.addEventListener('pause', function () { if (play) play.innerHTML = PLAY; player.classList.remove('is-playing'); });
    audio.addEventListener('ended', function () { if (play) play.innerHTML = PLAY; player.classList.remove('is-playing'); });
    audio.addEventListener('timeupdate', render);
    audio.addEventListener('loadedmetadata', render);
    audio.addEventListener('durationchange', render);

    if (bar) {
      var dragging = false;
      function seek(clientX) {
        var r = bar.getBoundingClientRect();
        var p = Math.min(1, Math.max(0, (clientX - r.left) / (r.width || 1)));
        var d = audio.duration;
        if (isFinite(d) && d > 0) audio.currentTime = p * d;
        // 元数据未就绪也先移动视觉,放手不会「失效」
        if (fill) fill.style.width = (p * 100).toFixed(1) + '%';
        if (thumb) thumb.style.left = (p * 100).toFixed(1) + '%';
      }
      bar.addEventListener('pointerdown', function (e) {
        dragging = true; bar.classList.add('dragging');
        try { bar.setPointerCapture(e.pointerId); } catch (_) {}
        seek(e.clientX); e.preventDefault();
      });
      bar.addEventListener('pointermove', function (e) { if (dragging) seek(e.clientX); });
      function stop() { dragging = false; bar.classList.remove('dragging'); }
      bar.addEventListener('pointerup', stop);
      bar.addEventListener('pointercancel', stop);
      window.addEventListener('pointerup', stop);
    }

    if (speed) {
      var speeds = [1, 1.5, 2], si = 0, base = 3.6;
      speed.addEventListener('click', function () {
        si = (si + 1) % speeds.length; var v = speeds[si];
        speed.textContent = v + '×';
        audio.playbackRate = v;
        if (vinyl) vinyl.style.setProperty('--spin', (base / v).toFixed(2) + 's');
      });
    }

    render();
    return audio;
  }

  /* 用真实音频驱动结果唱机。title/sub 可选,更新 .pname/.ptitle 与 .psub/.psubt。 */
  MM.wirePlayer = function (player, url, title, sub) {
    var t = player.querySelector('.ptitle') || player.querySelector('.pname');
    var subEl = player.querySelector('.psubt') || (player.querySelector('.pname') && player.querySelector('.psub'));
    if (t && title != null) {
      if (t.classList.contains('pname')) {
        t.innerHTML = MM.esc(title) + (sub != null ? ' <span class="psub">' + MM.esc(sub) + '</span>' : (subEl ? subEl.outerHTML : ''));
        subEl = t.querySelector('.psub');
      } else { t.textContent = title; }
    }
    if (subEl && subEl.classList && subEl.classList.contains('psubt') && sub != null) subEl.textContent = sub;
    var audio = new Audio(url); audio.preload = 'metadata';
    return wireAudio(player, audio);
  };

  window.MM = MM;
})();
