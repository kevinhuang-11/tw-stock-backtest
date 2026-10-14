"""Finite, resumable official supplemental downloads; no strategy integration."""
import argparse
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from tw_stock_backtest.config import DEFAULT_CONFIG_PATH, load_config
from tw_stock_backtest.cli.download_universe import save_progress
from tw_stock_backtest.data.supplemental import Client, Store, institution_plan, parse_revenue, parse_revenue_history, parse_institution, timestamp, listed_scope, validate_month_range
from tw_stock_backtest.security import safe_text


def update(settings, stocks, database, output, *, end, start=None, initial_days=35,
           refresh_days=7, kinds=('revenue','institution'), revenue_start=None,
           revenue_end=None, resume=None, client=None, revenue_source="latest"):
    if revenue_source not in ("latest","history"): raise ValueError("營收來源無效")
    validate_month_range(revenue_start,revenue_end)
    if not kinds or any(k not in ('revenue','institution') for k in kinds): raise ValueError('更新種類無效')
    universe=listed_scope(settings,stocks)
    store=Store(database)
    identity={'universe':universe,'schema':1,'stocks':sorted(stocks),'database':str(Path(database).resolve()),
              'end':str(end),'start':str(start) if start else None,'initial_days':initial_days,
              'refresh_days':refresh_days,'kinds':list(kinds),'download':settings['download'],
              'revenue_start':revenue_start,'revenue_end':revenue_end}
    if revenue_source != 'latest': identity['revenue_source']=revenue_source
    identity=json.loads(json.dumps(identity,default=str))
    if resume:
        progress=json.loads(Path(resume).read_text(encoding='utf-8'))
        if progress['identity']!=identity: raise ValueError('進度檔的日期、股票池、資料庫或設定不同')
        directory=Path(resume).parent
    else:
        directory=Path(output)/(datetime.now().strftime('%Y%m%dT%H%M%S')+'_'+uuid4().hex[:8])
        directory.mkdir(parents=True,exist_ok=False)
        days=institution_plan(store,stocks,end,start,initial_days,refresh_days) if 'institution' in kinds else []
        progress={'identity':identity,'run_id':directory.name,'started_at':timestamp(),
                  'tasks':(([{'kind':'revenue','period':sid} for sid in stocks] if revenue_source=='history' else [{'kind':'revenue','period':'latest'}]) if 'revenue' in kinds else [])+
                          [{'kind':'institution','period':d} for d in days], 'results':{},'complete':False}
    progress['complete']=False
    progress_path=directory/'supplemental_progress.json'
    save_progress(progress,progress_path)
    client=client or Client(settings['download'])
    for task in progress['tasks']:
        kind,period=task['kind'],task['period']; key=kind+':'+period
        # Successful/empty responses are complete for this exact job. A new run
        # deliberately refreshes recent periods; failed/partial tasks retry.
        if progress['results'].get(key,{}).get('status') in ('success','no_data'): continue
        historical=kind=='revenue' and revenue_source=='history'
        task_stocks=[period] if historical else stocks
        try:
            payload=client.get('revenue_history' if historical else kind,None if period=='latest' else period)
            rows=parse_revenue_history(payload,period,timestamp()) if historical else parse_revenue(payload,stocks,timestamp()) if kind=='revenue' else parse_institution(payload,period,stocks,timestamp())
            if kind=='revenue':
                rows=[r for r in rows if (not revenue_start or r['period']>=revenue_start) and (not revenue_end or r['period']<=revenue_end)]
            if historical:
                rows=[r for r in rows if r['period']<=end.isoformat()[:7]][-24:]
            store.save('revenue_history' if historical else kind,rows)
            found={r['stock_id'] for r in rows}
            item={'status':'success' if len(found)==len(task_stocks) else 'partial' if found else 'no_data',
                  'rows':len(rows),'periods':sorted({r['period'] for r in rows}),
                  'stocks':{sid:{'status':'success' if sid in found else 'no_data',
                           'reason':None if sid in found else '官方回應未提供此股票／期間；原因未知，不判定尚未公布或零值'} for sid in task_stocks}}
            if historical:
                item['source']='TWSE IIH'
                item['limitation']='IIH 僅回傳滾動歷史（本次實際月份另列），不支援任意起月；口徑與公告时间未知。缺少精確去年同期時年增率不提供。'
                if revenue_start:
                    current=date.fromisoformat(revenue_start+'-01'); expected=[]
                    while current.isoformat()[:7]<=revenue_end:
                        expected.append(current.isoformat()[:7])
                        current=date(current.year+int(current.month==12),current.month%12+1,1)
                    item['missing_months']=sorted(set(expected)-{r['period'] for r in rows})
                    if item['missing_months']: item['status']='partial' if rows else 'no_data'
            elif kind=='revenue':
                item['limitation']='此端點僅提供目前快照；指定歷史月份未提供時不代表尚未公布。不可補齊任意歷史範圍。'
                if revenue_start and revenue_end and (revenue_start!=revenue_end or not rows):
                    item['status']='partial' if rows else 'no_data'
        except Exception as error:
            item={'status':'failed','error_type':type(error).__name__,
                  'reason':'下載、解析或批次寫入失敗；保留前次成功資料', 'stocks':{sid:{'status':'failed'} for sid in task_stocks}}
        item['finished_at']=timestamp()
        progress['results'][key]=item
        save_progress(progress,progress_path)
        print(kind,period,item['status'],flush=True)
    counts=Counter(r['status'] for r in progress['results'].values())
    good=counts['success']
    has_rows=good or any(r.get('rows',0)>0 for r in progress['results'].values())
    code=0 if good==len(progress['tasks']) else 2 if has_rows else 1
    progress.update(complete=True,finished_at=timestamp(),counts=dict(counts),exit_code=code,
                    status={0:'success',2:'partial',1:'failed'}[code])
    store.save_update_states(progress)
    save_progress(progress,progress_path)
    # A separate fixed-name state contains no notes or credentials and is not a
    # report. It lets pages show a failed latest refresh without discarding rows.
    save_progress(progress,Path(database).with_suffix('.status.json'))
    return progress,progress_path


