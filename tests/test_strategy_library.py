import copy
import json
import re
import subprocess
import sys
import unittest
from datetime import date,timedelta
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory

from test_end_to_end import TEST_CONFIG
from tw_stock_backtest.config import load_config
from tw_stock_backtest.analysis.indicators import (
    exponential_moving_average as ema, relative_strength_index as rsi,
    moving_average_convergence_divergence as macd, previous_high_breakout,
)
from tw_stock_backtest.strategies import (from_settings,validate_strategy,snapshot,StrategyLibrary,analyze_strategy)
from tw_stock_backtest.data.database import save_records
from tw_stock_backtest.backtesting.portfolio import run_portfolio_backtest
from tw_stock_backtest.backtesting.costs import CostSettings
from tw_stock_backtest.strategy_comparison import compare_strategies
from tw_stock_backtest.workbench import Workbench,create_app


def records(sid='2330', count=40):
    return [{'stock_id':sid,'date':str(date(2026,8,1)+timedelta(days=i)),
             'open':D(100+i),'high':D(101+i),'low':D(99+i),'close':D(100+i),
             'volume':1000+i*10,'turnover':100000,'trade_count':10} for i in range(count)]


class IndicatorTests(unittest.TestCase):
    def test_known_ema_and_macd(self):
        # SMA3 seed=2, alpha=.5 => 3,4; fast2 at index2=2.5, slow3=2.
        values=list(map(D,[1,2,3,4,5]))
        self.assertEqual(ema(values,3),[None,None,D(2),D(3),D(4)])
        result=macd(values,2,3,2)
        self.assertEqual(result[2],{'macd':D('.5'),'signal':None,'histogram':None})
        self.assertEqual(result[3],{'macd':D('.5'),'signal':D('.5'),'histogram':D(0)})
        self.assertEqual(ema(values[:2],3),[None,None])
        nonlinear=macd(list(map(D,[1,2,4,8,16])),2,3,2)
        self.assertAlmostEqual(float(nonlinear[3]['signal']),37/36)
        self.assertAlmostEqual(float(nonlinear[3]['histogram']),7/36)

    def test_wilder_rsi_known_values_and_edges(self):
        # N=2: changes +2,-1 => gains1 losses.5 =>66 2/3; then -1 =>40.
        result=rsi(list(map(D,[10,12,11,10])),2)
        self.assertEqual(result[:2],[None,None])
        self.assertAlmostEqual(float(result[2]),200/3)
        self.assertEqual(result[3],D(40))
        self.assertEqual(rsi([D(1)]*4,2)[-1],D(50))
        self.assertEqual(rsi(list(map(D,[1,2,3])),2)[-1],D(100))
        self.assertEqual(rsi(list(map(D,[3,2,1])),2)[-1],D(0))
        for bad in (None,D('NaN'),D('Infinity')):
            for fn in (ema,rsi):
                with self.assertRaises(ValueError): fn([D(1),bad],2)
        with self.assertRaises(ValueError): macd([D(1)],3,2,1)

    def test_breakout_excludes_current_high(self):
        rows=records(count=3);rows[-1]['close']=D(500);rows[-1]['high']=D(9999)
        result=previous_high_breakout(rows,2)
        self.assertEqual(result['threshold'],D(102));self.assertTrue(result['passed'])
        rows[0]['high']=None
        with self.assertRaises(ValueError):previous_high_breakout(rows,2)
        with self.assertRaises(ValueError):previous_high_breakout(rows[1:],2)


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.config=self.root/'config.toml'
        self.config.write_text(TEST_CONFIG.replace('AAA','2330').replace('quantity = 10','quantity = 1'))
        self.settings=load_config(self.config);self.strategy=from_settings(self.settings)
        save_records(records(),db_path=self.settings['storage']['database_path'])

    def test_validation_snapshot_and_copy(self):
        library=StrategyLibrary(self.root/'strategies');library.save(self.strategy)
        before=snapshot(library.load('baseline'))
        new=library.copy('baseline','copy','副本');new['factors']['momentum_weight']='2'
        library.save(new,replace=True)
        self.assertEqual(snapshot(library.load('baseline')),before)
        self.assertNotEqual(snapshot(library.load('copy'))['sha256'],before['sha256'])
        for change in ({'schema_version':2},{'conditions':[{'type':[]}]},{'cash':100},{'id':'../bad'}, {'conditions':[{'type':'eval','code':'print(1)'}]}):
            with self.assertRaises(ValueError):validate_strategy({**self.strategy,**change})
        bad=copy.deepcopy(self.strategy)
        for k in ('momentum_weight','trend_weight','volatility_weight'):bad['factors'][k]='0'
        with self.assertRaises(ValueError):validate_strategy(bad)
        with self.assertRaises(ValueError):library.save(self.strategy)

    def test_filter_keeps_base_scores_future_ignored_and_unavailable(self):
        rows={'2330':records(),'2317':records('2317')};day=rows['2330'][-1]['date']
        baseline=analyze_strategy(rows,day,self.strategy)
        filtered=copy.deepcopy(self.strategy);filtered['conditions']=[{'type':'rsi','period':14,'minimum':'30','maximum':'70'}]
        result=analyze_strategy(rows,day,filtered)
        self.assertEqual(result['rankings'],baseline['rankings']);self.assertEqual(result['candidates'],[])
        self.assertEqual(result['diagnostics']['2330']['status'],'filtered')
        future={sid:rs+[dict(rs[-1],date='2026-12-31',close=D(999))] for sid,rs in rows.items()}
        self.assertEqual(analyze_strategy(future,day,filtered),result)
        filtered['conditions'][0]['period']=100
        self.assertEqual(analyze_strategy(rows,day,filtered)['diagnostics']['2330']['status'],'unavailable')

    def test_engine_baseline_unchanged_and_comparison_uses_same_results(self):
        s=self.settings;b=s['backtest'];rows={'2330':records()}
        kwargs=dict(screening_settings=s['screening'],factor_settings=s['factors'],ranking_method='factors',
                    initial_cash=b['initial_cash'],quantity=b['quantity'],max_positions=b['max_positions'],
                    cost_settings=CostSettings(**s['costs']),sizing_mode=b['sizing_mode'],slippage_rate=b['slippage_rate'])
        legacy=run_portfolio_backtest(rows,'2026-08-25','2026-09-09',**kwargs)
        current=run_portfolio_backtest(rows,'2026-08-25','2026-09-09',strategy=self.strategy,**kwargs)
        self.assertGreater(len(legacy['trades']),0)
        self.assertGreater(legacy['total_commission'],0)
        for key in legacy:self.assertEqual(legacy[key],current[key],key)
        other=copy.deepcopy(self.strategy);other['id']='rsi';other['conditions']=[{'type':'rsi','period':14,'minimum':'30','maximum':'70'}]
        output=compare_strategies(s,[self.strategy,other],'2026-08-25','2026-09-09',self.root/'reports')
        report=json.loads((output/'comparison.json').read_text())
        child=json.loads((output/report['strategies'][0]['report']/'results.json').read_text())
        self.assertEqual(child['strategy']['result']['final_equity'],str(legacy['final_equity']))
        self.assertEqual(report['strategies'][1]['trades'],0)
        self.assertTrue((output/'comparison.png').is_file())
        with self.assertRaises(ValueError):compare_strategies(s,[self.strategy,self.strategy],'2026-08-25','2026-09-09',self.root/'bad')

    def test_confirmed_halt_is_not_removed_for_recursive_indicators(self):
        rows=records();halt=rows[5]
        for field in ('open','high','low','close'):halt[field]=None
        for field in ('volume','turnover','trade_count'):halt[field]=0
        strategy=copy.deepcopy(self.strategy)
        strategy['conditions']=[{'type':'rsi','period':14,'minimum':'0','maximum':'100'}]
        s=self.settings;b=s['backtest']
        result=run_portfolio_backtest({'2330':rows},'2026-08-25','2026-09-09',
            screening_settings=s['screening'],factor_settings=s['factors'],strategy=strategy,
            initial_cash=b['initial_cash'],quantity=b['quantity'],max_positions=b['max_positions'],
            cost_settings=CostSettings(**s['costs']),confirmed_halts={('2330',halt['date']):{'reason':'test'}})
        self.assertTrue(all('2330' in d['errors'] for d in result['strategy_diagnostics']))
        self.assertEqual(result['trades'],[])

    def test_rules_baseline_and_cli_override_precedence(self):
        s=self.settings;b=s['backtest'];strategy=from_settings(s,'rules')
        kwargs=dict(screening_settings=s['screening'],initial_cash=b['initial_cash'],quantity=b['quantity'],
                    max_positions=b['max_positions'],cost_settings=CostSettings(**s['costs']))
        old=run_portfolio_backtest({'2330':records()},'2026-08-25','2026-09-09',**kwargs)
        new=run_portfolio_backtest({'2330':records()},'2026-08-25','2026-09-09',strategy=strategy,**kwargs)
        for key in old:self.assertEqual(old[key],new[key],key)
        library=StrategyLibrary(self.root/'strategies');library.save(self.strategy)
        completed=subprocess.run([sys.executable,'-m','tw_stock_backtest.cli.run_research',
            '--config',str(self.config),'--strategy',str(library.path('baseline')),
            '--skip-download','--as-of','2026-09-09','--top','1','--output-dir',str(self.root/'cli')],
            capture_output=True,text=True)
        self.assertEqual(completed.returncode,0,completed.stdout)
        summary=json.loads(next((self.root/'cli').glob('*/summary.json')).read_text())
        self.assertEqual(summary['strategy_snapshot']['definition']['factors']['top_n'],1)
        self.assertEqual(library.load('baseline')['factors']['top_n'],3)
        rejected=subprocess.run([sys.executable,'-m','tw_stock_backtest.cli.run_portfolio',
            '--config',str(self.config),'--strategy',str(library.path('baseline')),
            '--start','2026-08-25','--end','2026-09-09','--ranking','rules'],capture_output=True,text=True)
        self.assertEqual(rejected.returncode,1)

    def test_web_and_cli_strategy_same_snapshot(self):
        wb=Workbench(self.config,self.root/'reports',self.root/'state')
        try:
            app=create_app(wb);client=app.test_client()
            token=re.search(r'name="csrf" value="([^"]+)"',client.get('/strategies').text)[1]
            form={'csrf':token,'id':'baseline','name':'基準','description':'測試','ranking_mode':'factors','conditions':'[]'}
            for group in ('factors','screening'):
                form.update({group+'.'+k:str(v) for k,v in self.strategy[group].items()})
            self.assertEqual(client.post('/strategies',data=form).status_code,302)
            self.assertEqual(client.post('/strategies',data={**form,'csrf':''}).status_code,403)
            self.assertEqual(client.get('/strategies?id=../x').status_code,404)
            sid=wb.submit({'kind':'research','as_of':'2026-09-09','strategy_id':'baseline'})
            wb.pool.submit(lambda:None).result(timeout=30)
            saved=next((wb.state/sid/'reports').glob('*/summary.json'))
            summary=json.loads(saved.read_text())
            self.assertEqual(summary['strategy_snapshot'],snapshot(wb.library.load('baseline')))
            self.assertEqual(summary['status'],'success')
            ranking=json.loads((saved.parent/'rankings.json').read_text())
            self.assertEqual(ranking[0]['stock_id'],'2330')
            new=wb.library.load('baseline');new['factors']['momentum_weight']='2';wb.library.save(new,replace=True)
            self.assertNotEqual(json.loads(saved.read_text())['strategy_snapshot'],snapshot(wb.library.load('baseline')))
            comparison=json.loads(saved.read_text())
            self.assertEqual(comparison['counts']['ranked'],1)
        finally:wb.close()


if __name__=='__main__':unittest.main()
