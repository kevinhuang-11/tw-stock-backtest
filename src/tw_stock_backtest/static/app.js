'use strict';
// 共用互動：頁內分頁與送出中的按鈕狀態。不依賴任何函式庫。

// 分頁：<div data-tabs> 內含 role=tablist 的按鈕與 role=tabpanel 的區塊。
// 目前分頁記在網址的 #，重新整理或分享連結時會回到同一個分頁。
for (const root of document.querySelectorAll('[data-tabs]')) {
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
