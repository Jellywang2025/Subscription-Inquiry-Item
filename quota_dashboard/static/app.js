'use strict';

const providers = {
  codex: { name: 'Codex', mark: 'C' },
  deepseek: { name: 'DeepSeek', mark: 'D', keyLabel: 'DeepSeek API Key' },
  mimo: { name: 'MiMo', mark: 'M', keyLabel: 'MiMo SK' },
};
const workspace = document.querySelector('#workspace');
const confirmDialog = document.querySelector('#confirm');
const announcement = document.querySelector('#announcement');
const validViews = ['overview', 'settings', 'edit', 'auth'];
let accounts = [];
let accountsLoaded = false;
let accountsError = '';
let settingsFilters = { platform: '', q: '' };
let cancellingFocus;
let refreshInFlight = false;

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
  };
}
let route = readRoute();
if (route.view === 'settings') settingsFilters = { platform: route.platform, q: route.q };

function href(values) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value) query.set(key, value);
  }
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

async function apiFetch(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  // 会话过期时服务端把 API 重定向到登录页；跟随后的 200 HTML 不是数据。
  if (response.redirected && new URL(response.url).pathname === '/login') {
    // 回跳目标用当前页面而非被拦截的 API 路径，登录后整页恢复视图。
    location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
    throw new Error('登录已过期，正在转入登录页。');
  }
  let payload = {};
  try { payload = await response.json(); } catch { /* 非 JSON 响应按空负载处理 */ }
  if (!response.ok) throw new Error(payload.error || `请求失败（HTTP ${response.status}）`);
  return payload;
}

async function loadAccounts() {
  const firstLoad = !accountsLoaded;
  try {
    const payload = await apiFetch('/api/accounts');
    accounts = Array.isArray(payload.accounts) ? payload.accounts : [];
    accountsError = '';
  } catch (error) {
    accounts = [];
    accountsError = error.message;
  }
  accountsLoaded = true;
  if (route.view === 'overview' || route.view === 'settings') {
    renderResults();
  } else if (firstLoad && route.id) {
    // 深链接的账号数据首次到达时渲染；后续刷新由调用方决定，避免渲染循环。
    render();
  }
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
  if (account.connection !== 'connected') return '<span class="status issue">待连接</span>';
  return '<span class="status">已连接</span>';
}

function glyph(account) {
  return `<span class="glyph" aria-hidden="true">${providers[account.provider]?.mark || '?'}</span>`;
}

function providerName(account) {
  return providers[account.provider]?.name || account.provider;
}

const CLOCK_FORMAT = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const DAY_FORMAT = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });

function displayClock(value) {
  const time = new Date(value);
  if (Number.isNaN(time.getTime())) return '';
  const clock = CLOCK_FORMAT.format(time);
  return DAY_FORMAT.format(time) === DAY_FORMAT.format(new Date()) ? clock : `${DAY_FORMAT.format(time)} ${clock}`;
}

function showTime(account) {
  if (!account.updated) return '等待首次更新';
  const clock = displayClock(account.updated);
  const label = account.failed ? '上次成功' : '已更新';
  return clock ? `${label} ${clock}` : h(account.updated);
}

function money(currency, value) {
  const signs = { CNY: '¥', USD: '$' };
  return `${signs[currency] || ''}${value}`;
}

function resetDisplay(value) {
  if (!value) return '未提供';
  const clock = displayClock(value);
  return clock || h(value);
}

function quotaMetric(label, value, reset) {
  return `<div class="metric">
    <div class="metric-label">${h(label)}</div><strong>${h(value)}%</strong>
    <div class="progress" role="progressbar" aria-label="${h(label)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${h(value)}"><span style="width:${h(value)}%"></span></div>
    <small>${resetDisplay(reset)} 重置</small>
  </div>`;
}

const cardTones = { codex: 'dark', mimo: 'soft' };

