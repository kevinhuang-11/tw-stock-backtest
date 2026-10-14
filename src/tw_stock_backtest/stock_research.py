"""Local single-stock views: existing indicators, immutable reports and private notes."""
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

from tw_stock_backtest.analysis.indicators import (
    simple_moving_average, exponential_moving_average, relative_strength_index,
    moving_average_convergence_divergence,
)
from tw_stock_backtest.data.database import load_records
from tw_stock_backtest.data.universe import load_universe
from tw_stock_backtest.research_digest import load_bundle, differences
from tw_stock_backtest.strategies import analyze_strategy, from_settings, snapshot
from tw_stock_backtest.security import public_data
from tw_stock_backtest.web_forms import job_form, FormErrors

MAX_ROWS = 10000
MAX_REPORTS = 100
CONDITION_LABELS = {'rsi': 'RSI 位於上下限（含邊界）', 'macd': 'MACD > 訊號線',
                    'breakout': '收盤 > 前 N 筆最高價（不含當日）',
                    'trend': '收盤 > 短均線 > 長均線', 'volume': '量比 > 門檻',
                    'legacy_rules': '原有均線及成交量條件'}


def stock_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}', value):
        raise ValueError('股票代號需為四位數')
    return value


def today():
    return datetime.now(ZoneInfo('Asia/Taipei')).date().isoformat()


def parse_day(value):
    day = date.fromisoformat(value).isoformat()
    if day != value or day > today():
        raise ValueError('日期格式不符或晚於台灣當日')
    return day


def clean(value):
    return public_data(json.loads(json.dumps(value, default=str, ensure_ascii=False)))


class NoteConflict(ValueError):
    pass


