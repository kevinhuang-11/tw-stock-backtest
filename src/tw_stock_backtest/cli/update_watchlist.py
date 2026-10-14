"""Orchestrate existing price and supplemental updates, preserving separate outcomes."""
import argparse
from datetime import date
from pathlib import Path
from uuid import uuid4
from tw_stock_backtest.config import load_config, DEFAULT_CONFIG_PATH
from tw_stock_backtest.research import update_stocks, taipei_today
from tw_stock_backtest.cli.update_supplemental import update
from tw_stock_backtest.cli.download_universe import save_progress
from tw_stock_backtest.data.supplemental import timestamp, listed_scope, institution_plan, Store, validate_month_range


def run(settings, stocks, output, *, kind='all', end=None, start=None,
        revenue_source='history', revenue_start=None, revenue_end=None):
    end=end or taipei_today()
    if kind not in ('all','prices','both','revenue','institution'): raise ValueError('更新種類無效')
    if not 1<=len(stocks)<=10 or len(set(stocks))!=len(stocks): raise ValueError('更新需 1～10 檔')
    if end>taipei_today(): raise ValueError('截止日不可在未來')
    if start and (start>end or (end-start).days>=90): raise ValueError('範圍限 90 日')
    validate_month_range(revenue_start,revenue_end)
    if revenue_source not in ('latest','history'): raise ValueError('營收來源無效')
    universe=listed_scope(settings,stocks)
    database=Path(settings['storage']['database_path']).with_name('supplemental.sqlite3')
    if kind in ('all','both','institution'): institution_plan(Store(database),stocks,end,start)
    directory=Path(output)/uuid4().hex;directory.mkdir(parents=True)
    progress={'run_id':directory.name,'identity':{'stocks':stocks,'kind':kind,'universe':universe},
              'started_at':timestamp(),'complete':False,'results':{}}
    path=directory/'supplemental_progress.json'
    save_progress(progress,path)
    if kind in ('all','prices'):
        def record(items):
            for item in items:
                progress['results']['prices:'+item['stock_id']]={'status':item['status'],'stocks':{item['stock_id']:item},'rows':item['saved_rows']}
            save_progress(progress,path)
        try:
            update_stocks(universe["stocks"],settings,end,start=start,initial_days=90,on_progress=record)
        except (ValueError,OSError) as error:
            progress['results']['prices:error']={'status':'failed','stocks':{sid:{'status':'failed'} for sid in stocks},'reason':'行情更新未完成','error_type':type(error).__name__}
    if kind!='prices':
        kinds=('revenue','institution') if kind in ('all','both') else (kind,)
        try:
            result,subpath=update(settings,stocks,database,directory,end=end,start=start,kinds=kinds,
                revenue_source=revenue_source,revenue_start=revenue_start,revenue_end=revenue_end)
            progress['results'].update(result['results'])
            progress['supplemental_report']=str(subpath.relative_to(directory))
        except (ValueError,OSError) as error:
            progress['results']['supplemental:error']={'status':'failed','stocks':{},'reason':'補充資料更新未完成','error_type':type(error).__name__}
    values=list(progress['results'].values())
    good=any(v.get('rows',0)>0 or v['status']=='success' for v in values)
    code=0 if values and all(v['status']=='success' for v in values) else 2 if good else 1
    progress.update(complete=True,finished_at=timestamp(),exit_code=code,status={0:'success',2:'partial',1:'failed'}[code])
    if kind in ('all','prices'):
        Store(database).save_update_states({**progress,'identity':{**progress['identity'],'kinds':['prices']}})
    save_progress(progress,path)
    return progress,path


def main(argv=None):
    p=argparse.ArgumentParser(description='更新指定自選股票池：行情／歷史營收／法人；不產生排名、不寄信')
    p.add_argument('--config',default=str(DEFAULT_CONFIG_PATH));p.add_argument('--stocks',nargs='+')
    p.add_argument('--output-dir',default='reports/watchlist-updates')
    p.add_argument('--kind',choices=['all','prices','both','revenue','institution'],default='all')
    p.add_argument('--start',type=date.fromisoformat);p.add_argument('--end',type=date.fromisoformat,default=taipei_today())
    p.add_argument('--revenue-source',choices=['history','latest'],default='history')
    p.add_argument('--revenue-start');p.add_argument('--revenue-end')
    args=p.parse_args(argv);settings=load_config(args.config)
    result,path=run(settings,args.stocks or settings['universe']['stocks'],args.output_dir,kind=args.kind,
        end=args.end,start=args.start,revenue_source=args.revenue_source,revenue_start=args.revenue_start,revenue_end=args.revenue_end)
    print('更新摘要：',path,'；狀態：',result['status'])
    return result['exit_code']

if __name__=='__main__': raise SystemExit(main())