function card(account) {
  const isCodex = account.provider === 'codex';
  const tone = cardTones[account.provider] || '';
  let metrics;
  let detail;
  if (isCodex) {
    const windows = Array.isArray(account.windows) ? account.windows : [];
    metrics = account.updated
      ? windows.map((window) => quotaMetric(
        `${window.label}剩余`, window.remaining, window.reset
      )).join('')
      : `<div class="metric bad"><strong>待授权</strong><small>${h(account.error || '从配置管理完成连接后查看额度')}</small></div>`;
    detail = `<div>套餐　${h(account.plan || '未填写')}</div><div>费用　${account.amount ? h(money(account.currency, account.amount) + ' / ' + account.cycle) : '未填写'}</div><div>续费日　${h(account.renewal || '未填写')}</div>`;
  } else {
    const balances = Array.isArray(account.balances) ? account.balances : [];
    metrics = account.updated
      ? balances.map((balance) => `<div class="metric"><div class="metric-label">可用余额 · ${h(balance.currency)}</div><strong>${h(money(balance.currency, balance.value))}</strong></div>`).join('')
      : `<div class="metric bad"><strong>暂无余额</strong><small>${h(account.error || '尚未完成查询')}</small></div>`;
    detail = account.updated
      ? balances.map((balance) => `<div>${h(balance.description || '')}</div>`).join('')
      : '<div>暂无余额明细</div>';
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
      const platform = providerName(account);
      const info = account.provider === 'codex' ? account.renewal || '未填写' : `•••• ${account.key_tail || ''}`;
      return `<li>
        <div class="account-line">
          <div class="account">${glyph(account)}<b>${h(account.name)}</b></div>
          <span class="secondary-cell muted">${h(platform)}</span>
          <div>${status(account)}</div>
          <span class="secondary-cell muted">${h(info)}</span>
          <div class="actions" aria-label="${h(account.name)}的操作">
            ${account.provider === 'codex' ? `<a class="button quiet" href="${h(href({ view: 'auth', id: account.id }))}" data-link>${account.connection === 'connected' ? '重新授权' : '去授权'}</a>` : ''}
            <a class="button" href="${h(href({ view: 'edit', id: account.id }))}" data-link>编辑</a>
            <button class="button quiet" data-remove="${h(account.id)}">删除</button>
          </div>
        </div>
        <details class="line-details"><summary>账号明细</summary><div class="detail-list muted"><div>平台　${h(platform)}</div><div>${account.provider === 'codex' ? '续费日' : '密钥'}　${h(info)}</div></div></details>
      </li>`;
    }).join('')}</ul>
  </div>`;
}

function renderResults() {
  const counter = workspace.querySelector('#count');
  if (!counter) return;
  if (accountsError) {
    counter.textContent = '';
    workspace.querySelector('#results').innerHTML =
      `<section class="empty"><h2>账号加载失败</h2><p>${h(accountsError)}</p><button class="button" data-retry>重新加载</button></section>`;
    return;
  }
  const items = visibleAccounts();
  counter.textContent = `${items.length} 个账号`;
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
  if (route.id && !account) {
    // 深链接的账号数据未到达时先显示占位，loadAccounts 完成后重渲染。
    if (!accountsLoaded) return loadingAccount();
    if (accountsError) return accountLoadError();
    return missingAccount();
  }
  const provider = account?.provider || route.provider;
  const isCodex = provider === 'codex';
  const credentialName = providers[provider]?.keyLabel || '';
  const hasKey = Boolean(account?.key_tail) || account?.connection === 'connected';
  const title = account ? '编辑订阅' : '添加订阅';
  workspace.innerHTML = backLink() + heading(title, account ? `${providers[provider]?.name || provider} · ${account.name}` : '选择平台，填写您用于识别账号的名称。')
    + `<div class="form-layout">
      <form class="panel" id="account-form" autocomplete="off">
        <h2>账号配置</h2>
        <p class="intro">${isCodex ? '保存账号后可进入设备码授权。' : hasKey ? '填写并验证新密钥后，系统会替换当前密钥。' : '填写密钥并验证连接后保存。'}</p>
        <div class="form-fields">
          <label class="control">平台<select class="field" name="provider" aria-label="平台" ${account ? 'disabled' : ''}>${platformOptions(provider)}</select></label>
          <label class="control">账号名称<input class="field" name="name" value="${h(draftName ?? account?.name)}" placeholder="自定名称" maxlength="50" required></label>
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
        <dl class="specimen"><dt>平台</dt><dd>${h(providers[provider]?.name || provider)}</dd>
          ${hasKey ? `${account?.key_tail ? `<dt>已保存密钥</dt><dd>•••• ${h(account.key_tail)}</dd>` : ''}<dt>连接状态</dt><dd>${status(account)}</dd><dt>更新时间</dt><dd>${showTime(account)}</dd>`
            : `<dt>连接方式</dt><dd>${isCodex ? '官方设备码' : credentialName}</dd><dt>连接状态</dt><dd>待连接</dd>`}
        </dl>
      </aside>
    </div>`;
}

function missingAccount() {
  workspace.innerHTML = backLink() + heading('找不到这条订阅', '返回配置管理查看当前账号。');
}

function loadingAccount() {
  workspace.innerHTML = backLink() + heading('正在载入账号', '稍候片刻。');
}

function accountLoadError() {
  workspace.innerHTML = backLink() + heading('账号加载失败', accountsError || '请稍后重试。');
}

function authorization() {
  const account = readAccount();
  if (!account || account.provider !== 'codex') return missingAccount();
  renderAuthorization(account, { status: 'idle' });
  pollAuthorization(account);
}

let authTimer = null;

function stopAuthPolling() {
  if (authTimer) { clearInterval(authTimer); authTimer = null; }
}

function pollAuthorization(account) {
  stopAuthPolling();
  const tick = async () => {
    if (route.view !== 'auth' || route.id !== account.id) { stopAuthPolling(); return; }
    try {
      const payload = await apiFetch(`/api/accounts/${encodeURIComponent(account.id)}/device`);
      if (!authTimer || route.view !== 'auth' || route.id !== account.id) return;
      const device = payload.device || { status: 'idle' };
      if (device.status === 'connected' && account.connection !== 'connected') {
        // 授权刚完成：停止轮询，拉取最新账号数据后渲染一次。
        stopAuthPolling();
        await loadAccounts();
        const fresh = readAccount();
        if (fresh) renderAuthorization(fresh, device);
        return;
      }
      renderAuthorization(account, device);
    } catch (error) {
      if (!authTimer || route.view !== 'auth' || route.id !== account.id) return;
      renderAuthorization(account, { status: 'idle', error: error.message });
    }
  };
  authTimer = setInterval(tick, 1200);
  tick();
}

function renderAuthorization(account, device) {
  const state = device.status || 'idle';
  const isConnected = state === 'connected' || (state === 'idle' && account.connection === 'connected');
  let content;
  if (isConnected) {
    content = `<div class="feedback" role="status">连接成功 · 这个账号已可刷新额度。</div>
      <div class="form-actions"><a class="button primary" href="${h(href({ view: 'settings', ...settingsFilters }))}" data-back>返回配置管理</a></div>`;
  } else if (state === 'expired') {
    content = `<div class="feedback error" role="status">设备码已过期。请重新获取后继续。</div>
      <div class="form-actions"><button class="button primary" data-auth="start">重新获取设备码</button></div>`;
  } else if (state === 'waiting') {
    content = `<div class="code-box"><div class="code-head"><span class="muted">一次性设备码</span><span class="muted">有效期 15 分钟</span></div>
      <div class="code-row"><code class="code-value">${h(device.user_code || '')}</code><button class="button" data-auth="copy">复制</button></div></div>
      <div class="feedback" role="status">等待您在官方页面完成授权</div>
      <div class="form-actions"><a class="button primary" href="https://auth.openai.com/codex/device" target="_blank" rel="noopener noreferrer">打开 OpenAI 授权页 ↗</a><button class="button" data-auth="check">我已完成授权</button></div>`;
  } else if (state === 'failed') {
    content = `<div class="feedback error" role="status">${h(device.error || '授权未完成。')}</div>
      <div class="form-actions"><button class="button primary" data-auth="start">重新获取设备码</button></div>`;
  } else {
    content = `<div class="feedback" role="status">这个账号尚未连接。获取一次性设备码后，在 OpenAI 官方页面完成授权。</div>
      <div class="form-actions"><button class="button primary" data-auth="start">开始授权</button></div>`;
  }
  workspace.innerHTML = backLink() + heading('连接 Codex 账号', account.name)
    + `<div class="form-layout"><section class="panel"><h2>使用设备码授权</h2><p class="intro">登录 OpenAI 后，输入设备码以连接此账号。</p>${content}</section>
      <aside class="panel side"><h2>授权步骤</h2><ol class="steps"><li>打开 OpenAI 官方页面。</li><li>登录您想绑定的 Codex 账号并输入设备码。</li><li>返回此页查看连接状态。</li></ol></aside>
    </div>`;
}

function render() {
  stopAuthPolling();
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
    <p>确认删除「${h(account.name)}」（${h(providerName(account))}）。看板将清除此条凭据和缓存，并停止查询。</p>
    <div class="form-actions"><button class="button" value="cancel" autofocus>取消</button><button class="button delete" value="${h(account.id)}">确认删除</button></div>
  </form>`;
  confirmDialog.returnValue = 'cancel';
  confirmDialog.showModal();
}

