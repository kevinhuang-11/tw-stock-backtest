"""Bounded watchlist observations; no new ranking, no notes, no downloads on GET."""
import csv
import io
import json
import sqlite3
from contextlib import closing
from datetime import date
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from tw_stock_backtest.cli.download_universe import save_progress
from tw_stock_backtest.data.database import load_records
from tw_stock_backtest.data.supplemental import timestamp
from tw_stock_backtest.research import program_version
from tw_stock_backtest.research_digest import render_digest, load_digest
from tw_stock_backtest.security import public_data
from tw_stock_backtest.stock_research import (identities, report_bundles, saved_stock,
    stock_id, today, parse_day, clean, MAX_ROWS)
from tw_stock_backtest.strategies import analyze_strategy, from_settings, snapshot
from tw_stock_backtest.supplemental_views import panel_context

LABELS = {'eligible':'通過', 'filtered':'未通過', 'unavailable':'無法評估'}


def observations(wb, stocks, strategy_id=None, as_of=None):
    stocks=sorted(set(stock_id(s) for s in stocks))
    if len(stocks)>100: raise ValueError('自選最多 100 檔')
    cutoff=parse_day(as_of) if as_of else today()
    strategy=wb.library.load(strategy_id) if strategy_id else from_settings(wb.settings)
    strategy_snapshot=snapshot(strategy)
    try: names=identities(wb)
    except (ValueError,OSError,sqlite3.Error): names={}
    bundles=report_bundles(wb)
    database=Path(wb.settings['storage']['database_path'])
    rows=[]
    for sid in stocks:
        row={'stock_id':sid,'name':names.get(sid,{}).get('name'),
             'industry':names.get(sid,{}).get('industry'),'close':None,'price_date':None,
             'condition_date':None,'status':'unavailable','diagnostic':None,
             'rank':None,'score':None,'research':None,'warnings':[]}
        records=[]
        try:
            if database.is_file():
                with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                    n=db.execute('SELECT COUNT(*) FROM stock_prices WHERE stock_id=? AND date<=?',(sid,cutoff)).fetchone()[0]
                if n>MAX_ROWS: raise ValueError('超過查詢上限')
                records=load_records(sid,'1900-01-01',cutoff,db_path=database)
            target=cutoff if as_of or not records else records[-1]['date']
            row['condition_date']=target
            if records:
                row.update(close=records[-1]['close'],price_date=records[-1]['date'])
                lag=(date.fromisoformat(today())-date.fromisoformat(records[-1]['date'])).days
                row['price_age_calendar_days']=lag
                if lag: row['warnings'].append(f'行情距台灣今日 {lag} 個日曆日；不等於缺漏交易日')
            # Relative scores from this calculation are deliberately discarded.
            diagnostic=analyze_strategy({sid:records},target,strategy)['diagnostics'].get(sid)
            row.update(diagnostic=diagnostic,status=(diagnostic or {}).get('status','unavailable'))
        except (OSError,ValueError,sqlite3.Error,KeyError,TypeError):
            row['warnings'].append('行情或策略條件無法評估；不影響其他來源')
        candidates=[]
        for key,bundle in bundles.items():
            summary=bundle['summary']
            if summary['analysis_date']>cutoff or summary.get('status') not in ('success','partial'): continue
            if (summary.get('strategy_snapshot') or {}).get('sha256')!=strategy_snapshot['sha256']: continue
            value=saved_stock(bundle,sid)
            if value['status']=='not_saved': continue
            candidates.append((summary['analysis_date'],summary.get('finished_at',''),key,bundle,value))
        if candidates:
            day,_,key,bundle,value=max(candidates,key=lambda x:x[:3])
            row.update(rank=value['rank'],score=value['score'],research={
                'report_key':key,'run_id':bundle['summary'].get('run_id'),'date':day,
                'strategy_sha256':strategy_snapshot['sha256'],
                'pool':[s['stock_id'] for s in bundle['universe'].get('stocks',[])],
                'population':bundle['summary'].get('ranking_population'), 'status':value['status']})
        panel=panel_context(wb,sid,cutoff)
        row.update(revenue=panel['revenue'][-1] if panel['revenue'] else None,
                   revenue_source=panel.get('revenue_source'),
                   institution=panel['institutions'][-1] if panel['institutions'] else None,
                   sums=panel['sums'],updates=panel['updates'])
        if row['institution']:
            age=(date.fromisoformat(today())-date.fromisoformat(row['institution']['period'])).days
            if age: row['warnings'].append(f'法人資料距台灣今日 {age} 個日曆日，不推定缺漏交易日')
        row['warnings']+=panel['warnings']
        row['complete']=bool(row['status']!='unavailable' and row['close'] is not None
                             and row['revenue'] and all(row['revenue'][k]['value'] is not None for k in ('mom','yoy'))
                             and len(row['sums'])==2 and all(c['complete'] for c in row['sums']))
        row['update_failed']=any(any(k in u['counts'] for k in ('failed','partial')) for u in row['updates'].values())
        rows.append(clean(row))
    return {'digest_schema_version':1,'digest_kind':'watchlist','kind':'watchlist',
            'title':'自選股研究總覽（目前保存版本；非全市場排名）',
            'generated_at':timestamp(),'cutoff':cutoff,'requested_date':as_of,
            'specified_pool':stocks,'strategy_snapshot':strategy_snapshot,'rows':rows,
            'counts':{'requested':len(rows),'complete':sum(r['complete'] for r in rows),
                      'eligible':sum(r['status']=='eligible' for r in rows),
                      'unavailable':sum(r['status']=='unavailable' for r in rows)},
            'version':program_version(), 'comparison':{'message':'尚未保存；保存後與前次快照比較','changes':[]}}


