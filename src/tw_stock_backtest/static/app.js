'use strict';
// 共用互動：頁內分頁與送出中的按鈕狀態。不依賴任何函式庫。

// 分頁：<div data-tabs> 內含 role=tablist 的按鈕與 role=tabpanel 的區塊。
// 目前分頁記在網址的 #，重新整理或分享連結時會回到同一個分頁。
// 重新載入的區塊（例如個股頁的查詢結果）可呼叫 WorkbenchApp.enhance 重新啟用分頁。
function enhanceTabs(scope) {
for (const root of scope.querySelectorAll('[data-tabs]')) {
  if (root.dataset.ready) continue;
  root.dataset.ready = '1';
  const tabs = [...root.querySelectorAll('[role=tab]')];
  const select = (tab, focus) => {
    for (const item of tabs) {
      const active = item === tab;
      item.setAttribute('aria-selected', active ? 'true' : 'false');
      item.tabIndex = active ? 0 : -1;
      const panel = document.getElementById(item.getAttribute('aria-controls'));
      if (panel) panel.hidden = !active;
    }
    if (focus) tab.focus();
  };
  const fromHash = () => tabs.find(tab => '#' + tab.getAttribute('aria-controls') === location.hash);
  for (const tab of tabs) {
    tab.addEventListener('click', () => {
      select(tab, false);
      history.replaceState(null, '', '#' + tab.getAttribute('aria-controls'));
    });
    tab.addEventListener('keydown', event => {
      const step = {ArrowRight: 1, ArrowLeft: -1}[event.key];
      if (!step) return;
      event.preventDefault();
      select(tabs[(tabs.indexOf(tab) + step + tabs.length) % tabs.length], true);
    });
  }
  select(fromHash() || tabs.find(tab => tab.getAttribute('aria-selected') === 'true') || tabs[0], false);
  window.addEventListener('hashchange', () => { const tab = fromHash(); if (tab) select(tab, false); });
}
}
enhanceTabs(document);

// 最近查看過的股票：只存在這個瀏覽器（localStorage），不送到伺服器。
// 個股頁以 data-recent-stock 標記；股票選擇器與個股搜尋頁在沒有輸入時列出。
// 私密瀏覽或封鎖網站資料時讀寫會失敗，這時當成沒有紀錄，頁面照常運作。
const RECENT_KEY = 'tw-stock-backtest.recent-stocks';
const RECENT_MAX = 8;
const recentStocks = {
  read() {
    try {
      const items = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
      return Array.isArray(items) ? items.filter(item => item && /^\d{4}$/.test(item.stock_id)) : [];
    } catch { return []; }
  },
  remember(stock) {
    if (!stock || !/^\d{4}$/.test(stock.stock_id)) return;
    const items = [stock, ...recentStocks.read().filter(item => item.stock_id !== stock.stock_id)];
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(items.slice(0, RECENT_MAX))); } catch { /* 無法保存時略過 */ }
  },
};
const viewed = document.querySelector('[data-recent-stock]');
if (viewed) {
  recentStocks.remember({
    stock_id: viewed.dataset.recentStock,
    name: viewed.dataset.recentName || '',
    industry: viewed.dataset.recentIndustry || '',
  });
}
// 個股搜尋頁：沒有輸入時，把最近查看的股票填進預留的區塊。
for (const box of document.querySelectorAll('[data-recent-list]')) {
  const items = recentStocks.read();
  if (!items.length) continue;
  const list = box.querySelector('ul');
  list.replaceChildren(...items.map(item => {
    const li = document.createElement('li');
    const link = document.createElement('a');
    link.className = 'chip';
    link.href = '/stocks/' + item.stock_id;
    link.textContent = item.name ? item.stock_id + ' ' + item.name : item.stock_id;
    li.append(link);
    return li;
  }));
  box.hidden = false;
}
window.WorkbenchApp = {enhance: enhanceTabs, recentStocks};

// 一般表單送出後，按鈕顯示處理中並避免重複送出。
// 由其他腳本以 fetch 送出的表單不加 data-loading，維持各自的處理方式。
for (const form of document.querySelectorAll('form[data-loading]')) {
  form.addEventListener('submit', () => {
    const button = form.querySelector('button[type=submit], button:not([type])');
    if (!button || button.disabled) return;
    button.classList.add('is-loading');
    button.setAttribute('aria-busy', 'true');
    // 等這次送出帶走表單資料後再停用，避免按鈕的值沒被送出。
    setTimeout(() => { button.disabled = true; }, 0);
  });
}
