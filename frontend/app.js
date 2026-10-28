/* 记账小助手前端逻辑：对话 + 看账 + 导入 + 期初 */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const ACCOUNT_OPTIONS = [];   // 加载科目表后填充

  /* ============ 多账本状态 ============ */
  // 注意命名：loadBooks() = 看账/报表页；loadBookList() = 多账本切换器，两者不同，勿混。
  let currentBookId = localStorage.getItem('bookId') || '';
  let bookList = [];
  let currentTab = 'chat';

  /* ============ 页面切换 ============ */
  const pages = {
    chat: 'page-chat', flow: 'page-flow', books: 'page-books',
    setup: 'page-setup', settings: 'page-settings',
  };
  function showTab(name) {
    currentTab = name;
    $('page-picker').classList.add('hidden');
    $('bookbar').classList.remove('hidden');
    $('tabbar').classList.remove('hidden');
    Object.keys(pages).forEach((k) => $('page-' + k).classList.toggle('hidden', k !== name));
    document.querySelectorAll('#tabbar .tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
    if (name === 'flow') loadFlow();
    if (name === 'books') loadBooks();
    if (name === 'settings') loadSettings();
    if (name === 'setup') loadSetupBalances();
  }
  document.querySelectorAll('#tabbar .tab').forEach((b) => {
    b.addEventListener('click', () => showTab(b.dataset.tab));
  });

  /* ============ 设置页：导入账单 / 操作记录 ============ */
  let settingsSec = 'import';

  function showSettingsSec(sec) {
    settingsSec = sec;
    $('sec-import').classList.toggle('hidden', sec !== 'import');
    $('sec-audit').classList.toggle('hidden', sec !== 'audit');
    $('sec-api').classList.toggle('hidden', sec !== 'api');
    document.querySelectorAll('#settings-seg .seg-btn')
      .forEach((b) => b.classList.toggle('active', b.dataset.sec === sec));
    if (sec === 'audit') loadAudit();
    if (sec === 'api') loadApiSettings();
  }

  /* ---------- API 设置 ---------- */

  async function loadApiSettings() {
    try {
      const d = await api('/api/settings/llm');
      $('api-base-url').value = d.base_url;
      $('api-model').value = d.model;
      $('api-key').value = '';
      $('api-key').placeholder = d.key_configured
        ? '已配置（' + d.key_length + ' 位），留空表示不修改'
        : '请输入 API Key';
      $('api-status').innerHTML =
        '<div class="api-row"><span>当前平台</span><b class="' +
          (d.platform === 'workbuddy' ? 'good' : 'warn') + '">' + esc(d.platform) + '</b></div>' +
        '<div class="api-row"><span>Key 状态</span><b>' +
          (d.key_configured ? '已配置（' + d.key_length + ' 位）' : '未配置') + '</b></div>' +
        '<div class="api-row"><span>配置文件</span><code>' + esc(d.env_file) + '</code></div>';
    } catch (e) {
      $('api-status').innerHTML = '<div class="empty">' + esc(e.message) + '</div>';
    }
  }

  function apiFormBody() {
    return JSON.stringify({
      base_url: $('api-base-url').value.trim(),
      model: $('api-model').value.trim(),
      api_key: $('api-key').value.trim(),   // 留空 = 后端沿用原 key
    });
  }

  $('api-save').addEventListener('click', async () => {
    const out = $('api-result');
    out.innerHTML = '<div class="empty">保存中…</div>';
    try {
      const d = await api('/api/settings/llm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: apiFormBody(),
      });
      $('api-key').value = '';
      out.innerHTML = '<div class="ok-box good">已保存并立即生效（平台：' +
        esc(d.platform) + '）</div>';
      loadApiSettings();
    } catch (e) {
      out.innerHTML = '<div class="ok-box bad">' + esc(e.message) + '</div>';
    }
  });

  $('api-test').addEventListener('click', async () => {
    const out = $('api-result');
    out.innerHTML = '<div class="empty">正在连接…</div>';
    try {
      const d = await api('/api/settings/llm/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: apiFormBody(),
      });
      if (d.ok) {
        out.innerHTML = '<div class="ok-box good">连接成功<br>平台 ' + esc(d.platform) +
          '　模型 ' + esc(d.model) + '　耗时 ' + d.latency + 's<br>回复：' + esc(d.reply) + '</div>';
      } else {
        out.innerHTML = '<div class="ok-box bad">连接失败：' + esc(d.error) + '</div>';
      }
    } catch (e) {
      out.innerHTML = '<div class="ok-box bad">' + esc(e.message) + '</div>';
    }
  });

  function loadSettings() { showSettingsSec(settingsSec); }

  $('settings-seg').addEventListener('click', (e) => {
    const b = e.target.closest('.seg-btn');
    if (b) showSettingsSec(b.dataset.sec);
  });

  /* ============ 多账本：入口页 / 切换条 / 弹层 ============ */

  function showPicker() {
    $('page-picker').classList.remove('hidden');
    $('bookbar').classList.add('hidden');
    $('tabbar').classList.add('hidden');
    Object.keys(pages).forEach((k) => $('page-' + k).classList.add('hidden'));
    renderPicker();
  }

  function currentBook() {
    return bookList.find((b) => b.id === currentBookId) || null;
  }

  function renderPicker() {
    const box = $('picker-list');
    box.innerHTML = '';
    bookList.forEach((b) => {
      const btn = document.createElement('button');
      btn.className = 'book-card';
      btn.innerHTML = '<span class="book-name"></span><span class="book-caret">›</span>';
      btn.querySelector('.book-name').textContent = b.name;
      btn.addEventListener('click', () => enterApp(b.id));
      box.appendChild(btn);
    });
  }

  function renderBookbar() {
    const b = currentBook();
    if (!b) return;
    $('bookbar-name').textContent = b.name;
  }

  function renderBookSheet() {
    const box = $('book-list');
    box.innerHTML = '';
    bookList.forEach((b) => {
      const btn = document.createElement('button');
      btn.className = 'book-item' + (b.id === currentBookId ? ' active' : '');
      btn.innerHTML = '<span class="book-name"></span>' +
        (b.id === currentBookId ? '<span class="book-check">✓</span>' : '');
      btn.querySelector('.book-name').textContent = b.name;
      btn.addEventListener('click', () => switchBook(b.id));
      box.appendChild(btn);
    });
  }

  function openSheet() {
    renderBookSheet();
    $('book-sheet').classList.remove('hidden');
  }

  function closeSheet() {
    $('book-sheet').classList.add('hidden');
    $('sheet-new-form').classList.add('hidden');
    $('sheet-new-name').value = '';
  }

  $('bookbar-btn').addEventListener('click', openSheet);
  $('sheet-mask').addEventListener('click', closeSheet);
  $('sheet-cancel').addEventListener('click', closeSheet);

  function updateExportLink() {
    $('export-link').href = '/api/ledger/export?book=' + encodeURIComponent(currentBookId);
  }

  /** 进入应用（从入口页点进来） */
  function enterApp(bookId) {
    currentBookId = bookId;
    localStorage.setItem('bookId', bookId);
    renderBookbar();
    updateExportLink();
    resetClientState();
    showTab('chat');
  }

  /** 应用内切换账本：数据全部换一套，前端状态必须重置，否则会看到上一个账本的残留 */
  function switchBook(bookId) {
    closeSheet();
    if (bookId === currentBookId) return;
    currentBookId = bookId;
    localStorage.setItem('bookId', bookId);
    renderBookbar();
    updateExportLink();
    resetClientState();
    showTab(currentTab);
  }

  function resetClientState() {
    chatEl.innerHTML = '';
    showQuick([]);
    const b = currentBook();
    addMsg('已进入「' + ((b && b.name) || currentBookId) + '」\n把平时的收支说给我听就行。', 'ai');
    showQuick(['今天卖了500块馒头', '今天买了300块面粉', '这个月赚了多少', '我还有多少现金']);
    // 导入页：清掉上一个账本的预览，避免误提交到新账本
    importRows = []; importMapping = {};
    ['mapping-card', 'preview-card', 'confirm-card'].forEach((id) => $(id).classList.add('hidden'));
    $('import-status').innerHTML = ''; $('import-result').innerHTML = '';
    ['profit-table', 'balance-table', 'trial-table', 'ledger-table', 'ob-balances',
     'flow-list', 'flow-summary'].forEach((id) => { $(id).innerHTML = '<div class="empty">加载中…</div>'; });
    $('flow-count').textContent = '';
    flowRows = []; flowRowCache = []; auditByLink = {};
    flowKind = 'all'; flowKeyword = ''; flowPeriod = 'month'; flowSort = 'desc';
    $('flow-sort').textContent = '最新在前 ↓';
    flowRange = { from: '', to: '' };
    $('flow-search').value = '';
    $('flow-search-clear').classList.add('hidden');
    $('flow-range').classList.add('hidden');
    selectChip('flow-chips', $('flow-chips').querySelector('[data-period="month"]'));
    selectChip('flow-kinds', $('flow-kinds').querySelector('[data-kind="all"]'));
    $('ob-result').innerHTML = '';
    $('audit-list').innerHTML = '<div class="empty">加载中…</div>';
    $('audit-count').textContent = '';
    $('cc-ending').value = '';
    $('cc-result').innerHTML = '';
    costClosePending = false;
  }

  async function loadBookList() {
    const d = await api('/api/books');
    bookList = d.books || [];
    if (!bookList.some((b) => b.id === currentBookId)) {
      currentBookId = d.default || (bookList[0] && bookList[0].id) || '';
    }
  }

  /* ============ 新建账本 ============ */

  /** 把一组「按钮 + 表单」接成完整的新建账本交互 */
  function wireNewBook(btnId, formId, inputId, okId, cancelId, onCreated) {
    const form = $(formId);
    const input = $(inputId);
    const ok = $(okId);
    const err = document.createElement('div');
    err.className = 'ok-box bad hidden';
    form.appendChild(err);

    const hide = () => {
      form.classList.add('hidden');
      err.classList.add('hidden');
      input.value = '';
    };
    $(btnId).addEventListener('click', () => {
      form.classList.toggle('hidden');
      err.classList.add('hidden');
      if (!form.classList.contains('hidden')) input.focus();
    });
    $(cancelId).addEventListener('click', hide);

    async function submit() {
      const name = input.value.trim();
      if (!name) { input.focus(); return; }
      ok.disabled = true;
      err.classList.add('hidden');
      try {
        const b = await api('/api/books', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name }),
        });
        await loadBookList();
        hide();
        onCreated(b);
      } catch (e) {
        err.textContent = e.message;
        err.classList.remove('hidden');
      } finally {
        ok.disabled = false;
      }
    }
    ok.addEventListener('click', submit);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
  }

  wireNewBook('picker-new', 'picker-new-form', 'picker-new-name',
    'picker-new-ok', 'picker-new-cancel',
    (b) => { renderPicker(); enterApp(b.id); });

  wireNewBook('sheet-new', 'sheet-new-form', 'sheet-new-name',
    'sheet-new-ok', 'sheet-new-cancel',
    (b) => { renderBookSheet(); switchBook(b.id); });

  // 启动：先拉账本列表，有上次用的账本就直接进，否则显示入口页
  // 注意：必须等页面里所有 const（chatEl / importRows…）都初始化完再跑，所以在文件末尾才调用。
  async function init() {
    try {
      await loadBookList();
    } catch (e) {
      $('picker-list').innerHTML = '<div class="empty">加载账本列表失败，请确认后端已启动。</div>';
      showPicker();
      return;
    }
    await loadAccounts();
    if (currentBookId && localStorage.getItem('bookId')) {
      enterApp(currentBookId);
    } else {
      showPicker();
    }
  }

  /* ============ 对话 ============ */
  const chatEl = $('chat'), inputEl = $('input'), sendBtn = $('send'), quickEl = $('quickbar');

  function addMsg(text, who) {
    const div = document.createElement('div');
    div.className = 'msg ' + who;
    div.textContent = text;
    chatEl.appendChild(div);
    chatEl.scrollTop = chatEl.scrollHeight;
    return div;
  }
  function showQuick(buttons) {
    quickEl.innerHTML = '';
    (buttons || []).forEach(function (b) {
      const btn = document.createElement('button');
      btn.className = 'chip';
      btn.textContent = b;
      btn.addEventListener('click', function () { send(b); });
      quickEl.appendChild(btn);
    });
  }

  let busy = false;
  async function send(text) {
    text = (text || '').trim();
    if (!text || busy) return;
    busy = true; sendBtn.disabled = true; inputEl.value = ''; showQuick([]);
    addMsg(text, 'user');
    const typing = addMsg('正在记账…', 'ai');
    typing.classList.add('typing');
    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        // 这里绕过了 api() 封装，必须自己带上账本，否则会记到默认账本
        headers: { 'Content-Type': 'application/json', 'X-Book-Id': currentBookId },
        body: JSON.stringify({ message: text }),
      });
      const data = await res.json();
      typing.classList.remove('typing');
      typing.textContent = data.reply || '（没有回复）';
      showQuick(data.quick_replies || []);
    } catch (e) {
      typing.classList.remove('typing');
      typing.textContent = '连接不上服务器，请确认后端已启动。';
    } finally {
      busy = false; sendBtn.disabled = false;
      chatEl.scrollTop = chatEl.scrollHeight;
      inputEl.focus();
    }
  }
  sendBtn.addEventListener('click', () => send(inputEl.value));
  inputEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') send(inputEl.value); });

  /* ============ 工具 ============ */
  function fmt(n) {
    const v = parseFloat(n);
    if (isNaN(v)) return n;
    return v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  // 所有 API 请求的统一出口 —— 在这里集中注入当前账本
  async function api(path, opts) {
    opts = opts || {};
    const headers = Object.assign({}, opts.headers || {});
    if (currentBookId) headers['X-Book-Id'] = currentBookId;   // 未选账本时不带头，交给后端走默认
    opts.headers = headers;
    const res = await fetch(path, opts);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '请求失败');
    return data;
  }

  $('goto-flow').addEventListener('click', () => showTab('flow'));

  /* ============ 流水：给看不懂会计报表的人 ============ */
  let flowPeriod = 'month';
  let flowKind = 'all';          // all | income | expense
  let flowSort = 'desc';         // desc 最新在前 | asc 最早在前
  let flowKeyword = '';
  let flowRows = [];             // 按时间范围从后端拉到的原始行
  let flowRange = { from: '', to: '' };
  let auditByLink = {};          // ^link → 溯源记录，点开某条时显示"这句话是哪来的"

  function selectChip(boxId, active) {
    document.querySelectorAll('#' + boxId + ' .chip')
      .forEach((c) => c.classList.toggle('active', c === active));
  }

  $('flow-chips').addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    flowPeriod = chip.dataset.period;
    selectChip('flow-chips', chip);
    $('flow-range').classList.toggle('hidden', flowPeriod !== 'custom');
    if (flowPeriod === 'custom' && !flowRange.from) {
      const t = new Date();
      flowRange.to = t.toISOString().slice(0, 10);
      flowRange.from = new Date(t.getFullYear(), t.getMonth(), 1).toISOString().slice(0, 10);
      $('flow-from').value = flowRange.from;
      $('flow-to').value = flowRange.to;
    }
    loadFlow();
  });

  $('flow-kinds').addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    flowKind = chip.dataset.kind;
    selectChip('flow-kinds', chip);
    renderFlow();          // 纯前端过滤，即时响应
  });

  $('flow-from').addEventListener('change', () => {
    flowRange.from = $('flow-from').value;
    loadFlow();
  });
  $('flow-to').addEventListener('change', () => {
    flowRange.to = $('flow-to').value;
    loadFlow();
  });

  let flowSearchTimer = null;
  $('flow-search').addEventListener('input', () => {
    flowKeyword = $('flow-search').value;
    $('flow-search-clear').classList.toggle('hidden', !flowKeyword);
    clearTimeout(flowSearchTimer);
    flowSearchTimer = setTimeout(renderFlow, 150);   // 防抖，边打边筛
  });
  $('flow-search-clear').addEventListener('click', () => {
    $('flow-search').value = '';
    flowKeyword = '';
    $('flow-search-clear').classList.add('hidden');
    renderFlow();
  });

  function updateFlowExport() {
    const q = flowPeriod === 'custom'
      ? 'date_from=' + flowRange.from + '&date_to=' + flowRange.to
      : 'period=' + flowPeriod;
    $('flow-export').href = '/api/ledger-rows/export.csv?' + q +
      '&book=' + encodeURIComponent(currentBookId);
  }

  async function loadFlow() {
    updateFlowExport();
    if (!Object.keys(auditByLink).length) await loadAuditMap();
    try {
      const q = flowPeriod === 'custom'
        ? 'date_from=' + flowRange.from + '&date_to=' + flowRange.to
        : 'period=' + flowPeriod;
      const d = await api('/api/ledger-rows?' + q);
      flowRows = d.rows || [];
      renderFlow();
    } catch (e) {
      $('flow-summary').innerHTML = '<div class="empty">—</div>';
      $('flow-list').innerHTML = '<div class="empty">加载失败：' + esc(e.message) + '</div>';
    }
  }

  /** 预载溯源记录，点开某条流水时能显示「这句话是哪儿来的」 */
  async function loadAuditMap() {
    try {
      const d = await api('/api/audit?limit=500');
      auditByLink = {};
      (d.rows || []).forEach((a) => {
        if (!a.entry_link) return;
        (auditByLink[a.entry_link] = auditByLink[a.entry_link] || []).push(a);
      });
    } catch (e) { auditByLink = {}; }
  }

  /** 关键词 + 类型过滤（纯前端，即时） */
  $('flow-sort').addEventListener('click', () => {
    flowSort = flowSort === 'desc' ? 'asc' : 'desc';
    $('flow-sort').textContent = flowSort === 'desc' ? '最新在前 ↓' : '最早在前 ↑';
    renderFlow();
  });

  function filteredFlowRows() {
    const kw = flowKeyword.trim().toLowerCase();
    const out = flowRows.filter((r) => {
      if (flowKind === 'income' && r.kind !== 'income') return false;
      if (flowKind === 'expense' && r.kind !== 'expense') return false;
      if (kw) {
        const hay = [r.narration, r.payee, r.date, r.time,
          (r.accounts || []).map((a) => accountNames[a] || a).join(' ')].join(' ').toLowerCase();
        if (hay.indexOf(kw) === -1) return false;
      }
      return true;
    });
    if (flowSort === 'desc') out.reverse();   // 后端按时间升序返回，倒序即「最新在前」
    return out;
  }

  const WEEKDAY = ['日', '一', '二', '三', '四', '五', '六'];

  function dayLabel(iso) {
    const p = iso.split('-').map(Number);
    const dt = new Date(p[0], p[1] - 1, p[2]);
    const now = new Date();
    const base = p[1] + '月' + p[2] + '日 周' + WEEKDAY[dt.getDay()];
    return dt.toDateString() === now.toDateString() ? base + '（今天）' : base;
  }

  let flowRowCache = [];   // 本次渲染的行，供点开时取详情

  function flowRow(r, idx) {
    const inc = parseFloat(r.income);
    const exp = parseFloat(r.expense);
    let amt;
    let cls;
    if (r.kind === 'income') { amt = '+' + fmt(inc); cls = 'in'; }
    else if (r.kind === 'expense') { amt = '-' + fmt(exp); cls = 'out'; }
    else if (r.kind === 'transfer') { amt = fmt(inc); cls = 'move'; }
    else { amt = '—'; cls = 'move'; }   // settle（期初/结转）不计收支，只作说明行

    const title = r.narration || r.payee || '一笔账';
    const tag = [acctNames(r.accounts), r.payee && r.narration ? esc(r.payee) : '']
      .filter(Boolean).join('　·　');
    const tappable = (r.links || []).some((l) => auditByLink[l]);
    return '<div class="flow-row' + (tappable ? ' tappable' : '') + '"' +
        (tappable ? ' data-idx="' + idx + '"' : '') + '>' +
      '<span class="flow-time">' + esc(r.time || '—') + '</span>' +
      '<span class="flow-main">' +
        '<span class="flow-title">' + esc(title) + '</span>' +
        (tag ? '<span class="flow-tag">' + tag + '</span>' : '') +
      '</span>' +
      '<span class="flow-amt ' + cls + '">' + amt + '</span>' +
    '</div>' +
    (tappable ? '<div class="flow-detail hidden" id="flow-detail-' + idx + '"></div>' : '');
  }

  /** 点开一条流水：显示它是从哪句话记出来的（复用审计溯源） */
  function flowDetailHtml(r) {
    const audits = [];
    (r.links || []).forEach((l) => { (auditByLink[l] || []).forEach((a) => audits.push(a)); });
    if (!audits.length) return '';
    let h = '<div class="flow-src">';
    audits.forEach((a) => {
      h += '<div class="flow-src-line">' +
        '<span class="flow-src-who">' + esc(ACTION_LABEL[a.action] || a.action) + '</span>' +
        '<span class="flow-src-text">「' + esc(a.user_text) + '」</span>' +
        '<span class="flow-src-time">' + esc((a.created_at || '').replace('T', ' ')) + '</span>' +
      '</div>';
      if (a.payload && a.payload._edits && a.payload._edits.length) {
        h += '<div class="flow-src-edit">改过：' + esc(a.payload._edits.join('；')) + '</div>';
      }
    });
    h += '<div class="flow-src-link">账本分录　^' + esc(r.links[0]) + '</div></div>';
    return h;
  }

  $('flow-list').addEventListener('click', (e) => {
    const row = e.target.closest('.flow-row.tappable');
    if (!row) return;
    const box = $('flow-detail-' + row.dataset.idx);
    if (!box) return;
    if (!box.innerHTML) box.innerHTML = flowDetailHtml(flowRowCache[row.dataset.idx]);
    box.classList.toggle('hidden');
  });

  function renderFlow() {
    const rows = filteredFlowRows();
    flowRowCache = rows;

    // 副标题显示当前统计区间，取代装饰性文案。
    // 不能直接取首尾 —— 倒序时首尾正好是反的。
    if (rows.length) {
      const dates = rows.map((r) => r.date).sort();
      $('flow-subtitle').textContent =
        dates[0] + ' 至 ' + dates[dates.length - 1] + '　共 ' + rows.length + ' 笔';
    } else {
      $('flow-subtitle').textContent = '—';
    }

    const sumIn = rows.reduce((s, r) => s + (r.kind === 'income' ? parseFloat(r.income) : 0), 0);
    const sumOut = rows.reduce((s, r) => s + (r.kind === 'expense' ? parseFloat(r.expense) : 0), 0);
    $('flow-summary').innerHTML =
      '<div class="flow-sum-item"><div class="flow-sum-label">收入</div>' +
        '<div class="flow-sum-val in">' + fmt(sumIn) + '</div></div>' +
      '<div class="flow-sum-item"><div class="flow-sum-label">支出</div>' +
        '<div class="flow-sum-val out">' + fmt(sumOut) + '</div></div>' +
      '<div class="flow-sum-item"><div class="flow-sum-label">结余</div>' +
        '<div class="flow-sum-val">' + fmt(sumIn - sumOut) + '</div></div>';

    const filtering = flowKind !== 'all' || flowKeyword.trim();
    $('flow-filter-hint').classList.toggle('hidden', !filtering);
    if (filtering) {
      $('flow-filter-hint').textContent =
        '筛选后 ' + rows.length + ' 笔 / 共 ' + flowRows.length + ' 笔';
    }
    $('flow-count').textContent = rows.length ? '共 ' + rows.length + ' 笔' : '';

    if (!rows.length) {
      $('flow-list').innerHTML = flowRows.length
        ? '<div class="empty">没有符合条件的记录</div>'
        : '<div class="empty">这段时间还没有记录</div>';
      return;
    }

    // 按天分组，每天带小计 —— 「一目了然」的关键
    const days = [];
    const byDate = {};
    rows.forEach((r, i) => {
      let g = byDate[r.date];
      if (!g) { g = { date: r.date, items: [], income: 0, expense: 0 }; byDate[r.date] = g; days.push(g); }
      g.items.push([r, i]);
      if (r.kind === 'income') g.income += parseFloat(r.income);
      if (r.kind === 'expense') g.expense += parseFloat(r.expense);
    });

    let h = '';
    days.forEach((g) => {
      const sum = [g.income ? '收 ' + fmt(g.income) : '', g.expense ? '支 ' + fmt(g.expense) : '']
        .filter(Boolean).join('　');
      h += '<div class="flow-day"><span class="flow-day-name">' + dayLabel(g.date) + '</span>' +
        '<span class="flow-day-sum">' + sum + '</span></div>';
      g.items.forEach(([r, i]) => { h += flowRow(r, i); });
    });
    $('flow-list').innerHTML = h;
  }

  /* ============ 看账 ============ */
  let booksPeriod = 'month';
  $('period-chips').addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    booksPeriod = chip.dataset.period;
    document.querySelectorAll('#period-chips .chip').forEach((c) => c.classList.toggle('active', c === chip));
    loadBooks();
  });

  function tableHtml(headers, rows, totals) {
    let h = '<table><thead><tr>' + headers.map((x) => '<th>' + x + '</th>').join('') +
      '</tr></thead><tbody>';
    rows.forEach((r) => {
      h += '<tr>' + r.map((c, i) => {
        if (i > 0 && typeof c === 'number') {
          return '<td class="num' + (c < 0 ? ' neg' : '') + '">' + fmt(c) + '</td>';
        }
        return '<td>' + c + '</td>';
      }).join('') + '</tr>';
    });
    if (totals) {
      h += '<tr class="total">' + totals.map((c, i) => (
        (i > 0 && typeof c === 'number') ? '<td class="num">' + fmt(c) + '</td>' : '<td>' + c + '</td>'
      )).join('') + '</tr>';
    }
    return h + '</tbody></table>';
  }

  /** 财务报表渲染：k = section(分节) | item(明细) | total(小计) | grand(总计) | memo(脚注) */
  function stmtTable(rows) {
    let h = '<table><thead><tr><th>项目</th><th class="num">金额（元）</th></tr></thead><tbody>';
    rows.forEach((r) => {
      const cls = ['section', 'sub', 'total', 'grand', 'memo'].indexOf(r.k) >= 0 ? r.k : '';
      const label = r.k === 'item'
        ? '<td class="' + (r.lvl === 2 ? 'indent2' : 'indent') + '">' + r.label + '</td>'
        : '<td>' + r.label + '</td>';
      let amt = '<td></td>';
      if (typeof r.amount === 'number') {
        amt = '<td class="num' + (r.amount < 0 ? ' neg' : '') + '">' + fmt(r.amount) + '</td>';
      }
      h += '<tr class="' + cls + '">' + label + amt + '</tr>';
    });
    return h + '</tbody></table>';
  }

  async function loadBooks() {
    try {
      const [inc, bs, tb] = await Promise.all([
        api('/api/income-statement?period=' + booksPeriod),
        api('/api/balance-sheet'),
        api('/api/trial-balance'),
      ]);
      renderProfit(inc);
      renderBalance(bs);
      renderTrial(tb);
      loadLedger('Assets:Cash');
      loadCostStatus();
    } catch (e) {
      $('profit-table').innerHTML = '<div class="empty">加载失败：' + e.message + '</div>';
    }
  }

  /** 利润表：按「营业收入 → 毛利 → 期间费用 → 净利润」的正式结构列示 */
  function renderProfit(inc) {
    const incomeTotal = parseFloat(inc.income_total);
    const cogs = inc.expense.find((r) => r.code === 'Expenses:COGS');
    const cogsAmt = cogs ? parseFloat(cogs.amount) : 0;
    const others = inc.expense.filter((r) => r.code !== 'Expenses:COGS');
    const othersTotal = others.reduce((s, r) => s + parseFloat(r.amount), 0);

    const rows = [{ k: 'section', label: '一、营业收入' }];
    if (inc.income.length) {
      inc.income.forEach((r) => rows.push({ k: 'item', label: r.name, amount: parseFloat(r.amount) }));
    } else {
      rows.push({ k: 'item', label: '无', amount: 0 });
    }
    rows.push({ k: 'total', label: '营业收入合计', amount: incomeTotal });

    if (cogsAmt) {
      rows.push({ k: 'item', label: '减：营业成本', amount: -cogsAmt });
      rows.push({ k: 'total', label: '二、毛利', amount: incomeTotal - cogsAmt });
    }
    rows.push({ k: 'section', label: (cogsAmt ? '三、' : '二、') + '期间费用' });
    if (others.length) {
      others.forEach((r, i) => rows.push({
        k: 'item', label: (i === 0 ? '减：' : '　　　') + r.name, amount: -parseFloat(r.amount),
      }));
    } else {
      rows.push({ k: 'item', label: '无', amount: 0 });
    }
    rows.push({ k: 'total', label: '期间费用合计', amount: -othersTotal });
    rows.push({ k: 'grand', label: '净利润', amount: parseFloat(inc.net_profit) });
    rows.push({ k: 'memo', label: '编制单位　' + (currentBook() || {}).name + '　　会计期间　' +
      inc.date_from + ' 至 ' + inc.date_to + '　　单位：元' });
    $('profit-table').innerHTML = stmtTable(rows);
  }

  /** 资产负债表：按流动性分组（流动资产 / 非流动资产 / 流动负债 / 非流动负债） */
  function renderBalance(bs) {
    function group(title, list) {
      const out = [];
      if (!list || !list.length) return out;
      out.push({ k: 'sub', label: title });
      list.forEach((r) => out.push({ k: 'item', lvl: 2, label: r.name, amount: parseFloat(r.amount) }));
      out.push({
        k: 'total', label: title + '合计',
        amount: list.reduce((s, r) => s + parseFloat(r.amount), 0),
      });
      return out;
    }

    const rows = [{ k: 'section', label: '资产' }];
    rows.push.apply(rows, group('流动资产', bs.current_assets));
    rows.push.apply(rows, group('非流动资产', bs.non_current_assets));
    rows.push({ k: 'grand', label: '资产总计', amount: parseFloat(bs.assets_total) });

    rows.push({ k: 'section', label: '负债' });
    rows.push.apply(rows, group('流动负债', bs.current_liabilities));
    rows.push.apply(rows, group('非流动负债', bs.non_current_liabilities));
    if (!bs.liabilities.length) rows.push({ k: 'item', lvl: 2, label: '无', amount: 0 });
    rows.push({ k: 'total', label: '负债合计', amount: parseFloat(bs.liabilities_total) });

    rows.push({ k: 'section', label: '所有者权益' });
    bs.equity.forEach((r) => rows.push({ k: 'item', lvl: 2, label: r.name, amount: parseFloat(r.amount) }));
    rows.push({ k: 'item', lvl: 2, label: '未分配利润', amount: parseFloat(bs.undistributed_profit) });
    rows.push({ k: 'total', label: '所有者权益合计', amount: parseFloat(bs.equity_total) });

    const right = parseFloat(bs.liabilities_total) + parseFloat(bs.equity_total);
    rows.push({ k: 'grand', label: '负债和所有者权益总计', amount: right });

    const diff = parseFloat(bs.assets_total) - right;
    const ok = Math.abs(diff) < 0.005;
    const h = stmtTable(rows) +
      '<div class="check' + (ok ? '' : ' bad') + '">' +
      '资产总计　' + fmt(bs.assets_total) + '　　负债和所有者权益总计　' + fmt(right) +
      '　　差额　' + fmt(diff) + '</div>';
    $('balance-table').innerHTML = h;
  }

  function renderTrial(tb) {
    const rows = tb.rows.map((r) => [
      r.name,
      parseFloat(r.debit), parseFloat(r.credit), parseFloat(r.balance),
    ]);
    let d = 0, c = 0, b = 0;
    rows.forEach((r) => { d += r[1]; c += r[2]; b += r[3]; });
    $('trial-table').innerHTML = tableHtml(['科目', '借方', '贷方', '余额'], rows, ['合计', d, c, b]) +
      '<div class="check' + (Math.abs(d - c) < 0.005 ? '' : ' bad') + '">' +
      '借方合计　' + fmt(d) + '　　贷方合计　' + fmt(c) + '　　差额　' + fmt(d - c) + '</div>';
  }

  async function loadLedger(account) {
    const chips = $('ledger-chips');
    chips.innerHTML = '';
    ['Assets:Cash', 'Assets:Bank', 'Assets:Inventory', 'Expenses:Misc'].forEach((acc) => {
      const b = document.createElement('button');
      b.className = 'chip' + (acc === account ? ' active' : '');
      b.textContent = acc.replace('Assets:', '').replace('Expenses:', '');
      b.addEventListener('click', () => loadLedger(acc));
      chips.appendChild(b);
    });
    try {
      const d = await api('/api/account-ledger?account=' + account);
      const rows = d.rows.slice(-30).map((r) => [
        r.date, r.narration || (r.payee || ''),
        parseFloat(r.amount), parseFloat(r.balance),
      ]);
      $('ledger-table').innerHTML = d.rows.length
        ? tableHtml(['日期', '摘要', '发生额', '余额'], rows)
        : '<div class="empty">这个科目还没有记录</div>';
    } catch (e) {
      $('ledger-table').innerHTML = '<div class="empty">' + e.message + '</div>';
    }
  }

  /* ============ 审计溯源 ============ */

  // 科目代码 → 中文名（加载一次，缓存）
  let accountNames = {};
  async function loadAccounts() {
    try {
      const tree = await api('/api/accounts');
      Object.keys(tree).forEach((kind) => {
        (tree[kind] || []).forEach((a) => { accountNames[a.code] = a.name; });
      });
    } catch (e) { /* 拿不到就退回显示代码，不影响主流程 */ }
  }

  const ACTION_LABEL = {
    record: '记账', correct: '纠错', import: '批量导入',
    cost_close: '成本结转', opening: '期初结转',
  };
  const STATUS_LABEL = {
    committed: '已入账', cancelled: '已取消', failed: '失败', pending: '处理中',
  };

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function acctNames(codes) {
    return (codes || []).map((c) => esc(accountNames[c] || c)).join(' / ');
  }

  function renderAudit(rows) {
    $('audit-count').textContent = rows.length ? '共 ' + rows.length + ' 条' : '';
    if (!rows.length) {
      $('audit-list').innerHTML = '<div class="empty">还没有操作记录</div>';
      return;
    }
    let h = '';
    rows.forEach((r) => {
      const action = ACTION_LABEL[r.action] || r.action;
      const status = STATUS_LABEL[r.status] || r.status;
      const meta = [];
      if (r.accounts && r.accounts.length) meta.push(acctNames(r.accounts));
      if (r.amount) meta.push('金额 <span class="amt">' + fmt(r.amount) + '</span>');
      if (r.narration) meta.push('摘要 ' + esc(r.narration));
      if (r.entry_link) meta.push('分录 <span class="audit-link">^' + esc(r.entry_link) + '</span>');

      h += '<div class="audit-item">' +
        '<div class="audit-head">' +
          '<span class="audit-id">#' + r.id + '</span>' +
          '<span class="audit-action">' + esc(action) + '</span>' +
          '<span class="audit-status ' + esc(r.status) + '">' + esc(status) + '</span>' +
          '<span class="audit-time">' + esc((r.created_at || '').replace('T', ' ')) + '</span>' +
        '</div>' +
        '<div class="audit-text">' + esc(r.user_text) + '</div>' +
        '<div class="audit-meta">' + meta.join('　·　') + '</div>' +
      '</div>';
    });
    $('audit-list').innerHTML = h;
  }

  async function loadAudit() {
    try {
      const d = await api('/api/audit?limit=200');
      renderAudit(d.rows);
    } catch (e) {
      $('audit-list').innerHTML = '<div class="empty">' + esc(e.message) + '</div>';
    }
  }

  /* ============ 月末成本结转（定期盘存法） ============ */
  let costClosePending = false;

  /** 把「软件算出来的存货」摊开：账上应该有多少货 */
  async function loadCostStatus() {
    try {
      const d = await api('/api/cost-close/status');
      const rows = [
        { k: 'item', label: '上月结余的存货', amount: parseFloat(d.opening) },
        // 结转过的月份这里会是负数（等于本月转出的成本），所以叫"增减"而不是"进货"
        { k: 'item', label: '本月存货增减', amount: parseFloat(d.purchase_net) },
        { k: 'total', label: '账上现在有这么多货', amount: parseFloat(d.available) },
      ];
      if (d.last_close) {
        rows.push({
          k: 'memo',
          label: '本月已结转 ' + d.last_close.count + ' 次，累计转出 ' +
            fmt(d.last_close.total_cost) + '（最近的 ' + d.last_close.date +
            '）—— 上面的"增减"里已经含了这些',
        });
      } else {
        rows.push({ k: 'memo', label: '统计期间　' + d.date_from + ' 至 ' + d.date_to });
      }
      $('cc-status').innerHTML = stmtTable(rows);
    } catch (e) {
      $('cc-status').innerHTML = '<div class="empty">' + esc(e.message) + '</div>';
    }
  }

  function costCloseHtml(d, done) {
    // 措辞与上方状态面板保持一致；一列数字能直接相加，不做"减：X 又显示负数"的双重否定
    const again = d.last_close && d.last_close.count > 0;
    const rows = [
      { k: 'item', label: '上月结余的存货', amount: parseFloat(d.opening) },
      { k: 'item', label: '本月存货增减', amount: parseFloat(d.purchase_net) },
      { k: 'total', label: '账上有这么多货', amount: parseFloat(d.available) },
      { k: 'item', label: '你数出来的', amount: -parseFloat(d.ending) },
      // 第二次结转算的是"从上次结转之后到现在"这一段，不是整月，标签必须区分
      { k: 'grand', label: again ? '本次要结转的成本' : '本月卖掉的货的成本',
        amount: parseFloat(d.cost) },
      { k: 'memo', label: '结转期间　' + d.date_from + ' 至 ' + d.date_to },
    ];
    const cost = parseFloat(d.cost);
    let h = stmtTable(rows);
    if (done) {
      h += cost < 0
        ? '<div class="check">已冲回　借 存货／贷 营业成本　' + fmt(-cost) + ' 元</div>'
        : '<div class="check">已结转　借 营业成本／贷 存货　' + fmt(cost) + ' 元</div>';
    } else if (cost === 0) {
      // 成本和账上一致 → 没什么可转的，不给"确认结转"按钮（点了后端也会拒绝）
      h += '<div class="check">盘点数和账上一致，本月没有可结转的成本' +
        (d.last_close ? '（本月已经结转过）' : '') + '。</div>';
    } else if (cost < 0) {
      // 盘点数比账上多 → 说明之前转多了，本次是冲回，不是报错
      h += '<div class="check">盘点数比账上多 ' + fmt(-cost) + ' 元，说明之前把成本转多了' +
        '（多半是上次盘点估低了）。<br>本次会冲回：借 存货／贷 营业成本　' + fmt(-cost) + ' 元' +
        '<br>如果其实是<b>有进货没记账</b>，建议先去「记账」把进货补上再结转。</div>' +
        '<div class="btn-row"><button class="btn primary" id="cc-confirm">确认冲回</button></div>';
    } else {
      h += '<div class="check">待确认分录　借 营业成本／贷 存货　' + fmt(cost) + ' 元</div>' +
        '<div class="btn-row"><button class="btn primary" id="cc-confirm">确认结转</button></div>';
    }
    return h;
  }

  $('cc-preview').addEventListener('click', async () => {
    const box = $('cc-result');
    box.innerHTML = '<div class="empty">试算中…</div>';
    try {
      const d = await api('/api/cost-close/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ending_inventory: parseFloat($('cc-ending').value || '0') }),
      });
      costClosePending = true;
      box.innerHTML = costCloseHtml(d, false);
      $('cc-confirm').addEventListener('click', commitCostClose);
    } catch (e) {
      costClosePending = false;
      box.innerHTML = '<div class="ok-box bad">' + e.message + '</div>';
    }
  });

  async function commitCostClose() {
    if (!costClosePending) return;
    const box = $('cc-result');
    try {
      const d = await api('/api/cost-close/commit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ending_inventory: parseFloat($('cc-ending').value || '0') }),
      });
      costClosePending = false;
      box.innerHTML = costCloseHtml(d, true);
      loadBooks();   // 结转后存货变了，整页刷新
    } catch (e) {
      box.innerHTML = '<div class="ok-box bad">' + e.message + '</div>';
    }
  }

  /* ============ 导入 ============ */
  let importRows = [], importMapping = {};

  $('file-input').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const status = $('import-status');
    status.innerHTML = '<div class="ok-box">正在解析文件…（首次会调用 AI 智能分类，约几秒）</div>';
    try {
      const fd = new FormData();
      fd.append('file', file);
      const d = await api('/api/import/preview', { method: 'POST', body: fd });
      importRows = d.rows;
      importMapping = d.mapping;
      if (!importRows.length) {
        status.innerHTML = '<div class="ok-box bad">' + (d.note || '没有识别到记录') + '</div>';
        return;
      }
      let warn = '';
      (d.warnings || []).forEach((w) => { warn += '<div>· ' + w + '</div>'; });
      (d.skipped || []).slice(0, 3).forEach((s) => { warn += '<div>跳过第' + s.row_no + '行：' + s.reason + '</div>'; });
      status.innerHTML = '<div class="ok-box good">解析出 <b>' + d.total + '</b> 笔' +
        (d.skip_count ? '（跳过 ' + d.skip_count + ' 行）' : '') + '</div>' + warn;
      renderMapping();
      renderPreview(d.preview_rows);
      $('mapping-card').classList.remove('hidden');
      $('preview-card').classList.remove('hidden');
      $('confirm-card').classList.remove('hidden');
      $('import-result').innerHTML = '';
    } catch (err) {
      status.innerHTML = '<div class="ok-box bad">' + err.message + '</div>';
    }
  });

  function renderMapping() {
    const keys = Object.keys(importMapping);
    $('import-summary').textContent = 'AI 已经自动认出这些分类，请核对，不对的改一下：';
    let h = '<table><tr><th>方向</th><th>分类</th><th>记到科目</th></tr>';
    keys.forEach((key) => {
      const [dir, cat] = key.split(':');
      const dirlabel = dir === 'income' ? '收入' : dir === 'transfer' ? '转账' : '支出';
      h += '<tr><td>' + dirlabel + '</td><td>' + cat + '</td><td>' +
        '<select data-key="' + key + '">' +
        (dir === 'income'
          ? '<option value="Income:Sales">销售收入</option><option value="Income:Other">其他收入</option>'
          : '<option value="Assets:Inventory">存货(进货)</option><option value="Expenses:COGS">营业成本</option>' +
            '<option value="Expenses:Rent">租金</option><option value="Expenses:Utilities">水电</option>' +
            '<option value="Expenses:Salary">工资</option><option value="Expenses:Tax">税费</option>' +
            '<option value="Expenses:Misc">杂费</option>') +
        '</select></td></tr>';
    });
    h += '</table>';
    $('mapping-table').innerHTML = h;
    // 设置已选值
    document.querySelectorAll('#mapping-table select').forEach((sel) => {
      sel.value = importMapping[sel.dataset.key] || sel.value;
      sel.addEventListener('change', () => { importMapping[sel.dataset.key] = sel.value; });
    });
  }

  function renderPreview(rows) {
    let h = '<table><tr><th>日期</th><th>方向</th><th>分类</th><th>对方</th><th>金额</th></tr>';
    rows.forEach((r) => {
      h += '<tr><td>' + (r.date || '—') + '</td><td>' + (r.direction === 'income' ? '收' : r.direction === 'transfer' ? '转' : '支') +
        '</td><td>' + r.category + '</td><td>' + (r.payee || '') + '</td><td>' + fmt(r.amount) + '</td></tr>';
    });
    $('preview-table').innerHTML = h;
  }

  $('import-commit').addEventListener('click', async () => {
    const btn = $('import-commit');
    btn.disabled = true;
    try {
      const d = await api('/api/import/commit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rows: importRows, mapping: importMapping }),
      });
      $('import-result').innerHTML = '<div class="ok-box ' + (d.failed ? 'bad' : 'good') + '">' +
        '成功导入 <b>' + d.imported + '</b> 笔' + (d.failed ? '，失败 ' + d.failed + ' 笔：' + JSON.stringify(d.errors[0]) : '') + '</div>';
      loadBooks();
    } catch (err) {
      $('import-result').innerHTML = '<div class="ok-box bad">' + err.message + '</div>';
    } finally {
      btn.disabled = false;
    }
  });

  /* ============ 期初余额 ============ */
  $('ob-date').value = new Date().toISOString().slice(0, 10);

  $('ob-save').addEventListener('click', async () => {
    const body = {
      date: $('ob-date').value,
      cash: parseFloat($('ob-cash').value || '0'),
      bank: parseFloat($('ob-bank').value || '0'),
      inventory: parseFloat($('ob-inventory').value || '0'),
      receivables: parseFloat($('ob-receivable').value || '0'),
      payable: parseFloat($('ob-payable').value || '0'),
      loan: parseFloat($('ob-loan').value || '0'),
    };
    const out = $('ob-result');
    try {
      const d = await api('/api/opening-balance', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      out.innerHTML = '<div class="ok-box good">期初余额已入账：资产 ' + fmt(d.asset_total) +
        ' 元，负债 ' + fmt(d.liability_total) + ' 元</div>';
      loadSetupBalances();
      loadBooks();
    } catch (err) {
      out.innerHTML = '<div class="ok-box bad">' + err.message + '</div>';
    }
  });

  async function loadSetupBalances() {
    try {
      const d = await api('/api/balances');
      const rows = Object.entries(d.balances).map(([k, v]) => [
        k.replace('Assets:', '').replace('Liabilities:', '').replace('Equity:', ''),
        parseFloat(v),
      ]);
      $('ob-balances').innerHTML = tableHtml(['账户', '余额（元）'], rows);
    } catch (e) {
      $('ob-balances').innerHTML = '<div class="empty">' + e.message + '</div>';
    }
  }

  /* ============ 启动 ============ */
  init();
})();