def main(argv=None):
    p=argparse.ArgumentParser(description='更新研究用月營收與法人資料（不加入排名或回測）')
    p.add_argument('--config',default=str(DEFAULT_CONFIG_PATH))
    group=p.add_mutually_exclusive_group()
    group.add_argument('--stocks',nargs='+')
    group.add_argument('--watchlist',help='私人自選 SQLite；CLI 使用，網頁不需路徑')
    p.add_argument('--database',help='獨立補充資料 SQLite，預設行情資料庫旁 supplemental.sqlite3')
    p.add_argument('--output-dir',default='reports/supplemental')
    p.add_argument('--start',type=date.fromisoformat)
    p.add_argument('--end',type=date.fromisoformat,default=datetime.now(ZoneInfo('Asia/Taipei')).date())
    p.add_argument('--initial-days',type=int,default=35)
    p.add_argument('--refresh-days',type=int,default=7)
    p.add_argument('--kind',choices=['both','revenue','institution'],default='both')
    p.add_argument('--revenue-source',choices=['latest','history'],default='latest',help='latest 為 OpenAPI 快照；history 為 IIH 滾動歷史，分開保存')
    p.add_argument('--revenue-start',help='YYYY-MM；篩選來源實際提供的月份')
    p.add_argument('--revenue-end',help='YYYY-MM；篩選來源實際提供的月份')
    p.add_argument('--show-summary',help='只讀取既有 supplemental_progress.json，不下載或接續')
    p.add_argument('--resume',help='supplemental_progress.json；重試失敗／部分項目')
    args=p.parse_args(argv)
    try:
        import re
        if args.show_summary:
            report=json.loads(Path(args.show_summary).read_text(encoding='utf-8'))
            if report.get('identity',{}).get('schema')!=1 or not isinstance(report.get('results'),dict):
                raise ValueError('不是支援的補充資料摘要')
            print('工作完成：'+str(report.get('complete',False))+'；狀態：'+safe_text(str(report.get('status','incomplete'))))
            for key,item in report['results'].items():
                print(safe_text(key+'：'+item['status']+'；筆數 '+str(item.get('rows',0))))
                if item.get('reason'): print(safe_text(item['reason']))
                for sid,result in item.get('stocks',{}).items():
                    if result['status']!='success': print(safe_text(sid+'：'+result['status']+'；'+str(result.get('reason') or '原因詳見工作摘要')))
                if item.get('limitation'): print(safe_text(item['limitation']))
            return 0
        settings=load_config(args.config)
        stocks=args.stocks or settings['universe']['stocks']
        if args.watchlist:
            from tw_stock_backtest.stock_research import Watchlist
            if not Path(args.watchlist).is_file(): raise ValueError('自選清單不存在')
            stocks=[x['stock_id'] for x in Watchlist(args.watchlist).items()]
        if not stocks or len(stocks)>100 or len(set(stocks))!=len(stocks) or any(not re.fullmatch(r'[1-9][0-9]{3}',s) for s in stocks):
            raise ValueError('請提供 1～100 個不重複的上市普通股代號；不支援 ETF')
        if args.end>datetime.now(ZoneInfo('Asia/Taipei')).date(): raise ValueError('截止日不可在未來')
        validate_month_range(args.revenue_start,args.revenue_end)
        database=args.database or Path(settings['storage']['database_path']).with_name('supplemental.sqlite3')
        kinds=('revenue','institution') if args.kind=='both' else (args.kind,)
        result,path=update(settings,stocks,database,args.output_dir,end=args.end,start=args.start,
                           initial_days=args.initial_days,refresh_days=args.refresh_days,kinds=kinds,
                           revenue_start=args.revenue_start,revenue_end=args.revenue_end,resume=args.resume,revenue_source=args.revenue_source)
        print(f"狀態：{result['status']}；摘要：{path}")
        return result['exit_code']
    except (ValueError,OSError,KeyError) as error:
        print('補充資料更新無法開始：'+safe_text(str(error)))
        return 1


if __name__=='__main__':
    raise SystemExit(main())
