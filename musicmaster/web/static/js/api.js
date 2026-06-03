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
      + '.cast[aria-busy="true"]{cursor:progress}';
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
      var b = MM.badge(it.name), base = it.name.split('/').pop();
      var a = document.createElement('a');
      a.className = 'dl';
      a.href = MM.fileUrl(jobId, it.name);
      a.setAttribute('download', base);
      a.innerHTML =
        '<span class="ft ' + b[0] + '">' + MM.esc(b[1]) + '</span>' +
        '<span><div class="nm">' + MM.esc(base) + '</div><div class="sz">' +
        MM.fmtKB(it.size_kb) + '</div></span><span class="arr">↓</span>';
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
    return function () { return current; };
  };

  /* 把一个 [data-player] 唱机(设计里是播放 mockup)接成上传控件:点击唱机/封面打开文件选择,
     选中后把文件名写进 .pname,并回调 onFile(file)。返回 () => 当前 File|null。 */
  MM.makeUpload = function (player, accept, onFile) {
    var input = document.createElement('input');
    input.type = 'file'; input.hidden = true; if (accept) input.accept = accept;
    player.appendChild(input);
    var nameEl = player.querySelector('.pname');
    var sub = nameEl ? nameEl.querySelector('.psub') : null;
    var subText = sub ? sub.outerHTML : '';
    var deck = player.querySelector('.deck') || player;
    var current = null;
    deck.style.cursor = 'pointer';
    deck.title = '点击选择音频文件';
    function set(file) {
      current = file || null;
      if (nameEl) nameEl.innerHTML = MM.esc(current ? current.name : '点击唱机选择音频') + ' ' + subText;
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
    return function () { return current; };
  };

  var PLAY = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
  var PAUSE = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>';

  /* 用真实音频驱动设计稿里的唱机视觉。会克隆播放/进度控件以剥离 mockup 监听,再接真实 <audio>。
     title/sub 可选,用于更新 .pname/.ptitle 与 .psub/.psubt。 */
  MM.wirePlayer = function (player, url, title, sub) {
    // 标题
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
    var fill = player.querySelector('.pfill'), thumb = player.querySelector('.pthumb'),
        time = player.querySelector('.ptime'), bar = player.querySelector('.pbar'),
        vinyl = player.querySelector('.vinyl');
    // 克隆播放按钮 → 去掉 mockup 监听
    var play = player.querySelector('.pplay');
    if (play) { var np = play.cloneNode(true); play.parentNode.replaceChild(np, play); play = np; play.innerHTML = PLAY; }

    function render() {
      var d = audio.duration || 0, c = audio.currentTime || 0, p = d ? c / d : 0;
      if (fill) fill.style.width = (p * 100).toFixed(1) + '%';
      if (thumb) thumb.style.left = (p * 100).toFixed(1) + '%';
      if (time) time.textContent = MM.fmtTime(c) + ' / ' + MM.fmtTime(d);
    }
    if (play) play.addEventListener('click', function () { audio.paused ? audio.play() : audio.pause(); });
    audio.addEventListener('play', function () { if (play) play.innerHTML = PAUSE; player.classList.add('is-playing'); if (vinyl) vinyl.classList.add('spin'); });
    audio.addEventListener('pause', function () { if (play) play.innerHTML = PLAY; player.classList.remove('is-playing'); if (vinyl) vinyl.classList.remove('spin'); });
    audio.addEventListener('ended', function () { if (play) play.innerHTML = PLAY; });
    audio.addEventListener('timeupdate', render);
    audio.addEventListener('loadedmetadata', render);
    if (bar) {
      var nb = bar.cloneNode(true); bar.parentNode.replaceChild(nb, bar); bar = nb;
      fill = bar.querySelector('.pfill'); thumb = bar.querySelector('.pthumb');
      bar.addEventListener('click', function (e) {
        var r = bar.getBoundingClientRect(), p = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
        if (audio.duration) audio.currentTime = p * audio.duration;
      });
    }
    render();
    return audio;
  };

  window.MM = MM;
})();
