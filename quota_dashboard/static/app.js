"use strict";

const CURRENCY_SYMBOLS = { CNY: "¥", USD: "$" };

function currencySymbol(currency) {
  return CURRENCY_SYMBOLS[currency] ?? `${currency} `;
}

function formatAmount(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toFixed(2) : value;
}

function amountLine(symbol, value) {
  const line = document.createElement("div");
  const amount = document.createElement("span");
  amount.className = "amount";
  amount.textContent = `${symbol}${formatAmount(value)}`;
  line.append(amount);
  return line;
}

function detailRow(label, value, symbol) {
  const row = document.createElement("div");
  const name = document.createElement("span");
  name.textContent = label;
  const amount = document.createElement("strong");
  amount.textContent = `${symbol}${formatAmount(value)}`;
  row.append(name, amount);
  return row;
}

function windowRow(label, remainingPercent, resetAt) {
  const row = document.createElement("div");
  row.className = "window-row";
  const name = document.createElement("span");
  name.className = "window-label";
  name.textContent = label;
  const remaining = document.createElement("strong");
  remaining.textContent = `剩余 ${remainingPercent}%`;
  const reset = document.createElement("span");
  reset.className = "window-reset";
  reset.textContent = resetAt
    ? `重置时间: ${new Date(resetAt * 1000).toLocaleString("zh-CN", { hour12: false })}`
    : "重置时间: 未提供";
  row.append(name, remaining, reset);
  return row;
}

function codexWindowLine(label, window) {
  return windowRow(label, Math.max(0, 100 - Math.round(window.used_percent)), window.reset_at);
}

function renderChatGptCard(shared) {
  const card = document.querySelector('[data-source="chatgpt"]');
  const status = card.querySelector('[data-role="status"]');
  const balances = card.querySelector('[data-role="balances"]');
  if (shared) {
    balances.replaceChildren(windowRow(shared.label, shared.remaining_percent, shared.reset_at));
    status.textContent = "";
  } else {
    balances.replaceChildren();
    status.textContent = "需前往官方查看";
  }
}

const SOURCE_RENDERERS = {
  deepseek: {
    balances(payload) {
      return payload.data.balance_infos.map((info) =>
        amountLine(currencySymbol(info.currency), info.total_balance),
      );
    },
    details(payload) {
      return payload.data.balance_infos.flatMap((info) => {
        const symbol = currencySymbol(info.currency);
        return [
          [`${info.currency} 充值余额`, info.topped_up_balance],
          [`${info.currency} 赠金`, info.granted_balance],
        ].map(([label, value]) => detailRow(label, value, symbol));
      });
    },
  },
  codex: {
    balances(payload) {
      const rate = payload.data.rate_limit ?? {};
      const windows = [
        ["短窗口", rate.primary_window],
        ["长窗口", rate.secondary_window],
      ];
      return windows.flatMap(([label, window]) => (window ? [codexWindowLine(label, window)] : []));
    },
    details() {
      return [];
    },
    afterRender(payload) {
      renderChatGptCard(payload.shared ?? null);
    },
  },
  mimo: {
    balances(payload) {
      const info = payload.data.balance_infos[0];
      return info ? [amountLine(currencySymbol(info.currency), payload.available)] : [];
    },
    details(payload) {
      const info = payload.data.balance_infos[0];
      if (!info) return [];
      const symbol = currencySymbol(info.currency);
      return [
        ["现金", info.cash_balance],
        ["赠金", info.gift_balance],
        ["可用授信", info.credit_available],
        ["冻结", info.frozen_balance],
      ].map(([label, value]) => detailRow(label, value, symbol));
    },
  },
};

function createSourceController(name) {
  const endpoint = `/api/sources/${name}/balance`;
  let element = null;
  let requestSeq = 0;
  let hasResult = false;

  function card() {
    element ??= document.querySelector(`[data-source="${name}"]`);
    return element;
  }

  function render(payload) {
    const target = card();
    const renderer = SOURCE_RENDERERS[name];
    target.querySelector('[data-role="status"]').textContent = "";
    target.querySelector('[data-role="time"]').textContent = `成功时间: ${formatTime(payload.fetched_at)}`;
    target.querySelector('[data-role="balances"]').replaceChildren(...renderer.balances(payload));
    const details = renderer.details(payload);
    target.querySelector('[data-role="detail"]').replaceChildren(...details);
    target.querySelector('[data-role="toggle"]').hidden = details.length === 0;
    renderer.afterRender?.(payload);
  }

  async function refresh() {
    const seq = ++requestSeq;
    const hadResult = hasResult;
    const status = card().querySelector('[data-role="status"]');
    status.textContent = "查询中";
    try {
      const response = await fetch(endpoint, { cache: "no-store" });
      const payload = await response.json();
      if (seq !== requestSeq) return; // 已有更新的查询，本结果作废
      if (payload.status !== "ok") {
        if (!hadResult && payload.last_success) {
          hasResult = true;
          render(payload.last_success); // 本轮尚无结果时，先呈现持久化的上次成功结果
        }
        throw new Error(payload.error || "未知错误");
      }
      hasResult = true;
      render(payload);
    } catch (error) {
      if (seq !== requestSeq) return;
      const label = hadResult ? "刷新失败" : "查询失败";
      status.textContent = `${label}: ${error.message || error}`;
    }
  }

  function init() {
    const toggle = card().querySelector('[data-role="toggle"]');
    const detail = card().querySelector('[data-role="detail"]');
    toggle.addEventListener("click", () => {
      detail.hidden = !detail.hidden;
    });
  }

  return { refresh, init };
}

function formatTime(iso) {
  return new Date(iso).toLocaleTimeString("zh-CN", { hour12: false });
}

const CONTROLLERS = Object.fromEntries(
  Object.keys(SOURCE_RENDERERS).map((name) => [name, createSourceController(name)]),
);

function refreshAll() {
  return Promise.allSettled(Object.values(CONTROLLERS).map((controller) => controller.refresh()));
}

function init() {
  Object.values(CONTROLLERS).forEach((controller) => controller.init());
  document.getElementById("refresh-all").addEventListener("click", refreshAll);
  refreshAll();
}

document.addEventListener("DOMContentLoaded", init);
