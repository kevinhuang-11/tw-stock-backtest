import copy
import json
import re
import unittest
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_end_to_end import TEST_CONFIG
from test_strategy_library import records
from tw_stock_backtest.config import load_config
from tw_stock_backtest.data.database import save_records
from tw_stock_backtest.strategies import from_settings, snapshot, evaluate_conditions, StrategyLibrary
from tw_stock_backtest.strategy_comparison import compare_periods, compare_strategies, summarize_periods, validate_periods
from tw_stock_backtest.workbench import Workbench, create_app


def period(pid='first', start='2026-08-23', end='2026-08-25'):
    return dict(id=pid, start=start, end=end, purpose='development', previously_seen=True)


class PeriodTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'config.toml'
        self.config.write_text(TEST_CONFIG.replace('AAA', '2330').replace('quantity = 10', 'quantity = 1'))
        self.settings = load_config(self.config)
        self.strategies = [from_settings(self.settings), from_settings(self.settings, strategy_id='other', name='另一份')]
        save_records(records(), db_path=self.settings['storage']['database_path'])

    def read(self, path):
        return json.loads(path.read_text())

    def test_batch_equals_single_resets_and_never_trades_warmup_or_future(self):
        ps = [period(), period('second', '2026-08-26', '2026-09-01')]
        batch, code = compare_periods(self.settings, self.strategies, ps, self.root / 'batch')
        self.assertEqual(code, 0)
        data = self.read(batch / 'comparison.json')
        for p, output in zip(ps, data['periods']):
            single = compare_strategies(self.settings, self.strategies, p['start'], p['end'], self.root / 'single')
            direct = self.read(single / 'comparison.json')
            grouped = self.read(batch / output['report'] / 'comparison.json')
            for a, b in zip(direct['strategies'], grouped['strategies']):
                self.assertEqual({k:v for k,v in a.items() if k != 'report'}, {k:v for k,v in b.items() if k != 'report'})
                result = self.read(batch / output['report'] / b['report'] / 'results.json')['strategy']['result']
                for trade in result['trades']:
                    self.assertGreaterEqual(trade['date'], p['start'])
                    self.assertLessEqual(trade['date'], p['end'])
        old = data['strategies'][0]
        future = records()[-1]; future.update(date='2026-12-31', close=D('999999'))
        save_records([future], db_path=self.settings['storage']['database_path'])
        after, _ = compare_periods(self.settings, self.strategies, [ps[0]], self.root / 'future')
        new = self.read(after / 'comparison.json')['strategies'][0]
        self.assertEqual(old['total_return'], new['total_return'])
        self.assertEqual(old['strategy_snapshot'], snapshot(self.strategies[0]))

    def test_failed_period_keeps_others_overlap_and_percentage_points(self):
        periods = [period(), period('overlap', '2026-08-22', '2026-08-26'), period('missing', '2026-09-01', '2026-10-01')]
        path, code = compare_periods(self.settings, self.strategies, periods, self.root / 'out')
        data = self.read(path / 'comparison.json')
        self.assertEqual(code, 2); self.assertTrue(data['report_complete'])
        self.assertEqual(data['overlapping_periods'], [['first', 'overlap']])
        self.assertEqual(data['periods'][-1]['missing_boundaries'], {'2330':['2026-10-01']})
        for row in data['strategies']:
            self.assertEqual(D(str(row['return_difference_pp'])), (D(str(row['total_return']))-D(str(row['benchmark_return'])))*100)
        self.assertEqual(data['aggregate'][0]['valid_periods'], 2)
        self.assertTrue((path / data['strategies'][0]['report'] / 'results.json').is_file())
        failed, code = compare_periods(self.settings, self.strategies, [period('empty','2027-01-01','2027-01-02')], self.root / 'out')
        self.assertEqual(code, 1); self.assertEqual(self.read(failed / 'comparison.json')['strategies'], [])

    def test_recursive_missing_position_and_local_window_difference(self):
        rows = records(); rows[0]['close'] = None
        conditions = [{'type':'rsi','period':2,'minimum':'0','maximum':'100'},
                      {'type':'macd','fast':2,'slow':3,'signal':2},
                      {'type':'trend','short_window':2,'long_window':3}]
        result = evaluate_conditions(rows, rows[-1]['date'], conditions)
        for r in result[:2]:
            self.assertEqual(r['status'], 'unavailable')
            self.assertEqual(r['invalid_inputs'], [{'date':'2026-08-01','position':1,'field':'close'}])
            self.assertEqual(r['input_range']['rows'],40)
        self.assertEqual(result[-1]['status'], 'passed')
        future = dict(rows[-1], date='2027-01-01', close=None)
        self.assertEqual(result, evaluate_conditions(rows+[future], rows[-1]['date'], conditions))

    def test_validation_and_data_incomplete_not_in_denominator(self):
        for bad in ([dict(period(), id='../x')], [period(),period()], [dict(period(), purpose='test')], [dict(period(), previously_seen='false')]):
            with self.assertRaises(ValueError): validate_periods(bad)
        row = dict(id='baseline', unavailable_days=1, return_difference_pp='10')
        self.assertEqual(summarize_periods([row], self.strategies)[0]['valid_periods'], 0)

    def test_write_failure_retains_incomplete(self):
        from tw_stock_backtest.reporting import ResearchReport
        original = ResearchReport.write_json
        def fail(report, name, data):
            if name == 'comparison.json' and data.get('kind') == 'period_comparison':
                raise OSError('simulated disk failure')
            return original(report, name, data)
        with patch.object(ResearchReport, 'write_json', fail):
            with self.assertRaises(OSError): compare_periods(self.settings, self.strategies, [period()], self.root / 'out')
        paths = list((self.root / 'out').iterdir())
        self.assertEqual(len(paths),1); self.assertTrue(paths[0].name.endswith('.incomplete'))
        self.assertFalse((paths[0]/'comparison.json').exists())

    def test_web_period_job_uses_snapshot_and_downloads_csv(self):
        library = StrategyLibrary(self.root / 'strategies')
        for s in self.strategies: library.save(s)
        wb = Workbench(self.config, self.root / 'reports', self.root / 'jobs')
        self.addCleanup(wb.close)
        app = create_app(wb); client = app.test_client()
        home = client.get('/').get_data(as_text=True)
        token = re.search(r'name="csrf" value="([^"]+)"', home).group(1)
        form = dict(kind='period_comparison', stocks='2330', strategies='baseline other', periods=json.dumps([period()]))
        self.assertEqual(client.post('/jobs', data=form).status_code, 403)
        response = client.post('/jobs', data=dict(form,csrf=token))
        self.assertEqual(response.status_code,302)
        wb.pool.shutdown(wait=True)
        job = wb.jobs()[0]; self.assertEqual(job['status'],'success')
        self.assertIn('--periods',job['arguments'])
        entries = [(k,v) for k,v in wb.catalog().items() if v['details']['kind']=='period_comparison']
        self.assertEqual(len(entries),1)
        key, _ = entries[0]
        self.assertIn('百分點', client.get('/reports/'+key).get_data(as_text=True))
        self.assertEqual(client.get('/reports/'+key+'/csv').status_code,200)

    def test_one_strategy_exception_preserves_other_strategy(self):
        from tw_stock_backtest.strategy_comparison import run_portfolio_backtest
        def execute(*args, **kwargs):
            if kwargs['strategy']['id'] == 'other':
                raise ValueError('synthetic private exception content')
            return run_portfolio_backtest(*args, **kwargs)
        with patch('tw_stock_backtest.strategy_comparison.run_portfolio_backtest', side_effect=execute):
            path, code = compare_periods(self.settings, self.strategies, [period()], self.root / 'out')
        data = self.read(path / 'comparison.json')
        self.assertEqual(code, 2)
        self.assertEqual([r['id'] for r in data['strategies']], ['baseline'])
        self.assertEqual(data['periods'][0]['failures'][0]['id'], 'other')
        self.assertNotIn('synthetic private', (path / 'comparison.json').read_text())
