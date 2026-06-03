/* 互译(简谱 ⇄ 五线谱)接入 —— 部门 convert,面板 #p-convert。
 *
 * 后端:POST /api/convert,字段 file / direction('j2s'|'s2j') / key(可空)。
 * 结果:{ ok, message, direction, staff_svg|null, jianpu_text|null, downloads }。
 * 一切提交/轮询/下载渲染/阶段文案都复用 window.MM(见 js/api.js),本模块只做接线。
 */
(function () {
  'use strict';

  function init() {
    var MM = window.MM;
    var panel = MM && MM.panel('convert');
    if (!panel) return;

    var dropEl = panel.querySelector('.drop');
    var btn = panel.querySelector('.cast');
    var getFile = MM.makeDrop(dropEl); // 接管文件选择 + 拖放,返回 () => File|null

    // 三个产出 pane:0 誊写 / 1 谱面 / 2 带走
    var panes = panel.querySelectorAll('.out-body .out-pane');
    var paneTranscribe = panes[0];
    var paneSheet = panes[1];
    var paneTake = panes[2];

    if (!btn) return;

    btn.addEventListener('click', function () {
      var file = getFile();
      if (!file) { MM.toast('请先放上一份谱(简谱文本或五线谱文件)。', 'error'); return; }

      var dirInput = panel.querySelector('input[name=cv]:checked');
      var direction = dirInput ? dirInput.value : 'j2s';
      var keyInput = panel.querySelector('.input');
      var key = keyInput ? keyInput.value.trim() : '';

      var fd = new FormData();
      fd.append('file', file);
      fd.append('direction', direction);
      fd.append('key', key);

      var prog = MM.progress(btn);
      var restore = MM.setBusy(btn, '提交中…');
      prog.start();
      MM.run('convert', fd, function (job) {
        if (job && job.stage) MM.setBusyText(btn, job.stage);
        prog.update(job);
      })
        .then(function (job) {
          var ok = job.status !== 'error' && job.result && job.result.ok;
          if (ok) prog.done(); else prog.fail();
          if (job.status === 'error') { MM.toast(job.error || '任务出错。', 'error'); return; }
          var r = job.result || {};
          if (r.ok === false) { MM.toast(r.message || '转换未完成。', 'error'); return; }
          render(job, r);
        })
        .catch(function (e) { prog.fail(); MM.toast('' + e, 'error'); })
        .finally(restore);
    });

    MM.clearButton(panel, function () {
      if (getFile.clear) getFile.clear();
      if (btn._mmProg) btn._mmProg.hide();
      if (paneTake) MM.renderDownloads(paneTake.querySelector('.dl-grid'), '', []);
      MM.switchOut(panel, 0);
    });

    function render(job, r) {
      var isJ2s = r.direction === 'j2s';

      // ── 誊写[0]:盖章 + 消息;清掉 mockup 的假统计 ──
      if (paneTranscribe) {
        var dt = paneTranscribe.querySelector('.dt');
        var dp = paneTranscribe.querySelector('.dp');
        if (dt) dt.textContent = isJ2s ? '已译成五线谱' : '已译回简谱';
        if (dp) dp.textContent = r.message || '';
        var facts = paneTranscribe.querySelector('.facts');
        if (facts) { facts.innerHTML = ''; facts.style.display = 'none'; } // 无真实统计,绝不留 mockup
      }

      // ── 谱面[1]:j2s 注入 staff_svg;s2j 显示 jianpu_text 文本 ──
      if (paneSheet) {
        var sheet = paneSheet.querySelector('.sheet');
        var jianpu = paneSheet.querySelector('.jianpu');
        if (isJ2s && r.staff_svg) {
          if (sheet) { sheet.style.display = ''; sheet.innerHTML = r.staff_svg; MM.fitSheetSvg(sheet); }
          if (jianpu) jianpu.style.display = 'none';
        } else if (!isJ2s) {
          if (sheet) sheet.style.display = 'none'; // 隐藏 mockup 五线谱
          if (jianpu) {
            jianpu.style.display = '';
            jianpu.innerHTML = '';
            var pre = document.createElement('pre');
            pre.style.whiteSpace = 'pre-wrap';
            pre.textContent = r.jianpu_text || '';
            jianpu.appendChild(pre);
          }
        }
      }

      // ── 带走[2]:真实产物下载网格 ──
      if (paneTake) {
        MM.renderDownloads(paneTake.querySelector('.dl-grid'), job.id, r.downloads);
      }

      // 有五线谱预览就跳到「谱面」,否则停在「誊写」
      MM.switchOut(panel, r.staff_svg ? 1 : 0);
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
