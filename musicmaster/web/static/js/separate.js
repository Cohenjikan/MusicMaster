/* 拆声(人声分离)接入 —— 把 #p-separate 面板接到真实后端 /api/separate。
 *
 * 后端契约(见 musicmaster/web/server.py / runners.py:run_separate):
 *   POST /api/separate  字段:audio(混音文件)、stages(如 '1,2,3')、denoise('dereverb'|'deecho')
 *   result = { ok, message, log, tracks:[{ name, size_kb, kind }] }
 *     ok=false 为软失败(常见:本机无 GPU venv .venv-sep),必须把 message 友好展示。
 *
 * 全部交互复用 window.MM(api.js):提交/轮询/下载/播放器都不自己造。
 * 只改本文件,不动 index.html / api.js / 其它部门模块。
 */
(function () {
  'use strict';

  function init() {
    var MM = window.MM;
    if (!MM) return;                       // api.js 未加载,放弃(理论不会发生)
    var panel = MM.panel('separate');
    if (!panel) return;

    var btn = panel.querySelector('.cast');
    // 输入唱机:.card 内第一个 [data-player],接成上传控件
    var inputPlayer = panel.querySelector('.card [data-player]');
    var getFile = inputPlayer
      ? MM.makeUploadPlayer(inputPlayer, 'audio/*', null)
      : function () { return null; };

    // 两个产出子面板:0=几束声音、1=带走(按 index.html 中的出现顺序)
    var panes = panel.querySelectorAll('.out-body .out-pane');
    var pane0 = panes[0];
    var pane1 = panes[1];

    // 几束声音 pane:init 时缓存「放唱机的容器 playerHost」与模板 HTML 字符串。
    // 关键:不可每次从 playerTpl.parentNode 反查 —— 首跑会把模板自身 detach,二次进入 parentNode 即 null
    // 导致 querySelectorAll 抛 TypeError、成品丢失(修压测 M)。存 HTML 字符串则反复克隆永不失效。
    var firstPlayer = pane0 ? pane0.querySelector('[data-player]') : null;
    var playerHost = firstPlayer ? firstPlayer.parentNode : null;
    var playerTplHTML = firstPlayer ? firstPlayer.outerHTML : null;
    function clearPlayers() {
      if (!playerHost) return;
      var ps = playerHost.querySelectorAll('[data-player]');
      Array.prototype.forEach.call(ps, function (el) { el.parentNode.removeChild(el); });
    }

    if (!btn) return;

    // 收集勾选的处理步骤 → 按「勾选的第 i 个」映射成序号 String(i+1),join(',');都没勾默认 '1'
    function collectStages() {
      var boxes = panel.querySelectorAll('.steps .step input[type=checkbox]');
      var seq = [];
      Array.prototype.forEach.call(boxes, function (cb, i) {
        if (cb.checked) seq.push(String(i + 1));
      });
      return seq.length ? seq.join(',') : '1';
    }

    // 读取清理模型药丸(.on 切换由 index.html 内联脚本负责,这里只读)
    function readDenoise() {
      var on = panel.querySelector('.cm-pills .mini.on');
      var v = on && on.dataset ? on.dataset.val : '';
      return v || 'dereverb';
    }

    // 渲染「几束声音」pane:动态生成每束音频的唱机 + 更新文案
    function renderTracks(jobId, result) {
      if (!pane0) return;
      var tracks = (result && result.tracks) || [];

      // 顶部「拆好了」印章文案
      var dt = pane0.querySelector('.done .dt');
      var dp = pane0.querySelector('.done .dp');
      if (dt) dt.textContent = tracks.length ? ('拆好了 · ' + tracks.length + ' 束声音') : '拆好了';
      if (dp) dp.textContent = (result && result.message) || '一束束,各自干净';

      if (!playerHost || !playerTplHTML) return;

      clearPlayers();  // 清掉现有唱机(含 mockup)后按 tracks 重建;模板是 HTML 字符串,反复跑不失效

      // .donelist 在 host 中的位置:把新唱机插到它前面,保持「唱机在上、清单在下」
      var doneList = pane0.querySelector('.donelist');
      var anchor = (doneList && doneList.parentNode === playerHost) ? doneList : null;

      tracks.forEach(function (tr) {
        var tmp = document.createElement('div');
        tmp.innerHTML = playerTplHTML;
        var clone = tmp.firstElementChild;
        if (!clone) return;
        var base = String(tr.name).split('/').pop();
        if (anchor) playerHost.insertBefore(clone, anchor);
        else playerHost.appendChild(clone);
        // 接真实音频(wirePlayer 内部会克隆控件以剥离 mockup 监听)
        MM.wirePlayer(clone, MM.fileUrl(jobId, tr.name), base, null);
      });

      // 更新成品清单:每束声音一条
      if (doneList) {
        if (tracks.length) {
          doneList.innerHTML = '';
          tracks.forEach(function (tr) {
            var base = String(tr.name).split('/').pop();
            var line = document.createElement('div');
            line.className = 'doneline';
            line.innerHTML =
              '<span class="d"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
              'stroke-width="3"><path d="M20 6L9 17l-5-5" stroke-linecap="round" ' +
              'stroke-linejoin="round"/></svg></span>' + MM.esc(base);
            doneList.appendChild(line);
          });
        }
      }
    }

    // 软失败(result.ok===false):把 message 同时展示在面板与 toast
    function showSoftFail(message) {
      var msg = message || '拆声未能完成。';
      if (pane0) {
        var dt = pane0.querySelector('.done .dt');
        var dp = pane0.querySelector('.done .dp');
        if (dt) dt.textContent = '没能拆成';
        if (dp) dp.textContent = msg;
      }
      clearPlayers();  // 清掉 mockup 的「干净主唱/伴奏」假唱机,失败时不显示假成品
      // 清空带走 pane,避免残留 mockup 下载项
      if (pane1) MM.renderDownloads(pane1.querySelector('.dl-grid'), '', []);
      MM.toast(msg, 'error');
      MM.switchOut(panel, 0);              // 切到「几束声音」让用户看到说明
    }

    btn.addEventListener('click', function () {
      var file = getFile();
      if (!file) { MM.toast('请先点上方唱机选择要拆的整首歌(音频文件)。', 'error'); return; }

      var fd = new FormData();
      fd.append('audio', file);
      fd.append('stages', collectStages());
      fd.append('denoise', readDenoise());

      var prog = MM.progress(btn);
      var restore = MM.setBusy(btn, '提交中…');
      prog.start();
      MM.run('separate', fd, function (job) {
        MM.setBusyText(btn, job.stage || '分离中…');
        prog.update(job);
      }).then(function (job) {
        var ok = job.status !== 'error' && job.result && job.result.ok;
        if (ok) prog.done(); else prog.fail();
        if (job.status === 'error') { MM.toast(job.error || '拆声出错了。', 'error'); return; }
        var result = job.result || {};
        if (result.ok === false) { showSoftFail(result.message); return; }
        // 成功:渲染两个 pane + 切到「几束声音」
        renderTracks(job.id, result);
        if (pane1) MM.renderDownloads(pane1.querySelector('.dl-grid'), job.id, result.tracks || []);
        MM.switchOut(panel, 0);
      }).catch(function (e) {
        prog.fail();
        MM.toast('' + e, 'error');
      }).finally(restore);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
