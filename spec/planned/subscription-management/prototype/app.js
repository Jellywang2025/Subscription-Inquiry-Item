'use strict';

const providers = {
  codex: { name: 'Codex', mark: 'C' },
  deepseek: { name: 'DeepSeek', mark: 'D', keyLabel: 'DeepSeek API Key' },
  mimo: { name: 'MiMo', mark: 'M', keyLabel: 'MiMo SK' },
};
const sampleAccounts = [
  { id: 'codex-work', provider: 'codex', name: 'Codex 工作号', plan: 'ChatGPT Pro', amount: '200', currency: 'USD', cycle: '每月', renewal: '2026-12-08', short: 76, long: 42, updated: '16:15', connection: 'connected' },
  { id: 'codex-personal', provider: 'codex', name: 'Codex 个人号', plan: 'ChatGPT Plus', amount: '20', currency: 'USD', cycle: '每月', renewal: '2026-12-18', short: 92, long: 68, updated: '16:15', connection: 'connected' },
  { id: 'deepseek-work', provider: 'deepseek', name: 'DeepSeek 工作号', keyTail: '7a9c', balances: [{ value: '86.40', currency: 'CNY', description: '充值 ¥80.00 · 赠金 ¥6.40' }], updated: '16:14', connection: 'connected' },
  { id: 'mimo-personal', provider: 'mimo', name: 'MiMo 个人号', keyTail: '11de', balances: [{ value: '128.60', currency: 'CNY', description: '现金 ¥100.00 · 赠金 ¥28.60' }], updated: '16:14', connection: 'connected' },
];
const initial = new URLSearchParams(location.search);
const demo = initial.get('demo') || '';
let accounts = demo === 'empty' ? [] : structuredClone(sampleAccounts);
if (demo === 'failure') {
  accounts[0].failed = true;
  accounts[2].failed = true;
  accounts[3].failed = true;
  delete accounts[3].updated;
}
let refreshInterval = '15';
const authStates = {};
const workspace = document.querySelector('#workspace');
const confirmDialog = document.querySelector('#confirm');
const announcement = document.querySelector('#announcement');
const validViews = ['overview', 'settings', 'edit', 'auth'];
let cancellingFocus;

function escapeHtml(value = '') {
  const entities = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return String(value).replace(/[&<>"']/g, (character) => entities[character]);
}
const h = escapeHtml;

function readRoute() {
  const query = new URLSearchParams(location.search);
  const view = query.get('view');
  const platform = query.get('platform');
  return {
    view: validViews.includes(view) ? view : 'overview',
    platform: Object.hasOwn(providers, platform) ? platform : '',
    q: query.get('q') || '',
    id: query.get('id') || '',
    provider: Object.hasOwn(providers, query.get('provider')) ? query.get('provider') : 'codex',
    step: query.get('step') || '',
  };
}
let route = readRoute();
let settingsFilters = { platform: '', q: '' };
if (route.view === 'settings') settingsFilters = { platform: route.platform, q: route.q };

function href(values) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value) query.set(key, value);
  }
  if (demo) query.set('demo', demo);
  return `?${query}`;
}

function navigate(next) {
  history.pushState(null, '', href(next));
  route = readRoute();
  if (route.view === 'settings') settingsFilters = { platform: route.platform, q: route.q };
  render();
  window.scrollTo(0, 0);
  workspace.querySelector('h1')?.focus({ preventScroll: true });
}

function announce(message) {
  announcement.textContent = '';
  requestAnimationFrame(() => { announcement.textContent = message; });
}

function backLink() {
  return `<a class="back" href="${h(href({ view: 'settings', ...settingsFilters }))}" data-back>←　返回配置管理</a>`;
}

function heading(title, description, actions = '') {
  return `<div class="page-head"><div><h1 tabindex="-1">${h(title)}</h1><p>${h(description)}</p></div>${actions}</div>`;
}