def compare(current, previous):
    result={'message':'首次紀錄：尚無前次快照','changes':[], 'previous_run_id':None}
    if previous is None: return result
    result['previous_run_id']=previous['run_id']
    compatible=(current['specified_pool']==previous['specified_pool'] and
                current['strategy_snapshot']['sha256']==previous['strategy_snapshot']['sha256'] and
                current.get('requested_date')==previous.get('requested_date'))
    result['message']='相同策略與自選股票池的可比較前次快照；不是獨立交易日樣本' if compatible else '策略、股票池或指定日期不同；不比較條件升降'
    old={r['stock_id']:r for r in previous['rows']}
    for row in current['rows']:
        before=old.get(row['stock_id'])
        if before is None: continue
        messages=[]
        if compatible and {before['status'],row['status']}=={'eligible','filtered'}:
            messages.append('條件改變：'+('新通過' if row['status']=='eligible' else '不再通過'))
        elif compatible and before['status']=='unavailable' and row['status']!='unavailable':
            messages.append('前次無法評估，本次取得可評估資料；不算新通過')
        for key,label in [('revenue','營收'),('institution','法人')]:
            a,b=before.get(key),row.get(key)
            if b and (not a or b['period']>a['period']): messages.append(label+'資料更新至 '+b['period'])
            elif a and b and a['period']==b['period']:
                if a.get('source')!=b.get('source') or a.get('basis')!=b.get('basis'):
                    messages.append(label+'來源／口徑改變，不判定修訂')
                elif a.get('raw')!=b.get('raw'): messages.append(label+'同期間數值修訂：'+b['period'])
        a,b=before.get('research'),row.get('research')
        if compatible and a and b and a['date']<b['date'] and all(a.get(k)==b.get(k) and a.get(k) is not None for k in ('pool','population','strategy_sha256')) and before['rank'] is not None and row['rank'] is not None:
            messages.append(f"可比較研究名次變化 {before['rank']-row['rank']:+d}（正數上升）")
        if messages: result['changes'].append({'stock_id':row['stock_id'],'messages':messages})
    return result