class Watchlist:
    """Separate local DB with optimistic revisions; never stored in a research report."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError('私人資料庫不可為符號連結')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS watchlist (stock_id TEXT PRIMARY KEY, note TEXT NOT NULL, added_at TEXT NOT NULL, updated_at TEXT NOT NULL, revision INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1)')

    def items(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute('SELECT * FROM watchlist WHERE active=1 ORDER BY stock_id')]

    def get(self, sid):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM watchlist WHERE stock_id=?', (stock_id(sid),)).fetchone()
            return dict(row) if row else {'stock_id':sid, 'note':'', 'revision':0, 'active':0}

    def save(self, sid, note, revision, remove=False):
        sid = stock_id(sid)
        if not isinstance(note, str) or len(note) > 600:
            raise ValueError('筆記最多 600 字')
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision FROM watchlist WHERE stock_id=?', (sid,)).fetchone()
            if int(revision) != (row[0] if row else 0):
                raise NoteConflict('此筆記已在其他頁面更新；請重新載入後再修改')
            active = db.execute('SELECT active FROM watchlist WHERE stock_id=?', (sid,)).fetchone()
            if not remove and (not active or not active[0]) and db.execute('SELECT COUNT(*) FROM watchlist WHERE active=1').fetchone()[0] >= 100:
                raise ValueError('自選清單最多 100 檔')
            stamp = datetime.now(ZoneInfo('Asia/Taipei')).isoformat()
            # Tombstones keep revisions across remove/re-add, preventing stale writes.
            db.execute('INSERT INTO watchlist VALUES (?,?,?,?,?,?) ON CONFLICT(stock_id) DO UPDATE SET note=excluded.note,updated_at=excluded.updated_at,revision=excluded.revision,active=excluded.active',
                       (sid, '' if remove else note, stamp, stamp, int(revision)+1, int(not remove)))
        return self.get(sid)


def identities(wb):
    result = {sid: {'stock_id':sid} for sid in wb.settings['universe']['stocks']}
    dbpath = Path(wb.settings['storage']['database_path'])
    if dbpath.is_file():
        with closing(sqlite3.connect(dbpath.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            for (sid,) in db.execute('SELECT DISTINCT stock_id FROM stock_prices LIMIT 3000'):
                if re.fullmatch(r'[0-9]{4}', sid): result[sid] = {'stock_id':sid}
    path = dbpath.parent / 'listed_stocks.json'
    if path.is_file() and not path.is_symlink():
        for item in load_universe(path)['stocks'][:3000]:
            result[item['stock_id']] = {k:item.get(k) for k in ('stock_id','name','industry')}
    return result


def report_paths(wb):
    paths = set()
    for root in (wb.reports, wb.state):
        if root.exists():
            paths.update(p for p in root.rglob('summary.json')
                         if not p.is_symlink() and p.resolve().is_relative_to(root)
                         and not any(part.endswith('.incomplete') for part in p.parts))
    return paths


def checked_bundle(path):
    # Limit the whole saved bundle, not just its summary; no arbitrary user paths.
    files = [path.parent / name for name in ('summary.json','rankings.json','universe.json','settings.json','rankings.csv')]
    size = sum(p.stat().st_size for p in files if p.is_file())
    if size > 10_000_000 or any(p.is_symlink() for p in files):
        raise ValueError('報表過大或含符號連結')
    return load_bundle(path), size


def report_bundles(wb):
    selected = sorted(report_paths(wb), key=lambda p:(p.stat().st_mtime_ns,str(p)), reverse=True)[:MAX_REPORTS]
    result = {}; used = 0
    for path in selected:
        try:
            bundle, size = checked_bundle(path)
            used += size
            if used > 50_000_000: break
            if bundle and bundle['summary'].get('analysis_date'):
                key = sha256(str(path.parent).encode()).hexdigest()[:24]
                result[key] = bundle
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result


def saved_stock(bundle, sid):
    summary = bundle['summary']
    row = next((r for r in bundle['rankings'] if r.get('stock_id') == sid), None)
    evaluation = next((r for r in summary.get('evaluations', []) if r.get('stock_id') == sid), None)
    excluded = next((r for r in summary.get('excluded', []) if r.get('stock_id') == sid), None)
    status = (evaluation or {}).get('status')
    if status is None:
        status = 'unavailable' if excluded else 'eligible' if row else 'not_saved'
    return {'rank': row.get('rank') if row else None, 'score': row.get('score') if row else None,
            'status':status, 'evaluation':evaluation, 'reason':(excluded or evaluation or {}).get('reason'),
            'factors':row.get('factors') if row else None}


def stock_history(bundles, sid):
    # One report per day: latest finished_at, then run_id, then stable report key.
    selected = {}
    for key, bundle in bundles.items():
        s = bundle['summary']; day = s['analysis_date']
        order = (s.get('finished_at') or '', s.get('run_id') or '', key)
        if day not in selected or order > selected[day][0]: selected[day] = (order,key,bundle)
    result = []; previous = None
    for day, (_,key,bundle) in sorted(selected.items()):
        value = saved_stock(bundle,sid)
        limitations = differences(bundle,previous[0]) if previous else ['沒有更早的研究報表']
        if bundle['summary'].get('status') not in ('success','partial') or (previous and previous[0]['summary'].get('status') not in ('success','partial')):
            limitations.append('研究未成功完成排名')
        change = None
        if previous and not limitations and value['rank'] is not None and previous[1]['rank'] is not None:
            change = previous[1]['rank'] - value['rank']
        result.append({**value, 'date':day,'report_key':key,
                       'strategy_snapshot':bundle['summary'].get('strategy_snapshot'),
                       'population':len(bundle['summary']['ranking_population']) if 'ranking_population' in bundle['summary'] else None,
                       'change':change,'limitations':limitations})
        previous = (bundle,value)
    return list(reversed(result))


def chart_data(records, strategy, display=120):
    """Display slicing happens AFTER calculation. A gap never resets recursive seeds."""
    if display not in (60,120,240,600): raise ValueError('顯示範圍不支援')
    closes = [r['close'] for r in records]
    settings = strategy['factors'] if strategy['ranking_mode']=='factors' else strategy['screening']
    n = settings['long_window']
    conditions = {c['type']:c for c in strategy['conditions']}
    rsi_n = conditions.get('rsi', {}).get('period',14)
    macd = conditions.get('macd', {'fast':12,'slow':26,'signal':9})
    first_bad = next((i for i,c in enumerate(closes) if not isinstance(c,Decimal) or not c.is_finite() or c<=0),len(closes))
    prefix = closes[:first_bad]
    sma = []
    for i in range(len(closes)):
        try: sma.append(simple_moving_average(closes[max(0,i-n+1):i+1],n)[-1])
        except ValueError: sma.append(None)
    ema = exponential_moving_average(prefix,n) + [None]*(len(closes)-first_bad)
    rsi = relative_strength_index(prefix,rsi_n) + [None]*(len(closes)-first_bad)
    values = moving_average_convergence_divergence(prefix,macd['fast'],macd['slow'],macd['signal'])
    values += [{'macd':None,'signal':None,'histogram':None} for _ in range(len(closes)-first_bad)]
    rows = [{**r,'sma':sma[i],'ema':ema[i],'rsi':rsi[i],**values[i]} for i,r in enumerate(records)]
    reasons = []
    if first_bad<len(closes): reasons.append(f"{records[first_bad]['date']} 第 {first_bad+1} 筆收盤缺失或無效，此日起 EMA／RSI／MACD 不計算、不重置；SMA 僅檢查當期窗口。")
    if rows:
        for field in ('sma','ema','rsi','macd','signal'):
            if rows[-1][field] is None: reasons.append(f'{field} 在最後行情日缺值或暖機不足')
    else: reasons.append('沒有可用行情')
    return clean({'rows':rows[-display:],'calculation_rows':len(records),
                  'calculation_start':records[0]['date'] if records else None,
                  'parameters':{'sma_ema':n,'rsi':rsi_n,'macd':macd}, 'reasons':reasons,
                  'price_adjustment':'TWSE 原始行情，未做除權息／拆股調整'})


def current_stock(wb, sid, args):
    sid = stock_id(sid)
    cutoff = parse_day(args['as_of']) if args.get('as_of') else today()
    dbpath = Path(wb.settings['storage']['database_path'])
    records = []
    if dbpath.is_file():
        with closing(sqlite3.connect(dbpath.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            count = db.execute('SELECT COUNT(*) FROM stock_prices WHERE stock_id=? AND date<=?',(sid,cutoff)).fetchone()[0]
        if count>MAX_ROWS: raise ValueError('單檔超過 10000 筆；不裁切遞迴暖機，請使用 CLI')
        records = load_records(sid,'1900-01-01',cutoff,db_path=dbpath)
    target = cutoff if args.get('as_of') else records[-1]['date'] if records else cutoff
    strategy = wb.library.load(args['strategy_id']) if args.get('strategy_id') else from_settings(wb.settings)
    analysis = analyze_strategy({sid:records},target,strategy)
    scored = analysis['rankings']
    return {'stock_id':sid,'metadata':identities(wb).get(sid,{}), 'date':target,
            'latest':records[-1]['date'] if records else None,
            'source':'依目前資料庫重新計算；指定歷史日期' if args.get('as_of') else '最新可用資料（不代表今日行情）',
            'lag_days':(date.fromisoformat(today())-date.fromisoformat(target)).days,
            'strategy_snapshot':snapshot(strategy),'diagnostic':analysis['diagnostics'].get(sid),
            'factors':scored[0].get('factors') if scored else None,
            'chart':chart_data(records,strategy,int(args.get('display',120))),
            'ranking':None,'pool':None,'report_key':None}


def historic_stock(sid, key, bundle):
    saved = saved_stock(bundle,sid)
    metadata = next((s for s in bundle['universe'].get('stocks',[]) if s['stock_id']==sid),{})
    return {'stock_id':sid,'metadata':metadata,'date':bundle['summary']['analysis_date'],
            'latest':(saved['evaluation'] or {}).get('actual',{}).get('latest'),
            'source':'原研究報表快照；未重新計算','strategy_snapshot':bundle['summary'].get('strategy_snapshot'),
            'diagnostic':saved['evaluation'],'factors':saved['factors'], 'ranking':saved,
            'pool':bundle['universe'].get('stocks'),
            'population':len(bundle['summary']['ranking_population']) if 'ranking_population' in bundle['summary'] else None,
            'chart':{'rows':[], 'reasons':['此研究報表未保存完整 K 線快照，不使用現在資料庫代替。'],
                     'price_adjustment':'原研究資料未保存價格調整資訊'}, 'report_key':key}


def register_stock_routes(app, wb, token):
    from flask import abort, jsonify, redirect, render_template, request, url_for
    from tw_stock_backtest.config import apply_overrides
    notes = Watchlist(wb.state / 'private' / 'watchlist.sqlite3')
    from tw_stock_backtest.watchlist_research import register
    register(app,wb,notes,token)

    def view(sid):
        stock_id(sid)
        bundles = report_bundles(wb)
        key = request.args.get('report')
        if key:
            if not re.fullmatch(r'[a-f0-9]{24}',key): abort(404)
            if key not in bundles:
                for path in report_paths(wb):
                    if sha256(str(path.parent).encode()).hexdigest()[:24] == key:
                        bundle, _ = checked_bundle(path)
                        if bundle: bundles[key] = bundle
                        break
            if key not in bundles: abort(404)
            if request.args.get('as_of') or request.args.get('strategy_id'):
                raise ValueError('報表模式不可覆寫日期或策略；請明確重新計算')
            data = historic_stock(sid,key,bundles[key])
        else:
            data = current_stock(wb,sid,request.args)
        history = [r for r in stock_history(bundles,sid) if r['date']<=data['date']]
        chosen = request.args.get('history_day')
        if chosen:
            chosen = parse_day(chosen)
            history = [r for r in history if r['date']==chosen]
        return clean(data), history, notes.get(sid)

    @app.get('/api/watchlist-options')
    def watchlist_options():
        # 只回傳代號供股票選擇器使用；私人筆記不離開自選頁。
        try:
            return {'stocks':[r['stock_id'] for r in notes.items()]}
        except (OSError,sqlite3.Error):
            return {'error':'自選清單無法讀取'},503

    @app.get('/stocks')
    def stock_search():
        query = request.args.get('q','').strip()
        if len(query)>40: abort(400)
        try:
            items = [r for sid,r in sorted(identities(wb).items())
                     if query in sid or query in (r.get('name') or '')][:30]
            if re.fullmatch(r'[0-9]{4}',query) and not any(r['stock_id']==query for r in items):
                items.insert(0,{'stock_id':query})
        except (OSError,ValueError,sqlite3.Error):
            return '本機名單或行情無法讀取；未下載資料',503
        return render_template('stock_search.html',items=items,query=query)

    @app.get('/stocks/<sid>')
    def stock_page(sid):
        try:
            data, history, note = view(sid)
        except (ValueError,OSError,sqlite3.Error,KeyError,TypeError):
            return '個股查詢參數、策略或本機資料無法使用；未下載資料',400
        from tw_stock_backtest.supplemental_views import panel_context
        supplement=panel_context(wb,sid,today())
        template = 'stock_panel.html' if request.args.get('fragment')=='1' else 'stock.html'
        return render_template(template, stock=data, history=history, note=note, supp=supplement,
                               strategies=wb.library.list(), csrf=token, condition_labels=CONDITION_LABELS,
                               execution=clean({'backtest':wb.settings['backtest'],'costs':wb.settings['costs']}),
                               default_pool=' '.join(wb.settings['universe']['stocks']))

    @app.get('/watchlist')
    def watch_page():
        try:
            from tw_stock_backtest.watchlist_research import observations, filter_rows, previous_snapshots
            entries=notes.items()
            data=observations(wb,[r['stock_id'] for r in entries],request.args.get('strategy_id'),request.args.get('as_of'))
            rows=filter_rows(data['rows'],request.args.get('q',''),request.args.get('status',''),request.args.get('sort','stock_id'),request.args.get('direction')=='desc')
            by_id={r['stock_id']:r for r in data['rows']}
            for item in entries:
                r=by_id[item['stock_id']]
                item.update(metadata={'name':r['name']},latest=r['price_date'],research_date=(r['research'] or {}).get('date'))
            return render_template('watchlist.html',items=entries,rows=rows,data=data,csrf=token,
                strategies=wb.library.list(),snapshots=previous_snapshots(wb.state/'watchlist-reports')[:10])
        except (OSError,ValueError,sqlite3.Error):
            return '自選清單無法讀取',503

    @app.post('/watchlist/<sid>')
    def watch_save(sid):
        try:
            action=request.form.get('action','save')
            if action not in ('save','remove'): raise ValueError('未知動作')
            saved=notes.save(sid,request.form.get('note',''),request.form.get('revision','0'),action=='remove')
            if request.headers.get('Accept')=='application/json':
                return jsonify({'message':'已移除' if action=='remove' else '已儲存','revision':saved['revision'],'active':saved['active']})
            return redirect(url_for('watch_page'))
        except NoteConflict:
            return '筆記已被其他頁面更新；未覆蓋，請先重新載入並保留你的草稿',409
        except (ValueError,TypeError):
            return '股票、筆記或版本參數無效；未儲存',400
        except (OSError,sqlite3.Error):
            return '私人儲存空間無法寫入；未儲存',503

    @app.post('/stocks/<sid>/backtest')
    def stock_backtest(sid):
        # Confirmation and submission both go through validated CLI parameters.
        try:
            submitted=job_form(request.form)
            stock_id(sid)
            selection=submitted.get('strategy_id','')
            strategy=wb.library.load(selection) if selection else from_settings(wb.settings)
            form={'kind':'backtest','strategy_id':selection,'ranking':strategy['ranking_mode'],
                  'start':parse_day(submitted['start']),'end':parse_day(submitted['end'])}
            pool=submitted.get('stocks','').split()
            if not 1<=len(pool)<=10 or len(set(pool))!=len(pool) or sid not in pool: raise ValueError('股票池無效')
            for code in pool: stock_id(code)
            if form['start']>form['end']: raise ValueError('期間順序無效')
            form['stocks']=' '.join(pool)
            for key in ('cash','quantity','max_positions','sizing','slippage'):
                if submitted.get(key): form[key]=submitted[key]
            overrides={key:form.get(field) for field,key in (
                ('cash','initial_cash'),('quantity','quantity'),('max_positions','max_positions'),
                ('sizing','sizing_mode'),('slippage','slippage_rate'))}
            for key in ('quantity','max_positions'):
                if overrides[key] is not None: overrides[key]=int(overrides[key])
            settings=apply_overrides(wb.settings,{'universe':{'stocks':pool},'backtest':overrides})
            fingerprint=snapshot(strategy)['sha256']
            if submitted.get('confirm')=='yes':
                if submitted.get('strategy_hash')!=fingerprint:
                    return {'errors':{'strategy_id':'策略已變更，請重新確認；未建立工作'}},409
                # Reject silent outer-range shortening, and report which stock is short.
                for code in pool:
                    rows=load_records(code,form['start'],form['end'],db_path=settings['storage']['database_path'])
                    if not rows or rows[0]['date']!=form['start'] or rows[-1]['date']!=form['end']:
                        return {'errors':{'end':f'{code} 缺少指定起日或迄日行情；未改日期、未下載資料'}},400
                job_id=wb.submit(form)
                return redirect('/?job='+job_id)
            return render_template('stock_confirm.html',csrf=token,sid=sid,form=form,
                                   strategy=clean(snapshot(strategy)),execution=clean(settings['backtest']),
                                   costs=clean(settings['costs']),single=len(pool)==1)
        except FormErrors as error:
            return {'errors':error.errors},400
        except (ValueError,OSError,KeyError,TypeError,sqlite3.Error):
            return {'errors':{'_form':'回測參數、策略或本機行情無法使用；未建立工作'}},400
