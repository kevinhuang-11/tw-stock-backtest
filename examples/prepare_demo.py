"""Create an isolated, synthetic offline demo using existing application APIs.

No network, no user DB/config/notes read, no overwrite. Not market evidence.
"""
import argparse
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from tw_stock_backtest.config import load_config
from tw_stock_backtest.data.database import save_records
from tw_stock_backtest.data.supplemental import Store, growth, timestamp
from tw_stock_backtest.stock_research import Watchlist
from tw_stock_backtest.strategies import StrategyLibrary, from_settings
from tw_stock_backtest.workbench import Workbench
from tw_stock_backtest.watchlist_research import observations, save_snapshot
from tw_stock_backtest.cli.run_research import main as run_research


def prepare(output):
    root=Path(output).resolve()
    root.mkdir(parents=True,exist_ok=False)  # Refuse even an existing empty directory.
    (root/'config.toml').write_bytes(Path(__file__).with_name('demo.toml').read_bytes())
    (root/'SYNTHETIC.txt').write_text('合成展示資料；非 TWSE 實際行情，不可用於投資或策略績效證明。\n',encoding='utf-8')
    settings=load_config(root/'config.toml')
    days=[];day=date(2026,7,1)
    while day<=date(2026,9,29):
        if day.weekday()<5:days.append(day.isoformat())
        day+=timedelta(days=1)
    stamp=timestamp();store=Store(root/'data/supplemental.sqlite3')
    for offset,sid in enumerate(settings['universe']['stocks']):
        rows=[]
        for index,day in enumerate(days):
            close=Decimal(80+offset*40)+Decimal(index)/5+Decimal((index+offset)%7)/10
            rows.append({'stock_id':sid,'date':day,'open':close-Decimal('.1'),
                'high':close+1,'low':close-1,'close':close,'volume':100000+index*100,
                'turnover':10000000,'trade_count':100})
        save_records(rows,db_path=settings['storage']['database_path'])
        revenues=[];values={}
        for n in range(13):
            y,m=divmod(2025*12+8+n,12);month=f'{y:04d}-{m+1:02d}'
            value=str(100000+offset*20000+n*1000);values[month]=value
            d=date(y,m+1,1);prior=(d-timedelta(days=1)).isoformat()[:7];year=f'{y-1:04d}-{m+1:02d}'
            revenues.append({'stock_id':sid,'period':month,'revenue':value,
                'previous_month':values.get(prior),'previous_year':values.get(year),
                'mom':growth(value,values.get(prior)),'yoy':growth(value,values.get(year)),
                'official_mom_percent':None,'official_yoy_percent':None,'source_generated_date':None,
                'source':'synthetic://portfolio-example','basis':'合成示例；非官方營收',
                'unit':'TWD thousand','published_at':None,'fetched_at':stamp,
                'raw':{'synthetic':True,'value':value}})
        store.save('revenue_history',revenues)
        flows=[]
        for day in days[-17:]:
            groups={key:{'buy':'1000','sell':'1200','net':'-200'} for key in ('foreign','trust','foreign_dealer','dealer_own','dealer_hedge')}
            groups['dealer']={'buy':'2000','sell':'2400','net':'-400'}
            flows.append({'stock_id':sid,'period':day,'groups':groups,'total':'-800',
                'unit':'shares','source':'synthetic://portfolio-example','published_at':None,
                'fetched_at':stamp,'raw':{'synthetic':True,'day':day},'notes':['合成示例，不是官方交易日曆']})
        store.save('institution',flows)
    library=StrategyLibrary(root/'strategies')
    strategy=from_settings(settings,name='離線合成示例（非真實行情）');library.save(strategy)
    state=root/'workbench'
    notes=Watchlist(state/'private/watchlist.sqlite3')
    for sid in settings['universe']['stocks']:notes.save(sid,'',0)
    code=run_research(['--config',str(root/'config.toml'),'--skip-download','--as-of','2026-09-29',
        '--strategy',str(library.path('baseline')),'--output-dir',str(root/'reports')])
    if code!=0:raise RuntimeError('合成示例研究未完整成功，請查看輸出；原資料未受影響')
    wb=Workbench(root/'config.toml',root/'reports',state,strategy_root=root/'strategies')
    try:
        saved=save_snapshot(observations(wb,settings['universe']['stocks'],'baseline'),state/'watchlist-reports')
    finally:wb.close()
    print('已建立合成展示；不代表真實市場結果。')
    print('目錄：',output)
    print('初次摘要：',saved.relative_to(root))
    return root


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='reports/portfolio-demo',help='必須是不存在的新目錄')
    args=parser.parse_args()
    try:prepare(args.output)
    except FileExistsError:parser.exit(1,'展示目錄已存在；不覆寫，請沿用或指定新的 --output。\n')