function platformOptions(current, includeAll = false) {
  return `${includeAll ? '<option value="">全部平台</option>' : ''}${Object.entries(providers).map(([id, value]) =>
    `<option value="${id}" ${current === id ? 'selected' : ''}>${value.name}</option>`
  ).join('')}`;
}

function toolbar() {
  return `<div class="toolbar">
    <div class="filters">
      <label class="search"><span class="sr-only">搜索账号名称</span><input class="field" id="search" type="search" autocomplete="off" placeholder="搜索账号名称" value="${h(route.q)}"></label>
      <label><span class="sr-only">按平台筛选</span><select class="field" id="platform-filter">${platformOptions(route.platform, true)}</select></label>
    </div>
    <span class="result-count" id="count"></span>
  </div>`;
}

function visibleAccounts() {
  const term = route.q.trim().toLocaleLowerCase();
  return accounts.filter((account) =>
    (!route.platform || account.provider === route.platform) &&
    account.name.toLocaleLowerCase().includes(term)
  );
}

function status(account) {
  if (account.failed) return '<span class="status issue">刷新失败</span>';
  if (account.connection !== 'connected') return '<span class="status issue">待授权</span>';
  return '<span class="status">已连接</span>';
}

function glyph(account) {
  return `<span class="glyph" aria-hidden="true">${providers[account.provider].mark}</span>`;
}

function showTime(account) {
  return account.updated ? `${account.failed ? '上次成功' : '已更新'} ${h(account.updated)}` : '等待首次更新';
}

function money(currency, value) {
  const signs = { CNY: '¥', USD: '$' };
  return `${signs[currency] || ''}${value}`;
}

function quotaMetric(label, value, reset) {
  return `<div class="metric">
    <div class="metric-label">${label}</div><strong>${value}%</strong>
    <div class="progress" role="progressbar" aria-label="${label}" aria-valuenow="${value}"><span style="width:${value}%"></span></div>
    <small>${reset} 重置</small>
  </div>`;
}

function card(account) {
  const isCodex = account.provider === 'codex';
  const tone = account.id === 'codex-work' ? 'dark' : account.provider === 'mimo' ? 'soft' : '';
  let metrics;
  let detail;
  if (isCodex) {
    metrics = account.updated
      ? quotaMetric('5 小时窗口剩余', account.short, '18:40') + quotaMetric('每周窗口剩余', account.long, '11 月 30 日 09:00')
      : '<div class="metric bad"><strong>待授权</strong><small>从配置管理完成连接后查看额度</small></div>';
    detail = `<div>套餐　${h(account.plan || '未填写')}</div><div>费用　${account.amount ? h(money(account.currency, account.amount) + ' / ' + account.cycle) : '未填写'}</div><div>续费日　${h(account.renewal || '未填写')}</div>`;
  } else {
    metrics = account.updated
      ? account.balances.map((balance) => `<div class="metric"><div class="metric-label">可用余额 · ${h(balance.currency)}</div><strong>${h(money(balance.currency, balance.value))}</strong></div>`).join('')
      : '<div class="metric bad"><strong>暂无余额</strong><small>查询超时 · 可立即刷新</small></div>';
    detail = account.updated ? account.balances.map((balance) => `<div>${h(balance.description)}</div>`).join('') : '<div>暂无余额明细</div>';
  }
  if (account.failed && account.updated) detail += '<div>请求超时 · 显示上次成功数据</div>';
  return `<article class="card ${tone}">
    <div class="card-head"><div class="account">${glyph(account)}<h3>${h(account.name)}</h3></div>${status(account)}</div>
    <div class="metric-row">${metrics}</div>
    <div class="card-footer"><details><summary>查看明细</summary><div class="detail-list">${detail}</div></details><span>${showTime(account)}</span></div>
  </article>`;
}