confirmDialog.addEventListener('close', async () => {
  const deleted = accounts.find((account) => account.id === confirmDialog.returnValue);
  if (!deleted) {
    cancellingFocus?.focus();
    return;
  }
  try {
    await apiFetch(`/api/accounts/${encodeURIComponent(deleted.id)}`, { method: 'DELETE' });
    announce(`已删除 ${deleted.name}`);
    await loadAccounts();
    workspace.querySelector('#search')?.focus();
  } catch (error) {
    announce(`删除失败：${error.message}`);
    cancellingFocus?.focus();
  }
});

async function refreshNow(trigger) {
  if (refreshInFlight) return;
  refreshInFlight = true;
  const label = trigger.textContent;
  trigger.disabled = true;
  trigger.textContent = '正在刷新…';
  try {
    await apiFetch('/api/refresh', { method: 'POST' });
    await loadAccounts();
    announce('刷新完成');
  } catch (error) {
    announce(`刷新失败：${error.message}`);
  } finally {
    refreshInFlight = false;
    trigger.disabled = false;
    trigger.textContent = label;
  }
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
  } else if (target.hasAttribute('data-retry')) {
    loadAccounts();
  } else if (target.dataset.auth) {
    handleAuthAction(target.dataset.auth, target);
  } else if (target.dataset.remove) {
    showDelete(target.dataset.remove, target);
  } else if (target.id === 'refresh') {
    refreshNow(target);
  }
});

