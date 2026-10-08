/* Read-only diagnostics for stored world relations with absent endpoints. */
(function (root) {
  'use strict';
  const $ = id => document.getElementById(id);
  const dialog = $('relationDiagnostics');
  if (!dialog) return;
  const pageSize = 50;
  const origins = {verified_fact:'已验证事实（记录标注）', user_statement:'用户陈述',
    working_model:'工作模型', hypothesis:'假设', assistant_inference:'助手推断',
    tool_observation:'工具观察', simulation:'模拟结果', unknown:'未知 / 历史未标注'};
  let version = 0, controller, offset = 0, nextOffset = null, busy = false;
  let detailVersion = 0, detailController, detailTarget, detailOpener;

  function hideDetail(restoreFocus = false) {
    ++detailVersion;
    detailController?.abort();
    $('diagnosticRecord').hidden = true;
    $('diagnosticRecordBody').replaceChildren();
    $('diagnosticItems').hidden = false;
    $('diagnosticPagination').hidden = false;
    if (restoreFocus && detailOpener?.isConnected) detailOpener.focus({preventScroll: true});
    detailTarget = null;
    detailOpener = null;
  }
  function recordButton(label, tier, recordId) {
    const button = element('button', label, 'diagnostic-record-link');
    button.type = 'button';
    button.onclick = () => {
      detailOpener = button;
      loadRecord({tier, recordId});
    };
    return button;
  }
  async function loadRecord(target) {
    detailTarget = target;
    const currentVersion = ++detailVersion;
    detailController?.abort();
    const request = detailController = new AbortController();
    const timeout = setTimeout(() => request.abort(), 15000);
    $('diagnosticItems').hidden = true;
    $('diagnosticPagination').hidden = true;
    $('diagnosticRecord').hidden = false;
    $('diagnosticRecordTitle').textContent = target.tier === 'S5' ? '来源事件' : '端点实体';
    $('diagnosticRecordId').textContent = target.recordId;
    $('diagnosticRecordBody').replaceChildren();
    $('diagnosticRecordStatus').textContent = '正在读取原始记录…';
    $('diagnosticRecordRetry').disabled = true;
    $('diagnosticRecordBody').setAttribute('aria-busy', 'true');
    $('diagnosticRecordTitle').focus();
    try {
      const response = await root.EvaHttp.request('/api/memory/graph/node?' +
        new URLSearchParams({tier: target.tier, record_id: target.recordId}),
      {signal: request.signal, cache: 'no-store'});
      if (detailVersion !== currentVersion || !dialog.open) return;
      if (response.status === 404) {
        $('diagnosticRecordStatus').textContent = target.tier === 'S5'
          ? '未找到该来源事件。关系中的引用仍保留，现有信息不足，无法判断记录缺失的原因。'
          : '该实体当前已不存在，可能在诊断列表读取后发生变化。';
        return;
      }
      if (!response.ok) throw new Error(`读取失败（HTTP ${response.status}）`);
      const node = await response.json();
      if (detailVersion !== currentVersion || !dialog.open) return;
      const body = $('diagnosticRecordBody');
      body.append(element('h4', node.label), element('p', '记录类型：' + node.kind),
        element('p', '记录时间：' + (node.timestamp || '未记录')),
        element('p', '来源状态：' + (origins[node.provenance?.epistemic_status] || origins.unknown)),
        element('p', '记录来源：' + (node.source || '未标注')),
        element('pre', node.content || '（无正文）'));
      const provenance = element('details', '');
      provenance.append(element('summary', '来源与字段记录'), element('pre',
        JSON.stringify({provenance: node.provenance, field_provenance: node.field_provenance}, null, 2)));
      body.append(provenance);
      $('diagnosticRecordStatus').textContent = node.content_truncated
        ? '内容超过显示上限，当前为截断视图。' : '已读取原始记录。记录内容与来源标注不等同于事实证明。';
    } catch (error) {
      if (detailVersion === currentVersion && dialog.open) {
        $('diagnosticRecordStatus').textContent = (error.name === 'AbortError' ? '读取超时' : error.message) + '。可重新读取或返回关系列表。';
      }
    } finally {
      clearTimeout(timeout);
      if (detailVersion === currentVersion) {
        $('diagnosticRecordRetry').disabled = false;
        $('diagnosticRecordBody').setAttribute('aria-busy', 'false');
      }
    }
  }

  function controls() {
    $('diagnosticPrevious').disabled = busy || offset === 0;
    $('diagnosticNext').disabled = busy || nextOffset === null;
    $('diagnosticRetry').disabled = busy;
    $('diagnosticItems').setAttribute('aria-busy', String(busy));
  }
  function element(tag, value, className) {
    const node = document.createElement(tag);
    node.textContent = value;
    if (className) node.className = className;
    return node;
  }
  function endpoint(label, node) {
    const row = element('div', '', 'diagnostic-endpoint');
    row.append(element('strong', label + (node.missing ? ' · 实体缺失' : ' · 实体存在')));
    if (!node.missing && node.label) row.append(element('span', node.label));
    row.append(element('code', node.record_id));
    if (node.missing) row.classList.add('missing');
    else row.append(recordButton('查看实体', 'S4', node.record_id));
    return row;
  }
  function render(items) {
    const fragment = document.createDocumentFragment();
    for (const item of items) {
      const card = element('article', '', 'diagnostic-card');
      card.append(element('h3', item.relation), endpoint('起点', item.source), endpoint('终点', item.target));
      const origin = item.provenance || {};
      card.append(element('p', '来源状态：' + (origins[origin.epistemic_status] || origins.unknown)));
      card.append(element('p', '记录来源：' + (origin.source || '未标注')));
      card.append(element('p', '来源事件 ID：' + (origin.source_event_id || '未标注')));
      card.append(element('p', '记录时间：' + (item.timestamp || '未记录')));
      if (origin.source_event_id) card.append(recordButton('查看来源事件', 'S5', origin.source_event_id));
      fragment.append(card);
    }
    $('diagnosticItems').replaceChildren(fragment);
    $('diagnosticItems').scrollTop = 0;
  }
  async function load(pageOffset = 0) {
    hideDetail();
    const currentVersion = ++version;
    controller?.abort();
    const request = controller = new AbortController();
    const timeout = setTimeout(() => request.abort(), 15000);
    offset = pageOffset;
    nextOffset = null;
    busy = true;
    controls();
    $('diagnosticItems').replaceChildren();
    $('diagnosticStatus').textContent = '正在检查关系…';
    try {
      const page = await root.EvaHttp.json('/api/memory/graph/dangling?' +
        new URLSearchParams({limit: String(pageSize), offset: String(pageOffset)}),
      {signal: request.signal, cache: 'no-store'});
      if (version !== currentVersion || !dialog.open) return;
      nextOffset = page.next_offset;
      render(page.items);
      $('diagnosticStatus').textContent = page.total === 0
        ? '未发现缺少端点实体的世界关系。'
        : page.items.length
          ? `共 ${page.total.toLocaleString()} 条 · 当前 ${page.offset + 1}–${page.offset + page.items.length} 条`
          : '当前页已无记录，数据可能已变化。请返回上一页或重新读取。';
    } catch (error) {
      if (version === currentVersion && dialog.open) {
        $('diagnosticStatus').textContent = (error.name === 'AbortError' ? '读取超时' : error.message) + '。请重新读取。';
      }
    } finally {
      clearTimeout(timeout);
      if (version === currentVersion) { busy = false; controls(); }
    }
  }
  $('openDiagnostics').onclick = () => { dialog.showModal(); load(); };
  $('closeDiagnostics').onclick = () => dialog.close();
  dialog.addEventListener('close', () => { ++version; controller?.abort(); hideDetail(); });
  $('diagnosticRecordBack').onclick = () => hideDetail(true);
  $('diagnosticRecordRetry').onclick = () => { if (detailTarget) loadRecord(detailTarget); };
  $('diagnosticPrevious').onclick = () => load(Math.max(0, offset - pageSize));
  $('diagnosticNext').onclick = () => { if (nextOffset !== null) load(nextOffset); };
  $('diagnosticRetry').onclick = () => load();
})(globalThis);