function group(id, items) {
  if (!items.length) return '';
  return `<section aria-label="${providers[id].name}"><div class="section-title"><h2>${providers[id].name}</h2><span class="count">${items.length} 个账号</span></div><div class="cards">${items.map(card).join('')}</div></section>`;
}

function overview(items) {
  const codex = group('codex', items.filter((account) => account.provider === 'codex'));
  const balances = ['deepseek', 'mimo'].map((id) => group(id, items.filter((account) => account.provider === id))).join('');
  return `${codex}${balances ? `<div class="balance-groups">${balances}</div>` : ''}`;
}

function listRows(items) {
  return `<div class="list-wrap">
    <div class="table-head" aria-hidden="true"><span>账号名称</span><span class="secondary-cell">平台</span><span>连接状态</span><span class="secondary-cell">续费日／密钥</span><span>操作</span></div>
    <ul class="account-list">${items.map((account) => {
      const platform = providers[account.provider].name;
      const info = account.provider === 'codex' ? account.renewal || '未填写' : `•••• ${h(account.keyTail)}`;
      return `<li>
        <div class="account-line">
          <div class="account">${glyph(account)}<b>${h(account.name)}</b></div>
          <span class="secondary-cell muted">${platform}</span>
          <div>${status(account)}</div>
          <span class="secondary-cell muted">${h(info)}</span>
          <div class="actions" aria-label="${h(account.name)}的操作">
            ${account.provider === 'codex' ? `<a class="button quiet" href="${h(href({ view: 'auth', id: account.id }))}" data-link>${account.connection === 'connected' ? '重新授权' : '去授权'}</a>` : ''}
            <a class="button" href="${h(href({ view: 'edit', id: account.id }))}" data-link>编辑</a>
            <button class="button quiet" data-remove="${account.id}">删除</button>
          </div>
        </div>
        <details class="line-details"><summary>账号明细</summary><div class="detail-list muted"><div>平台　${platform}</div><div>${account.provider === 'codex' ? '续费日' : '密钥'}　${h(info)}</div></div></details>
      </li>`;
    }).join('')}</ul>
  </div>`;
}

function renderResults() {
  const items = visibleAccounts();
  workspace.querySelector('#count').textContent = `${items.length} 个账号`;
  let body;
  if (!items.length) {
    body = accounts.length
      ? '<section class="empty"><h2>没有匹配的订阅</h2><p>试试其他账号名称，或清除筛选条件。</p><button class="button" data-clear>清除条件</button></section>'
      : '<section class="empty"><h2>还没有订阅</h2><p>添加账号后，这里会显示额度与余额。</p><button class="button primary" data-add>添加第一条订阅</button></section>';
  } else {
    body = route.view === 'settings' ? listRows(items) : overview(items);
  }
  workspace.querySelector('#results').innerHTML = body;
}

function landing() {
  const settings = route.view === 'settings';
  const actions = `<div class="actions">${settings ? '' : '<button class="button" id="refresh">↻　立即刷新</button>'}<button class="button primary" data-add>＋　添加订阅</button></div>`;
  workspace.innerHTML = heading(
    settings ? '配置管理' : '订阅总览',
    settings ? '管理平台账号、连接凭据和刷新间隔。' : '查看各账号的剩余额度、可用余额和更新时间。',
    actions
  ) + toolbar() + `<div class="board">
    <div id="results"></div>
    ${settings ? `<section class="setting-row"><div><h2>后台刷新</h2><p class="muted">每隔设定时间查询已连接的账号。</p></div><label><span class="sr-only">刷新间隔</span><select class="field" id="refresh-interval">${['5','15','30','60'].map((value) => `<option value="${value}" ${value === refreshInterval ? 'selected' : ''}>每 ${value} 分钟</option>`).join('')}</select></label></section>` : ''}
  </div>`;
  renderResults();
}

function readAccount() {
  return accounts.find((account) => account.id === route.id);
}