async function handleAuthAction(action, trigger) {
  const account = readAccount();
  if (!account) return;
  if (action === 'copy') {
    if (!navigator.clipboard) {
      announce('请选中设备码复制');
      return;
    }
    try {
      await navigator.clipboard.writeText(workspace.querySelector('.code-value')?.textContent || '');
      trigger.textContent = '已复制';
    } catch {
      announce('请选中设备码复制');
    }
    return;
  }
  trigger.disabled = true;
  try {
    const payload = await apiFetch(
      `/api/accounts/${encodeURIComponent(account.id)}/device/${action}`, { method: 'POST' }
    );
    const device = payload.device || { status: 'idle' };
    if (device.status === 'connected' && account.connection !== 'connected') {
      await loadAccounts();
      const fresh = readAccount();
      if (fresh) renderAuthorization(fresh, device);
    } else {
      renderAuthorization(account, device);
    }
    if (action === 'check' && device.status !== 'connected') {
      pollAuthorization(account);
    }
  } catch (error) {
    announce(error.message);
  } finally {
    trigger.disabled = false;
  }
}

workspace.addEventListener('input', (event) => {
  if (event.target.id === 'search') applyFilters();
});
workspace.addEventListener('change', (event) => {
  if (event.target.id === 'platform-filter') applyFilters();
  const form = event.target.closest('#account-form');
  if (form && event.target.name === 'provider') {
    route.provider = event.target.value;
    history.replaceState(null, '', href(route));
    edit(form.elements.name.value);
    workspace.querySelector('[name="provider"]').focus();
  }
});
workspace.addEventListener('submit', async (event) => {
  if (event.target.id !== 'account-form') return;
  event.preventDefault();
  const form = event.target;
  const input = new FormData(form);
  const previous = readAccount();
  const provider = previous?.provider || route.provider;
  const name = input.get('name').trim();
  const key = (input.get('key') || '').trim();
  const feedback = form.querySelector('#form-feedback');
  const submitButton = form.querySelector('button[type=submit]');

  if (!name) {
    feedback.className = 'feedback error';
    feedback.textContent = '请填写账号名称。';
    feedback.hidden = false;
    return;
  }

  const payload = { provider, name };
  if (provider === 'codex') {
    for (const field of ['plan', 'amount', 'currency', 'cycle', 'renewal']) {
      payload[field] = (input.get(field) || '').trim();
    }
  } else if (key || !previous) {
    payload.key = key;
  }

  submitButton.disabled = true;
  try {
    const result = await apiFetch(previous ? `/api/accounts/${encodeURIComponent(previous.id)}` : '/api/accounts', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    announce('已保存订阅配置');
    await loadAccounts();
    if (!previous && provider === 'codex' && result.account?.id) {
      navigate({ view: 'auth', id: result.account.id });
    } else {
      navigate({ view: 'settings', ...settingsFilters });
    }
  } catch (error) {
    feedback.className = 'feedback error';
    feedback.textContent = `保存失败：${error.message}`;
    feedback.hidden = false;
  } finally {
    submitButton.disabled = false;
  }
});

window.addEventListener('popstate', () => {
  route = readRoute();
  if (route.view === 'settings') settingsFilters = { platform: route.platform, q: route.q };
  render();
});

render();
loadAccounts();
