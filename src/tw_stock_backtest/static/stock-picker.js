'use strict';
// 股票選擇器：把 <input name="stocks">（以空白分隔的代號）升級成可搜尋、以標籤顯示的元件。
// 送出時仍是同一個欄位名稱與同樣的格式，後端不需要任何改動。
// 沒有 JavaScript 時，原本的文字欄位照常可用（直接輸入或貼上代號）。
(() => {
const SEPARATORS = /[\s,，、;；]+/;
const CODE = /^\d{4}$/;
const SEARCH_DELAY = 120;           // 毫秒；連續輸入時只送最後一次搜尋
let serial = 0;
let stockList;      // 共用的名單請求（代號、名稱、產業），整頁只取一次
let watchlistList;

const make = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const loadStocks = () => stockList ??= fetch('/api/stock-options')
  .then(response => { if (!response.ok) throw new Error(); return response.json(); });

// 搜尋與排序在伺服器（stock_search.py），與個股搜尋頁是同一個函式，兩邊結果一定相同。
const searchStocks = (query, industry) => fetch('/api/stock-search?' + new URLSearchParams({q: query, industry}))
  .then(response => { if (!response.ok) throw new Error(); return response.json(); });

const recent = () => (window.WorkbenchApp && window.WorkbenchApp.recentStocks ? window.WorkbenchApp.recentStocks.read() : []);

const loadWatchlist = () => watchlistList ??= fetch('/api/watchlist-options')
  .then(response => { if (!response.ok) throw new Error(); return response.json(); })
  .then(data => data.stocks);

function mount(field) {
  if (field.dataset.picker) return;
  field.dataset.picker = '1';
  const id = 'stock-picker-' + (++serial);
  const max = Number(field.dataset.max) || 10;
  const known = new Map();              // 代號 → {stock_id, name, industry}
  const chosen = field.value.trim().split(SEPARATORS).filter(Boolean);
  let options = [];                     // 目前顯示、可以選的建議（不含分組標題與說明）
  let active = -1;                      // 鍵盤目前指到的建議
  let watched = [];                     // 自選股代號；沒有輸入時列出
  let searchSerial = 0;                 // 只採用最後一次送出的搜尋結果
  let searchTimer;
  let pending = null;                   // 尚未完成的搜尋；Enter 與上下鍵會等它完成
  let settle = null;                    // 讓被新搜尋取代的舊 pending 也會結束

  // ----- 結構 -----
  const root = make('div', 'stock-picker');
  const head = make('div', 'sp-head');
  const label = make('label', 'sp-label', '股票');
  const count = make('span', 'sp-count');
  head.append(label);

  const control = make('div', 'sp-control');
  const tags = make('ul', 'sp-tags');
  tags.setAttribute('aria-label', '已選股票');
  const input = make('input', 'sp-input');
  input.type = 'text';
  input.id = id + '-input';
  input.autocomplete = 'off';
  input.spellcheck = false;
  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-autocomplete', 'list');
  input.setAttribute('aria-expanded', 'false');
  input.setAttribute('aria-controls', id + '-list');
  input.placeholder = '輸入代號或名稱，或貼上多個代號';
  label.htmlFor = input.id;
  control.append(tags, input);

  const list = make('ul', 'sp-list');
  list.id = id + '-list';
  list.setAttribute('role', 'listbox');
  list.setAttribute('aria-label', '股票建議');
  list.hidden = true;

  const actions = make('div', 'sp-actions');
  const addWatchlist = make('button', 'sp-action', '加入全部自選股');
  addWatchlist.type = 'button';
  const clearAll = make('button', 'sp-action', '清除全部');
  clearAll.type = 'button';
  // 依產業瀏覽：選了產業時列出該產業所有股票，也可以再輸入文字縮小範圍。
  const industry = make('select', 'sp-industry');
  industry.id = id + '-industry';
  industry.setAttribute('aria-label', '依產業篩選');
  industry.append(new Option('全部產業', ''));
  // 快捷操作放在輸入區上方，展開的建議清單才不會蓋住它們。
  actions.append(industry, addWatchlist, clearAll, count);
  head.append(actions);

  const message = make('p', 'sp-message');
  message.setAttribute('role', 'status');
  message.setAttribute('aria-live', 'polite');

  // 原欄位改成隱藏欄位並留在表單內，名稱與值的格式不變。
  const holder = field.parentElement.tagName === 'LABEL' ? field.parentElement : field;
  holder.after(root);
  field.type = 'hidden';
  field.removeAttribute('required');
  // 建議清單以輸入區為定位基準，展開時浮在下方內容之上。
  const anchor = make('div', 'sp-field');
  anchor.append(control, list);
  root.append(head, anchor, message, field);
  if (holder !== field) holder.remove();

  // ----- 狀態 -----
  const describe = code => {
    const stock = known.get(code);
    return stock && stock.name ? code + ' ' + stock.name : code;
  };

  const say = (text, isError) => {
    message.textContent = text || '';
    message.classList.toggle('is-error', Boolean(text) && Boolean(isError));
  };

  const sync = () => {
    field.value = chosen.join(' ');
    count.textContent = '已選 ' + chosen.length + ' / ' + max;
    count.classList.toggle('is-full', chosen.length >= max);
    clearAll.hidden = chosen.length === 0;
    tags.replaceChildren(...chosen.map(code => {
      const item = make('li', 'sp-tag');
      item.append(make('span', 'sp-tag-text', describe(code)));
      const remove = make('button', 'sp-remove', '✕');
      remove.type = 'button';
      remove.setAttribute('aria-label', '移除 ' + describe(code));
      remove.addEventListener('click', () => { removeCode(code); input.focus(); });
      item.append(remove);
      return item;
    }));
    field.dispatchEvent(new Event('change', {bubbles: true}));
  };

  // 回傳 {added, duplicate, invalid, overflow}，由呼叫端決定要顯示什麼訊息。
  const addCodes = codes => {
    const result = {added: [], duplicate: [], invalid: [], overflow: []};
    for (const raw of codes) {
      const code = raw.trim();
      if (!code) continue;
      if (!CODE.test(code)) result.invalid.push(code);
      else if (chosen.includes(code) || result.added.includes(code)) result.duplicate.push(code);
      else if (chosen.length + result.added.length >= max) result.overflow.push(code);
      else result.added.push(code);
    }
    chosen.push(...result.added);
    if (result.added.length) sync();
    return result;
  };

  const report = result => {
    const parts = [];
    if (result.added.length > 1) parts.push('已加入 ' + result.added.length + ' 檔');
    if (result.invalid.length) parts.push('無法辨識：' + result.invalid.join('、') + '（需為四位數代號）');
    if (result.overflow.length) parts.push('超過 ' + max + ' 檔上限，未加入：' + result.overflow.join('、'));
    if (result.duplicate.length && !result.added.length && !result.invalid.length && !result.overflow.length) parts.push('已在清單中：' + result.duplicate.join('、'));
    say(parts.join('；'), result.invalid.length > 0 || result.overflow.length > 0);
  };

  const removeCode = code => {
    const index = chosen.indexOf(code);
    if (index < 0) return;
    chosen.splice(index, 1);
    sync();
    say('已移除 ' + describe(code));
    refresh();
  };

  // ----- 建議清單 -----
  const close = () => {
    list.hidden = true;
    active = -1;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
  };

  const highlight = index => {
    active = index;
    [...list.children].forEach((item, position) => {
      item.setAttribute('aria-selected', position === index ? 'true' : 'false');
      if (position === index) {
        input.setAttribute('aria-activedescendant', item.id);
        item.scrollIntoView({block: 'nearest'});
      }
    });
    if (index < 0) input.removeAttribute('aria-activedescendant');
  };

  // 把一組項目畫進清單：groups 是 [{title, stocks}]，note 是清單底部的說明（不可選）。
  const render = (groups, note, emptyText) => {
    options = [];
    const nodes = [];
    for (const group of groups) {
      if (!group.stocks.length) continue;
      if (group.title) {
        const title = make('li', 'sp-group', group.title);
        title.setAttribute('role', 'presentation');
        nodes.push(title);
      }
      for (const stock of group.stocks) {
        const index = options.length;
        options.push(stock);
        const item = make('li', 'sp-option');
        item.id = id + '-option-' + index;
        item.setAttribute('role', 'option');
        item.append(make('span', 'sp-option-code', stock.stock_id), make('span', 'sp-option-name', stock.name || '名稱未提供'));
        if (chosen.includes(stock.stock_id)) item.append(make('span', 'sp-option-chosen', '已選'));
        else if (stock.industry) item.append(make('span', 'sp-option-industry', stock.industry));
        // mousedown 時就加入，避免輸入框先失去焦點而關閉清單。
        item.addEventListener('mousedown', event => { event.preventDefault(); choose(index); });
        nodes.push(item);
      }
    }
    if (!options.length && emptyText) nodes.push(make('li', 'sp-empty', emptyText));
    if (note) {
      const footer = make('li', 'sp-note', note);
      footer.setAttribute('role', 'presentation');
      nodes.push(footer);
    }
    list.replaceChildren(...nodes);
    list.hidden = !nodes.length;
    input.setAttribute('aria-expanded', list.hidden ? 'false' : 'true');
  };

  const describeStock = code => known.get(code) || {stock_id: code};

  // 沒有輸入、也沒有選產業：列出自選股與最近查看，都沒有時顯示使用說明。
  const showStart = () => {
    const recentCodes = recent().map(item => item.stock_id).filter(code => !watched.includes(code));
    const groups = [
      {title: '自選股', stocks: watched.map(describeStock)},
      {title: '最近查看', stocks: recentCodes.map(code => ({...recent().find(item => item.stock_id === code), ...known.get(code)}))},
    ];
    const empty = !groups.some(group => group.stocks.length);
    render(groups, null, empty ? '輸入代號（例如 2330，或開頭 23）或名稱（例如 台積電、積電）；也可以從上方的產業選單瀏覽' : null);
    highlight(-1);
  };

  const refresh = () => {
    const query = input.value.trim();
    clearTimeout(searchTimer);
    if (settle) { settle(); settle = null; }
    if (document.activeElement !== input || SEPARATORS.test(query)) { close(); return; }
    if (!query && !industry.value) { searchSerial++; pending = null; showStart(); return; }
    const ticket = ++searchSerial;
    // 舊的建議立刻清掉，避免在新結果回來前選到舊清單的項目。
    render([], null, '搜尋中…');
    highlight(-1);
    pending = new Promise(resolve => {
      settle = resolve;
      searchTimer = setTimeout(() => {
        searchStocks(query, industry.value).then(result => {
          if (ticket !== searchSerial || document.activeElement !== input) return;
          for (const stock of result.stocks) if (!known.has(stock.stock_id)) known.set(stock.stock_id, stock);
          const unlisted = result.unlisted_code ? '名單中沒有 ' + result.unlisted_code + '；按 Enter 仍可加入' : '找不到符合的股票';
          render([{title: null, stocks: result.stocks}], result.total ? result.summary : null, unlisted);
          highlight(options.length && query ? 0 : -1);
        }).catch(() => {
          if (ticket !== searchSerial) return;
          render([], null, '搜尋失敗；仍可直接輸入或貼上代號');
        }).finally(() => { if (ticket === searchSerial) { pending = null; settle = null; } resolve(); });
      }, SEARCH_DELAY);
    });
  };

  const choose = index => {
    const stock = options[index];
    if (!stock) return;
    report(addCodes([stock.stock_id]));
    input.value = '';
    refresh();
  };

  // 把輸入框裡的文字當成一個或多個代號加入；有無效內容時保留在輸入框讓使用者修正。
  const commitText = text => {
    const result = addCodes(text.split(SEPARATORS));
    report(result);
    input.value = result.invalid.join(' ');
    refresh();
  };

  // ----- 事件 -----
  control.addEventListener('mousedown', event => {
    if (event.target === control || event.target === tags) { event.preventDefault(); input.focus(); }
  });
  input.addEventListener('focus', refresh);
  industry.addEventListener('change', () => { input.focus(); refresh(); });
  input.addEventListener('input', () => { say(''); refresh(); });
  input.addEventListener('blur', close);

  input.addEventListener('paste', event => {
    const text = (event.clipboardData || window.clipboardData).getData('text').trim();
    // 只有一個詞且不是完整代號時，當成一般輸入（例如貼上名稱來搜尋）。
    if (!SEPARATORS.test(text) && !CODE.test(text)) return;
    event.preventDefault();
    commitText(text);
  });

  input.addEventListener('keydown', event => {
    if (event.isComposing) return;      // 注音／拼音選字中，不攔截按鍵
    if (pending && (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Enter')) {
      // 搜尋還沒回來：等結果出來再處理這個按鍵。
      if (event.key === 'Enter' && !input.value.trim()) return;
      event.preventDefault();
      const key = event.key;
      pending.then(() => input.dispatchEvent(new KeyboardEvent('keydown', {key, bubbles: true})));
      return;
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (list.hidden) { refresh(); return; }
      if (!options.length) return;
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      highlight((active + step + options.length) % options.length);
    } else if (event.key === 'Enter') {
      const text = input.value.trim();
      if (!text && active < 0) return;  // 沒有輸入時不攔截，讓表單照常送出
      event.preventDefault();
      if (active >= 0) choose(active);
      else commitText(text);
    } else if (event.key === 'Escape') {
      if (!list.hidden) { event.preventDefault(); close(); }
    } else if (event.key === 'Backspace' && !input.value && chosen.length) {
      event.preventDefault();
      removeCode(chosen[chosen.length - 1]);
    } else if ((event.key === ' ' || event.key === ',') && CODE.test(input.value.trim())) {
      event.preventDefault();           // 打完代號按空白或逗號，直接變成標籤
      commitText(input.value);
    }
  });

  clearAll.addEventListener('click', () => {
    chosen.length = 0;
    sync();
    say('已清除全部');
    input.focus();
  });

  addWatchlist.addEventListener('click', async () => {
    addWatchlist.disabled = true;
    try {
      const codes = await loadWatchlist();
      if (!codes.length) { say('自選清單目前是空的'); return; }
      const result = addCodes(codes);
      if (result.overflow.length) say('自選共 ' + codes.length + ' 檔；已加入 ' + result.added.length + ' 檔，其餘 ' + result.overflow.length + ' 檔超過 ' + max + ' 檔上限未加入', true);
      else if (!result.added.length) say('自選股都已在清單中');
      else say('已加入 ' + result.added.length + ' 檔自選股');
      refresh();
    } catch {
      say('自選清單載入失敗；仍可輸入或貼上代號', true);
    } finally {
      addWatchlist.disabled = false;
    }
  });

  // ----- 啟動 -----
  for (const code of chosen) known.set(code, {stock_id: code});
  sync();
  loadStocks().then(data => {
    for (const stock of data.stocks) known.set(stock.stock_id, stock);
    for (const item of data.industries || []) industry.append(new Option(item.name + '（' + item.count + '）', item.name));
    sync();                             // 補上已選股票的名稱
    refresh();
  }).catch(() => say('名稱清單載入失敗；仍可輸入或貼上代號', true));
  // 自選股只用於沒有輸入時的建議；載入失敗時就只列最近查看。
  loadWatchlist().then(codes => { watched = codes; refresh(); }).catch(() => {});
}

window.StockPicker = {mount};
})();