function metadataFields(account) {
  return `<label class="control">套餐<input class="field" name="plan" value="${h(account?.plan)}" placeholder="例如 ChatGPT Pro" maxlength="60"></label>
    <label class="control">费用<input class="field" name="amount" type="number" min="0" step="0.01" value="${h(account?.amount)}" placeholder="0.00"></label>
    <label class="control">币种<select class="field" name="currency"><option value="USD" ${account?.currency === 'USD' ? 'selected' : ''}>USD</option><option value="CNY" ${account?.currency === 'CNY' ? 'selected' : ''}>CNY</option></select></label>
    <label class="control">续费周期<select class="field" name="cycle">${['每月', '每年', '其他'].map((cycle) => `<option ${cycle === account?.cycle ? 'selected' : ''}>${cycle}</option>`).join('')}</select></label>
    <label class="control">下次续费日<input class="field" name="renewal" type="date" value="${h(account?.renewal)}"></label>`;
}

function edit(draftName) {
  const account = readAccount();
  if (route.id && !account) return missingAccount();
  const provider = account?.provider || route.provider;
  const isCodex = provider === 'codex';
  const credentialName = providers[provider].keyLabel;
  const hasKey = Boolean(account?.keyTail);
  const title = account ? '编辑订阅' : '添加订阅';
  workspace.innerHTML = backLink() + heading(title, account ? `${providers[provider].name} · ${account.name}` : '选择平台，填写您用于识别账号的名称。')
    + `<div class="form-layout">
      <form class="panel" id="account-form" autocomplete="off">
        <h2>账号配置</h2>
        <p class="intro">${isCodex ? '保存账号后可进入设备码授权。' : hasKey ? '填写并验证新密钥后，系统会替换当前密钥。' : '填写密钥并验证连接后保存。'}</p>
        <div class="form-fields">
          <label class="control">平台<select class="field" name="provider" aria-label="平台" ${account ? 'disabled' : ''}>${platformOptions(provider)}</select></label>
          <label class="control">账号名称<input class="field" name="name" value="${h(draftName ?? account?.name)}" placeholder="例如工作号" maxlength="50" required></label>
          ${isCodex ? metadataFields(account) : `<label class="control full">${hasKey ? '新 ' : ''}${credentialName}<input class="field" name="key" type="password" placeholder="${hasKey ? '输入新密钥以替换' : '输入用于查询余额的密钥'}" maxlength="240" ${hasKey ? '' : 'required'}><small>${hasKey ? '留空可保留当前连接。' : '验证并保存后仅显示末四位。'}</small></label>`}
        </div>
        <div id="form-feedback" class="feedback" role="status" hidden></div>
        <div class="form-actions">
          <button class="button primary" type="submit">${isCodex ? (account ? '保存修改' : '保存并去授权') : '验证并保存'}</button>
          <a class="button" href="${h(href({ view: 'settings', ...settingsFilters }))}" data-back>取消</a>
        </div>
      </form>
      <aside class="panel side" aria-label="当前连接">
        <h2>${hasKey ? '当前连接' : '连接方式'}</h2>
        <dl class="specimen"><dt>平台</dt><dd>${providers[provider].name}</dd>
          ${hasKey ? `<dt>已保存密钥</dt><dd>•••• ${h(account.keyTail)}</dd><dt>连接状态</dt><dd>${status(account)}</dd><dt>余额更新时间</dt><dd>${showTime(account)}</dd>`
            : `<dt>连接方式</dt><dd>${isCodex ? '官方设备码' : credentialName}</dd><dt>连接状态</dt><dd>待连接</dd>`}
        </dl>
      </aside>
    </div>`;
}

function missingAccount() {
  workspace.innerHTML = backLink() + heading('找不到这条订阅', '返回配置管理查看当前账号。');
}