def previous_snapshots(root):
    rows=[]
    for p in sorted(Path(root).glob('*/summary.json'),reverse=True)[:100]:
        if p.is_symlink() or p.parent.name.endswith('.incomplete') or not p.resolve().is_relative_to(Path(root).resolve()): continue
        try:
            if p.stat().st_size>10_000_000: continue
            data=json.loads(p.read_text())
            if data.get('report_complete') and data.get('digest_kind')=='watchlist':
                data['report_key']=sha256(str(p.parent).encode()).hexdigest()[:24]
                rows.append(data)
        except (ValueError,OSError): continue
    return sorted(rows,key=lambda d:(d['generated_at'],d['run_id']),reverse=True)


def save_snapshot(data, root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    run_id=timestamp().replace(':','').replace('+','_')+'_'+uuid4().hex[:8]
    stage=root/(run_id+'.incomplete');stage.mkdir()
    histories=previous_snapshots(root)
    earlier=[p for p in histories if p['generated_at']<data['generated_at']]
    previous=next((p for p in earlier if p['specified_pool']==data['specified_pool'] and p['strategy_snapshot']['sha256']==data['strategy_snapshot']['sha256'] and p.get('requested_date')==data.get('requested_date')),earlier[0] if earlier else None)
    saved=public_data({**data,'run_id':run_id,'comparison':compare(data,previous),'report_complete':True})
    # All saved fields originate from the public observation allowlist, not notes.
    output=io.StringIO();writer=csv.writer(output)
    writer.writerow(['股票','行情日期','收盤','條件狀態','研究日期','研究名次','研究分數','營收月份','月增倍率','年增倍率'])
    for r in saved['rows']:
        rev=r.get('revenue') or {}
        writer.writerow([r['stock_id'],r['price_date'],r['close'],r['status'],(r['research'] or {}).get('date'),r['rank'],r['score'],rev.get('period'),rev.get('mom',{}).get('value'),rev.get('yoy',{}).get('value')])
    saved['ranking_csv']=output.getvalue()
    text,html=render_digest(saved)
    (stage/'report.html').write_text(html,encoding='utf-8')
    (stage/'report.txt').write_text(text,encoding='utf-8')
    (stage/'rankings.csv').write_text(output.getvalue(),encoding='utf-8')
    save_progress(saved,stage/'summary.json')
    final=root/run_id;stage.rename(final)
    return final


def filter_rows(rows, query='', status='', sort='stock_id', descending=False):
    if sort not in ('stock_id','close','rank','score','mom','yoy'): raise ValueError('排序欄位無效')
    if status not in ('','eligible','filtered','unavailable','incomplete','update_failed'): raise ValueError('狀態篩選無效')
    selected=[r for r in rows if query in r['stock_id'] or query in (r['name'] or '')]
    if status: selected=[r for r in selected if (not r['complete'] if status=='incomplete' else r['update_failed'] if status=='update_failed' else r['status']==status)]
    def value(r):
        v=((r.get('revenue') or {}).get(sort) or {}).get('value') if sort in ('mom','yoy') else r.get(sort)
        return None if v is None else v if sort=='stock_id' else Decimal(str(v))
    present=[r for r in selected if value(r) is not None]
    missing=[r for r in selected if value(r) is None]
    return sorted(present,key=lambda r:(value(r),r['stock_id']),reverse=descending)+sorted(missing,key=lambda r:r['stock_id'])


def digest_lines(d):
    def show(v): return '未提供' if v is None else str(v)
    def rate(v): return '資料不足' if not v or v.get('value') is None else str(Decimal(v['value'])*100)+'%'
    c=d['counts']
    lines=[d['title'],f"產生時間：{d['generated_at']}；期間截止 {d['cutoff']}",
        '策略：'+d['strategy_snapshot']['definition']['name']+'；SHA-256 '+d['strategy_snapshot']['sha256'],
        f"涵蓋 {c['requested']} 檔；欄位完整 {c['complete']} 檔；通過 {c['eligible']} 檔；無法評估 {c['unavailable']} 檔。",
        '完整指已具備所列數值，不保證市場資料齊全或最新。私人筆記不包含於此摘要。']
    for i,r in enumerate(d['rows'],1):
        lines+=['',f"{i}. {r['stock_id']} {show(r['name'])}｜{show(r['industry'])}",
            f"行情 {show(r['price_date'])}：收盤 {show(r['close'])}；條件日期 {show(r['condition_date'])}：{LABELS.get(r['status'],'無法評估')}"]
        diag=r.get('diagnostic') or {}
        lines+=['條件：'+json.dumps(x,ensure_ascii=False) for x in diag.get('conditions',[])]
        if diag.get('reason'): lines.append('原因：'+str(diag['reason']))
        ref=r['research']
        if ref: lines.append(f"保存研究 {ref['date']}：名次 {show(r['rank'])}，相對分數 {show(r['score'])}；指定股票池 {','.join(ref['pool'])}；有效母體 {len(ref['population']) if ref['population'] is not None else '未提供'}；來源報表 {ref['report_key']}")
        else: lines.append('尚無與目前策略相符且保存此股票的研究排名；未建立單檔相對分數。')
        rev=r['revenue']
        if rev: lines+= [f"營收 {rev['period']}：{show(rev['revenue'])} 千元；月增 {rate(rev['mom'])}；年增 {rate(rev['yoy'])}",f"来源 {rev['source']}；{rev['basis']}；抓取 {rev['fetched_at']}；公告時間未提供"]
        else: lines.append('營收未提供')
        inst=r['institution']
        lines.append('法人最新資料日期：'+show(inst['period'] if inst else None))
        for total in r['sums']:
            lines.append(f"法人 {total['window']} 日：{total['count']} 筆；基準 {','.join(total['expected_dates'])}；"+('依本機行情日期完整' if total['complete'] else '資料不足')+'；外資／投信／自營商（股）：'+'/'.join(show(total['totals'].get(k)) for k in ('foreign','trust','dealer')))
        lines+=r['warnings']
        for kind,u in r['updates'].items(): lines.append(f"{kind} 最近更新 {u['finished_at']}：{u['counts']}；{u['reasons']}")
    lines+=['','排名變化',d['comparison']['message']]
    for c in d['comparison']['changes']: lines.append(c['stock_id']+'：'+'；'.join(c['messages']))
    lines+=['','資料限制與研究提示','營收與法人僅展示目前保存版本；抓取時間不等於公告時間，不作歷史當時已知資訊。無獨立交易日曆；日期差為日曆日。相對分數不是上漲機率。','執行資訊','run_id：'+d.get('run_id','尚未保存'),'版本：'+json.dumps(d['version'],ensure_ascii=False)]
    return lines


def register(app, wb, notes, token):
    from flask import request, redirect, abort
    @app.post('/watchlist/snapshot')
    def watchlist_snapshot():
        try:
            stocks=[r['stock_id'] for r in notes.items()]
            if not stocks: raise ValueError('自選清單為空')
            data=observations(wb,stocks,request.form.get('strategy_id'),request.form.get('as_of'))
            with wb.lock:
                directory=save_snapshot(data,wb.state/'watchlist-reports')
            key=sha256(str(directory).encode()).hexdigest()[:24]
            return redirect('/reports/'+key,303)
        except (ValueError,OSError,sqlite3.Error):
            return {'errors':{'_form':'無法保存：請確認策略、日期及自選清單。原快照未覆寫。'}},400

    @app.post('/watchlist/update')
    def watchlist_update():
        try:
            stocks=[r['stock_id'] for r in notes.items()]
            if not stocks: raise ValueError('自選清單為空')
            form={'kind':'watchlist_update','stocks':' '.join(stocks),
                  'supplemental_kind':request.form.get('update_kind','all'),
                  'revenue_source':'history', 'end':request.form.get('end',''),
                  'start':request.form.get('start',''),
                  'revenue_start':request.form.get('revenue_start',''),
                  'revenue_end':request.form.get('revenue_end','')}
            job=wb.submit(form)
            return {'job_id':job,'summary_url':'/jobs/'+job+'/summary'},202
        except (ValueError,OSError):
            return {'errors':{'_form':'更新未建立：需 1～10 檔自選股、有效日期（最多 90 日）與種類。'}},400
