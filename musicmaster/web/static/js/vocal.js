/* 重塑(修音换音色)接入 —— 部门:vocal,面板 #p-vocal。
 *
 * 后端契约(见 musicmaster/web/server.py / runners.py:run_vocal):
 *   POST /api/vocal  multipart 字段:
 *     raw           文件 · 你的原唱(跑调也行)
 *     ref           文件 · 想唱成的样子(去和声后的目标旋律)
 *     self_ref      文件 · 你的声音样本(音色锚,~10–30s 干净清唱)
 *     correct_steps int   · 音准精修(扩散步数 50–200)
 *     voice_steps   int   · 音色细腻度(重塑步数 20–100)
 *     voice_cfg     float · 贴近你的声音(cfg 0–1)
 *   result:{ ok, message, detail, final|null, mid|null, downloads }
 *
 * 全程复用 window.MM(api.js)的 helper:不自己写 fetch/轮询/播放器/下载渲染。
 * 本脚本在 UI 行为脚本与 api.js 之后加载(body 末尾),DOM 与 window.MM 均已就绪。
 */
(function () {
  'use strict';
  if (!window.MM) return;
  var MM = window.MM;

  var panel = MM.panel('vocal');
  if (!panel) return;

  // 安慰进度条文案:把「修音准 → 换音色」两段式架构走一遍。整首高精度可能 20+ 分钟,
  // 真实进度只有 ~15% / 50% / 92% 几个里程碑,长等待靠这些技术名词 + 极慢假进度撑住安全感。
  var COMFORT_MSGS = [
    '正在载入两段式管线:先修音准,再以你的音色重生成…',
    '正在把原唱与目标旋律按时间轴对齐…',
    '正在提取你原唱的逐帧音高曲线…',
    '正在以目标旋律为参考,规划音准修正量…',
    '正在启动扩散模型,按步数迭代修音准…',
    '扩散采样进行中:步数越多越精细,也越慢…',
    '正在整首自动分块,逐块修音以保留细节…',
    '正在把修好的音准重采样到 44.1kHz…',
    '正在载入 Seed-VC 音色转换模型(GPU)…',
    '正在从你的清唱样本里提取音色指纹…',
    '正在做内容与音色解耦:保留咬字,替换音色…',
    '正在逐块重生成歌声,贴近你本人的声音…',
    'cfg 引导中:在「像你」与「自然」之间找平衡…',
    '正在跨块做交叉淡化,消除拼接痕迹…',
    '正在统一响度与动态,避免忽大忽小…',
    '正在对齐相位,减少分块边界的杂音…',
    '正在合成最终歌声,保留气声与情感细节…',
    '正在回采样并导出成品 wav…',
    '正在保留「只修音准」的中间产物以备对照…',
    '整首高精度可能要二十分钟以上,模型仍在努力…',
    '正在做质量校验:确保在调、干净、仍是你…',
    '快好了 —— 正在收尾整理成品与中间产物…'
  ];

  // 三个上传:按索引对齐后端字段(0=原唱 raw / 1=去和声目标 ref / 2=音色锚 self_ref)。
  var drops = panel.querySelectorAll('.vc-ins .drop');
  var getRaw = drops[0] ? MM.makeDrop(drops[0]) : function () { return null; };
  var getRef = drops[1] ? MM.makeDrop(drops[1]) : function () { return null; };
  var getSelf = drops[2] ? MM.makeDrop(drops[2]) : function () { return null; };

  // 三个旋钮(读 .value 即可;数值显示由设计稿 UI 脚本自行更新,不在此处管)。
  var csEl = panel.querySelector('[data-slider=cs]');   // correct_steps
  var vsEl = panel.querySelector('[data-slider=vs]');   // voice_steps
  var cfgEl = panel.querySelector('[data-slider=cfg]'); // voice_cfg

  // 结果区:成品(.player.showpiece)、中间产物(其后那个 .player)。
  var showpiece = panel.querySelector('.player.showpiece');
  var players = panel.querySelectorAll('[data-player]');
  var midPlayer = null;
  for (var i = 0; i < players.length; i++) {
    if (players[i] !== showpiece) { midPlayer = players[i]; break; }
  }

  // 面板原本没有下载区:在 ii 卡片(放按钮/播放器的那张)末尾建一个 .dl-grid。
  var btn = panel.querySelector('.cast');
  var iiCard = btn ? btn.closest('.card') : (midPlayer ? midPlayer.closest('.card') : panel);
  var dlGrid = null;
  function ensureGrid() {
    if (dlGrid) return dlGrid;
    dlGrid = document.createElement('div');
    dlGrid.className = 'dl-grid';
    dlGrid.style.marginTop = '18px';
    (iiCard || panel).appendChild(dlGrid);
    return dlGrid;
  }

  if (!btn) return;

  btn.addEventListener('click', function () {
    var raw = getRaw(), ref = getRef(), self = getSelf();

    // 校验:三样东西缺一不可,语义不能搞反。
    if (!raw) { MM.toast('请先在「你的原唱」处上传一段音频(跑调也没关系)。', 'error'); return; }
    if (!ref) { MM.toast('请上传「想唱成的样子」:去和声后的目标旋律(可在「拆声」里得到 clean / lead)。', 'error'); return; }
    if (!self) { MM.toast('请上传「你的声音样本」:一段 ~10–30s 的干净清唱(音色锚)。', 'error'); return; }

    var fd = new FormData();
    fd.append('raw', raw);
    fd.append('ref', ref);
    fd.append('self_ref', self);
    fd.append('correct_steps', csEl ? csEl.value : '150');
    fd.append('voice_steps', vsEl ? vsEl.value : '50');
    fd.append('voice_cfg', cfgEl ? cfgEl.value : '0.7');

    var prog = MM.progress(btn, { comfort: true, messages: COMFORT_MSGS });
    var restore = MM.setBusy(btn, '提交中…');
    prog.start();

    MM.run('vocal', fd, function (job) {
      // 长任务:用真实阶段文案刷新按钮(修音准 → 重塑音色 …)+ 进度条。
      if (job && job.stage) MM.setBusyText(btn, job.stage);
      prog.update(job);
    }).then(function (job) {
      var ok = job.status !== 'error' && job.result && job.result.ok;
      if (ok) prog.done(); else prog.fail();
      // 硬失败:任务本身报错。
      if (job.status === 'error') { MM.toast(job.error || '任务失败。', 'error'); return; }

      var r = job.result || {};

      // 软失败:done 但业务未成功(常见:GPU venv / 权重未就绪)。清楚展示 message。
      if (!r.ok) {
        MM.toast(r.message || '未产出成品。', 'error');
        // 即便软失败也可能有中间产物/可下载文件 —— 尽量展示出来。
        if (midPlayer && r.mid) {
          MM.wirePlayer(midPlayer, MM.fileUrl(job.id, r.mid), '中间产物', '只修了音准');
        }
        if (r.downloads && r.downloads.length) {
          MM.renderDownloads(ensureGrid(), job.id, r.downloads);
        }
        return;
      }

      // 成功:成品 + 中间产物两个播放器 + 下载网格。
      if (r.final) {
        var finalBase = String(r.final).split('/').pop();
        if (showpiece) {
          MM.wirePlayer(showpiece, MM.fileUrl(job.id, r.final),
            '在调了 · 干净了 · 还是你', '成品 · ' + finalBase);
        }
      } else {
        // ok 为真却无 final 的兜底:占位 + 友好报错。
        MM.toast(r.message || '未产出成品。', 'error');
      }
      if (midPlayer && r.mid) {
        MM.wirePlayer(midPlayer, MM.fileUrl(job.id, r.mid), '中间产物', '只修了音准');
      }
      MM.renderDownloads(ensureGrid(), job.id, r.downloads || []);
    }).catch(function (e) {
      prog.fail();
      MM.toast('' + e, 'error');
    }).finally(restore);
  });

  MM.clearButton(panel, function () {
    if (getRaw.clear) getRaw.clear();
    if (getRef.clear) getRef.clear();
    if (getSelf.clear) getSelf.clear();
    if (btn._mmProg) btn._mmProg.hide();
    if (dlGrid) MM.renderDownloads(dlGrid, '', []);
    var au = panel.querySelectorAll('audio');
    Array.prototype.forEach.call(au, function (a) { try { a.pause(); } catch (e) {} });
  });
})();