function authorization() {
  const account = readAccount();
  if (!account || account.provider !== 'codex') return missingAccount();
  const state = authStates[account.id] || (route.step === 'expired' ? 'expired' : 'waiting');
  const authContent = {
    waiting: `<div class="code-box"><div class="code-head"><span class="muted">一次性设备码</span><span class="muted">有效期 15 分钟</span></div>
      <div class="code-row"><code class="code-value">Q7KM–2G4T</code><button class="button" data-auth="copy">复制</button></div></div>
      <div class="feedback" role="status">等待您在官方页面完成授权</div>
      <div class="form-actions"><a class="button primary" href="https://auth.openai.com/codex/device" target="_blank" rel="noopener noreferrer">打开 OpenAI 授权页 ↗</a><button class="button" data-auth="connected">我已完成授权</button></div>`,
    expired: '<div class="feedback error" role="status">设备码已过期。请重新获取后继续。</div><div class="form-actions"><button class="button primary" data-auth="waiting">重新获取设备码</button></div>',
    connected: `<div class="feedback" role="status">连接成功 · 这个账号已可刷新额度。</div><div class="form-actions"><a class="button primary" href="${h(href({ view: 'settings', ...settingsFilters }))}" data-back>返回配置管理</a></div>`,
  };
  workspace.innerHTML = backLink() + heading('连接 Codex 账号', account.name)
    + `<div class="form-layout"><section class="panel"><h2>使用设备码授权</h2><p class="intro">登录 OpenAI 后，输入设备码以连接此账号。</p>${authContent[state]}</section>
      <aside class="panel side"><h2>授权步骤</h2><ol class="steps"><li>打开 OpenAI 官方页面。</li><li>登录您想绑定的 Codex 账号并输入设备码。</li><li>返回此页查看连接状态。</li></ol></aside>
    </div>`;
}

function render() {
  const active = route.view === 'overview' ? 'overview' : 'settings';
  document.querySelectorAll('nav a').forEach((link) => {
    if (link.dataset.page === active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  if (route.view === 'edit') return edit();
  if (route.view === 'auth') return authorization();
  landing();
}

function applyFilters() {
  const platform = workspace.querySelector('#platform-filter').value;
  const q = workspace.querySelector('#search').value;
  const next = { view: route.view, platform, q };
  history.replaceState(null, '', href(next));
  route = readRoute();
  if (route.view === 'settings') settingsFilters = { platform, q };
  renderResults();
}

function showDelete(id, trigger) {
  const account = accounts.find((item) => item.id === id);
  if (!account) return;
  cancellingFocus = trigger;
  confirmDialog.innerHTML = `<form method="dialog">
    <h2 id="confirm-title">删除订阅配置？</h2>
    <p>确认删除「${h(account.name)}」（${providers[account.provider].name}）。看板将清除此条凭据和缓存，并停止查询。</p>
    <div class="form-actions"><button class="button" value="cancel" autofocus>取消</button><button class="button delete" value="${h(account.id)}">确认删除</button></div>
  </form>`;
  confirmDialog.returnValue = 'cancel';
  confirmDialog.showModal();
}

confirmDialog.addEventListener('close', () => {
  const deleted = accounts.find((account) => account.id === confirmDialog.returnValue);
  if (deleted) {
    accounts = accounts.filter((account) => account.id !== deleted.id);
    delete authStates[deleted.id];
    renderResults();
    announce(`已删除 ${deleted.name}`);
    workspace.querySelector('#search').focus();
  } else {
    cancellingFocus?.focus();
  }
});

function updateAuth(action) {
  const account = readAccount();
  if (action === 'copy') {
    if (!navigator.clipboard) {
      announce('请选中设备码复制');
      return;
    }
    navigator.clipboard.writeText('Q7KM-2G4T').then(() => {
      workspace.querySelector('[data-auth="copy"]').textContent = '已复制';
    }).catch(() => announce('请选中设备码复制'));
    return;
  }
  authStates[account.id] = action;
  if (action === 'connected') {
    Object.assign(account, { connection: 'connected', short: account.short ?? 85, long: account.long ?? 63, updated: now() });
  }
  authorization();
  workspace.querySelector('h1').focus();
}

function now() {
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date());
}

document.addEventListener('click', (event) => {
  const target = event.target.closest('button, a');
  if (!target || (target.tagName === 'A' && (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey))) return;
  if (target.hasAttribute('data-page') || target.hasAttribute('data-link') || target.hasAttribute('data-back')) {
    event.preventDefault();
    const next = Object.fromEntries(new URL(target.href).searchParams);
    navigate(next);
  } else if (target.hasAttribute('data-add')) {
    navigate({ view: 'edit' });
  } else if (target.hasAttribute('data-clear')) {
    workspace.querySelector('#search').value = '';
    workspace.querySelector('#platform-filter').value = '';
    applyFilters();
    workspace.querySelector('#search').focus();
  } else if (target.dataset.remove) {
    showDelete(target.dataset.remove, target);
  } else if (target.dataset.auth) {
    updateAuth(target.dataset.auth);
  } else if (target.id === 'refresh') {
    target.disabled = true;
    target.textContent = '正在刷新…';
    setTimeout(() => {
      accounts.forEach((account) => {
        if (!account.failed && account.connection === 'connected') account.updated = now();
      });
      if (route.view === 'overview') {
        renderResults();
        const refreshButton = workspace.querySelector('#refresh');
        refreshButton.disabled = false;
        refreshButton.textContent = '↻　立即刷新';
      }
      announce(accounts.some((account) => account.failed) ? '刷新结束，部分账号查询失败' : '刷新完成');
    }, 500);
  }
});

workspace.addEventListener('input', (event) => {
  if (event.target.id === 'search') applyFilters();
});
workspace.addEventListener('change', (event) => {
  if (event.target.id === 'platform-filter') applyFilters();
  if (event.target.id === 'refresh-interval') {
    refreshInterval = event.target.value;
    announce(`刷新间隔已设为 ${refreshInterval} 分钟`);
  }
  const form = event.target.closest('#account-form');
  if (form && event.target.name === 'provider') {
    route.provider = event.target.value;
    history.replaceState(null, '', href(route));
    edit(form.elements.name.value);
    workspace.querySelector('[name="provider"]').focus();
  }
});
workspace.addEventListener('submit', (event) => {
  if (event.target.id !== 'account-form') return;
  event.preventDefault();
  const form = event.target;
  const input = new FormData(form);
  const previous = readAccount();
  const provider = previous?.provider || route.provider;
  const name = input.get('name').trim();
  const key = (input.get('key') || '').trim();
  const feedback = form.querySelector('#form-feedback');

  if (!name || (provider !== 'codex' && !previous && !key) || (key && (key.length < 8 || key.toLowerCase().startsWith('bad')))) {
    feedback.className = 'feedback error';
    feedback.textContent = !name ? '请填写账号名称。' : `密钥验证失败。${previous ? "当前连接已保留。" : "请核对后重试。"}`;
    feedback.hidden = false;
    return;
  }

  const account = previous || { id: crypto.randomUUID(), provider, connection: 'pending' };
  account.name = name;
  if (provider === 'codex') {
    for (const field of ['plan', 'amount', 'currency', 'cycle', 'renewal']) account[field] = input.get(field).trim();
  } else if (key) {
    const sample = sampleAccounts.find((item) => item.provider === provider);
    Object.assign(account, { keyTail: key.slice(-4), connection: 'connected', balances: structuredClone(sample.balances), updated: now(), failed: false });
  }
  if (!previous) accounts.push(account);
  announce('已保存订阅配置');
  if (provider === 'codex' && !previous) navigate({ view: 'auth', id: account.id });
  else navigate({ view: 'settings', ...settingsFilters });
});

window.addEventListener('popstate', () => {
  route = readRoute();
  if (route.view === 'settings') settingsFilters = { platform: route.platform, q: route.q };
  render();
});

render();
