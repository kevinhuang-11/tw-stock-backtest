import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from tw_stock_backtest.config import load_config
from tw_stock_backtest.data.database import save_records, load_records
from tw_stock_backtest.cli import run_research, fetch_stock
from tw_stock_backtest.reporting import ResearchReport
from tw_stock_backtest.research import (
    plan_update, update_stocks, evaluate_research, choose_analysis_date,
)

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ['日期', '成交股數', '成交金額', '開盤價', '最高價', '最低價', '收盤價', '成交筆數']


def payload(day='115/09/01', close='10'):
    return {'fields': FIELDS, 'data': [[day, '100', '1000', close, close, close, close, '10']]}


def records(sid, start=date(2026, 9, 1), days=4):
    return [dict(stock_id=sid, date=(start+timedelta(days=i)).isoformat(),
                 open=Decimal(10+i), high=Decimal(10+i), low=Decimal(10+i),
                 close=Decimal(10+i), volume=100, turnover=1000, trade_count=10)
            for i in range(days)]


class ResearchCase(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = self.root/'config.toml'
        self.config.write_text((ROOT/'config.toml').read_text())
        self.settings = load_config(self.config)
        self.db = self.settings['storage']['database_path']
        self.settings['download']['request_interval_seconds'] = 0
        self.settings['factors'].update(short_window=1, long_window=2,
                                        momentum_window=1, volatility_window=1)
        self.stocks = [{'stock_id': 'AAA', 'name': '中文公司'}, {'stock_id': 'BBB', 'name': ''}]

    def seed(self, sid='AAA', **kwargs):
        save_records(records(sid, **kwargs), self.db)

    def evaluate(self, **kwargs):
        return evaluate_research(self.stocks, self.settings, date(2026,9,4), **kwargs)


class TestIncremental(ResearchCase):
    def test_initial_and_cross_month_plan(self):
        lower, months = plan_update(None, date(2026,1,10), initial_days=40)
        self.assertEqual(lower, date(2025,12,2))
        self.assertEqual(months, [date(2025,12,1), date(2026,1,1)])
        self.assertEqual(plan_update('2026-08-31',date(2026,9,29))[1],
                         [date(2026,8,1), date(2026,9,1)])

    def test_explicit_backfill_overrides_newer_data(self):
        lower, months = plan_update('2026-09-29', date(2026,8,31), start=date(2026,8,15))
        self.assertEqual((lower,months),(date(2026,8,15),[date(2026,8,1)]))

    def test_same_month_is_refetched_and_upserted(self):
        with patch('tw_stock_backtest.research.fetch_month_with_retry', return_value=payload()) as fetch:
            for _ in range(2):
                result=update_stocks(self.stocks[:1],self.settings,date(2026,9,4),start=date(2026,9,1))
        self.assertEqual(fetch.call_count,2)
        self.assertEqual(result[0]['status'],'success')
        self.assertEqual(len(load_records('AAA','2026-09-01','2026-09-04',self.db)),1)
        with patch('tw_stock_backtest.research.fetch_month_with_retry',return_value=payload(close='20')):
            update_stocks(self.stocks[:1],self.settings,date(2026,9,4))
        self.assertEqual(load_records('AAA','2026-09-01','2026-09-04',self.db)[0]['close'],Decimal(20))

    def test_failure_continues_next_stock_and_does_not_advance_month(self):
        with patch('tw_stock_backtest.research.fetch_month_with_retry',side_effect=[
            TimeoutError('secret must not be saved'),payload('115/08/03'),payload(),
        ]) as fetch:
            result=update_stocks(self.stocks,self.settings,date(2026,9,4),start=date(2026,8,1))
        self.assertEqual([r['status'] for r in result],['failed','success'])
        self.assertEqual(fetch.call_count,3)
        self.assertNotIn('secret',json.dumps(result))
        self.assertEqual(len(result[0]['months']),1)

    def test_no_data_and_empty_filtered_range_are_distinct_from_failure(self):
        with patch('tw_stock_backtest.research.fetch_month_with_retry', side_effect=[
            fetch_stock.NoMarketData(), payload(),
        ]):
            result=update_stocks(self.stocks,self.settings,date(2026,9,4),start=date(2026,9,2))
        self.assertEqual([r['status'] for r in result],['no_data','no_data'])
        self.assertFalse(self.db.exists())

    def test_latest_before_cutoff_and_exact_requested_range(self):
        self.seed(start=date(2026,10,1),days=1)
        with patch('tw_stock_backtest.research.fetch_month_with_retry',return_value=payload()):
            result=update_stocks(self.stocks[:1],self.settings,date(2026,9,4),start=date(2026,9,2))
        self.assertEqual(result[0]['start'],'2026-09-02')
        self.assertEqual(result[0]['status'],'no_data')

    def test_rate_limit_applies_between_stocks(self):
        self.settings['download']['request_interval_seconds']=3
        with patch('tw_stock_backtest.research.fetch_month_with_retry',return_value=payload()), patch('tw_stock_backtest.research.time.sleep') as sleep:
            update_stocks(self.stocks,self.settings,date(2026,9,4),start=date(2026,9,1))
        sleep.assert_called_once_with(3)

    def test_source_no_data_uses_typed_compatible_error(self):
        response=io.BytesIO(json.dumps({'stat':'很抱歉，沒有符合條件的資料!'}).encode())
        with patch.object(fetch_stock,'urlopen',return_value=response):
            with self.assertRaises(fetch_stock.NoMarketData):
                fetch_stock.fetch_month('AAA','20260901',timeout_seconds=1)


class TestResearchRanking(ResearchCase):
    def test_strict_partial_and_repeatable_rankings(self):
        self.seed()
        strict=self.evaluate(as_of='2026-09-04')
        partial=self.evaluate(as_of='2026-09-04',allow_partial=True)
        self.assertEqual((strict['exit_code'],strict['rankings']),(1,[]))
        self.assertEqual(partial['exit_code'],2)
        self.assertEqual(partial['ranking_population'],['AAA'])
        self.assertEqual(partial['rankings'],self.evaluate(as_of='2026-09-04',allow_partial=True)['rankings'])

    def test_missing_close_and_stale_are_reported(self):
        self.seed(); self.seed('BBB',days=3)
        r=records('AAA')[-1]; r['close']=None; save_records([r],self.db)
        result=self.evaluate(as_of='2026-09-04',allow_partial=True)
        self.assertEqual(result['exit_code'],1)
        reasons=' '.join(e['reason'] for e in result['excluded'])
        self.assertIn('收盤價',reasons); self.assertIn('未更新',reasons)

    def test_automatic_latest_date_requires_each_stock_freshness(self):
        self.seed(); self.seed('BBB',days=3)
        self.assertEqual(self.evaluate()['exit_code'],1)
        result=self.evaluate(allow_partial=True)
        self.assertEqual(result['analysis_date'],'2026-09-04')
        self.assertEqual(result['exit_code'],2)
        self.assertEqual({r['date'] for r in result['rankings']},{'2026-09-04'})
        self.assertEqual(result['lag_calendar_days'],0)
        self.assertEqual(result['ranking_population'],['AAA'])

    def test_no_common_date_and_future_ignored(self):
        histories={'AAA':records('AAA',days=1), 'BBB':records('BBB',start=date(2026,9,2),days=1)}
        self.assertEqual(choose_analysis_date(histories,date(2026,9,4)),'2026-09-02')
        self.assertIsNone(choose_analysis_date({'AAA':records('AAA')},date(2026,8,31)))

    def test_download_failure_excludes_even_valid_old_data(self):
        self.seed(); self.seed('BBB')
        result=self.evaluate(as_of='2026-09-04',allow_partial=True,
                             downloads=[{'stock_id':'AAA','status':'failed'}])
        self.assertEqual(result['ranking_population'],['BBB'])
        self.assertEqual(result['exit_code'],2)

    def test_all_good_is_success(self):
        self.seed(); self.seed('BBB')
        self.assertEqual(self.evaluate(as_of='2026-09-04')['exit_code'],0)


class TestResearchReports(ResearchCase):
    def test_serialization_and_unique_completed_directories(self):
        paths=[]
        for _ in range(2):
            report=ResearchReport(self.root/'output')
            report.write_json('settings.json',{'price':Decimal('1.23'),'date':date(2026,9,1),'name':'中文'})
            paths.append(report.finish({'rankings':[], 'exit_code':1}))
        self.assertNotEqual(*paths)
        doc=json.loads((paths[0]/'settings.json').read_text())
        self.assertEqual(doc,{'price':'1.23','date':'2026-09-01','name':'中文'})
        self.assertTrue(json.loads((paths[0]/'summary.json').read_text())['report_complete'])
        self.assertFalse(list((self.root/'output').glob('*.incomplete')))

    def test_write_failure_does_not_publish_complete_directory(self):
        report=ResearchReport(self.root/'output')
        with patch('tw_stock_backtest.cli.download_universe.os.replace',side_effect=OSError('disk full')):
            with self.assertRaises(OSError): report.finish({'rankings':[]})
        self.assertTrue(report.path.exists())
        self.assertFalse(report.final_path.exists())
        self.assertFalse((report.path/'summary.json').exists())

    def test_nonserializable_value_is_rejected(self):
        report=ResearchReport(self.root/'output')
        with self.assertRaises(TypeError): report.write_json('settings.json',{'x':object()})
        self.assertFalse((report.path/'settings.json').exists())


class TestResearchCLI(ResearchCase):
    def test_offline_subprocess_end_to_end(self):
        self.seed('2330',days=25)
        output=self.root/'output'
        completed=subprocess.run([
            sys.executable,'-m','tw_stock_backtest.cli.run_research',
            '--config',str(self.config),'--stocks','2330','--skip-download',
            '--as-of','2026-09-25','--output-dir',str(output),
        ],capture_output=True,text=True,timeout=30)
        self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)
        run=next(output.iterdir())
        summary=json.loads((run/'summary.json').read_text())
        self.assertEqual(summary['ranking_population'],['2330'])
        self.assertEqual(summary['downloads'],[])
        self.assertEqual(summary['analysis_date'],'2026-09-25')
        with (run/'rankings.csv').open(encoding='utf-8-sig') as f:
            self.assertEqual(list(csv.DictReader(f))[0]['stock_id'],'2330')

    def test_update_rank_export_without_network(self):
        rows=[]
        for day in range(1,26): rows += payload(f'115/09/{day:02}')['data']
        with patch('tw_stock_backtest.research.fetch_month_with_retry',return_value={'fields':FIELDS,'data':rows}), redirect_stdout(io.StringIO()):
            code=run_research.main(['--config',str(self.config),'--stocks','2330',
                '--as-of','2026-09-25','--start','2026-09-01','--output-dir',str(self.root/'out')])
        self.assertEqual(code,0)

    def test_missing_database_produces_failure_report_without_creating_db(self):
        with redirect_stdout(io.StringIO()):
            code=run_research.main(['--config',str(self.config),'--skip-download',
                '--as-of','2026-09-25','--output-dir',str(self.root/'out')])
        self.assertEqual(code,1)
        self.assertFalse(self.db.exists())
        self.assertEqual(len(list((self.root/'out').glob('*/summary.json'))),1)

    def test_invalid_flags_use_failure_exit_code(self):
        with self.assertRaises(SystemExit) as cm:
            run_research.parse_arguments(['--unknown'])
        self.assertEqual(cm.exception.code,1)


class TestResearchBoundaries(ResearchCase):
    def test_no_data_update_blocks_strict_ranking_even_with_cached_prices(self):
        self.seed(); self.seed('BBB')
        result=self.evaluate(as_of='2026-09-04', downloads=[{'stock_id':'AAA','status':'no_data'}])
        self.assertEqual(result['rankings'],[])
        self.assertEqual(result['exit_code'],1)

    def test_progress_keeps_completed_stock_on_interruption(self):
        snapshots=[]
        def progress(rows): snapshots.append(json.loads(json.dumps(rows)))
        with patch('tw_stock_backtest.research.fetch_month_with_retry',side_effect=[payload(), KeyboardInterrupt()]):
            with self.assertRaises(KeyboardInterrupt):
                update_stocks(self.stocks,self.settings,date(2026,9,4),start=date(2026,9,1),on_progress=progress)
        self.assertEqual(snapshots[-1][0]['status'],'success')
        self.assertEqual(snapshots[-1][1]['status'],'running')

    def test_taipei_timezone_near_utc_midnight(self):
        from tw_stock_backtest.research import taipei_today
        with patch('tw_stock_backtest.research.datetime') as clock:
            clock.now.return_value=datetime(2026,10,1,1,tzinfo=timezone(timedelta(hours=8)))
            self.assertEqual(taipei_today(),date(2026,10,1))
            self.assertEqual(str(clock.now.call_args.args[0]),'Asia/Taipei')

    def test_cli_future_date_fails_without_downloading(self):
        with patch('tw_stock_backtest.cli.run_research.taipei_today',return_value=date(2026,9,4)), patch('tw_stock_backtest.cli.run_research.update_stocks') as update, redirect_stdout(io.StringIO()):
            code=run_research.main(['--config',str(self.config),'--as-of','2026-09-05'])
        self.assertEqual(code,1)
        update.assert_not_called()

    def test_report_csv_preserves_decimal_and_rank_order(self):
        self.seed(); self.seed('BBB')
        result=self.evaluate(as_of='2026-09-04')
        original=list(result['rankings'])
        report=ResearchReport(self.root/'out')
        path=report.finish(result)
        self.assertEqual(result['rankings'],original)
        with (path/'rankings.csv').open(encoding='utf-8-sig') as f:
            rows=list(csv.DictReader(f))
        data=json.loads((path/'rankings.json').read_text())
        self.assertEqual([r['stock_id'] for r in rows],result['ranking_population'])
        self.assertEqual(Decimal(rows[0]['score']),Decimal(data[0]['score']))


class TestResearchResume(ResearchCase):
    def run_command(self, *extra):
        with redirect_stdout(io.StringIO()):
            return run_research.main(['--config',str(self.config),'--stocks','AAA','BBB',
                '--as-of','2026-09-25','--start','2026-09-01',
                '--output-dir',str(self.root/'out'),*extra])

    def full_payload(self):
        return {'fields':FIELDS,'data':[payload(f'115/09/{day:02}')['data'][0] for day in range(1,26)]}

    def test_resume_skips_success_retries_failure_and_keeps_original_plan(self):
        with patch('tw_stock_backtest.research.time.sleep'), patch('tw_stock_backtest.research.fetch_month_with_retry',side_effect=[self.full_payload(),TimeoutError()]):
            self.assertEqual(self.run_command(),1)
        previous=next((self.root/'out').glob('*/progress.json'))
        with patch('tw_stock_backtest.research.fetch_month_with_retry',return_value=self.full_payload()) as fetch:
            self.assertEqual(self.run_command('--resume',str(previous)),0)
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args,('BBB','20260901'))
        summaries=[json.loads(p.read_text()) for p in (self.root/'out').glob('*/summary.json')]
        succeeded=next(s for s in summaries if s['exit_code']==0)
        self.assertEqual(succeeded['counts']['downloads'],{'skipped':1,'success':1})
        self.assertEqual(len(succeeded['ranking_population']),2)

    def test_resume_rejects_changed_database_settings_or_pool(self):
        with patch('tw_stock_backtest.research.time.sleep'), patch('tw_stock_backtest.research.fetch_month_with_retry',return_value=self.full_payload()):
            self.assertEqual(self.run_command(),0)
        progress=next((self.root/'out').glob('*/progress.json'))
        for extra in [ ['--database',str(self.root/'different.db')], ['--top','1'], ['--allow-partial'], ['--stocks','AAA'], ['--start','2026-08-01'] ]:
            with self.subTest(extra=extra),patch('tw_stock_backtest.research.fetch_month_with_retry') as fetch:
                self.assertEqual(self.run_command('--resume',str(progress),*extra),1)
                fetch.assert_not_called()

    def test_resume_missing_database_does_not_claim_success(self):
        with patch('tw_stock_backtest.research.time.sleep'), patch('tw_stock_backtest.research.fetch_month_with_retry',return_value=self.full_payload()):
            self.run_command()
        progress=next((self.root/'out').glob('*/progress.json'))
        self.db.unlink()
        self.assertEqual(self.run_command('--resume',str(progress)),1)

    def test_resume_after_keyboard_interrupt(self):
        with patch('tw_stock_backtest.research.time.sleep'),patch('tw_stock_backtest.research.fetch_month_with_retry',side_effect=[self.full_payload(),KeyboardInterrupt()]):
            with self.assertRaises(KeyboardInterrupt): self.run_command()
        progress=next((self.root/'out').glob('*.incomplete/progress.json'))
        self.assertFalse(list((self.root/'out').glob('*/summary.json')))
        with patch('tw_stock_backtest.research.fetch_month_with_retry',return_value=self.full_payload()):
            self.assertEqual(self.run_command('--resume',str(progress)),0)

    def test_listing_after_cutoff_is_skipped_without_request(self):
        stocks=[{'stock_id':'AAA','listed_date':'2026-10-01'}]
        with patch('tw_stock_backtest.research.fetch_month_with_retry') as fetch:
            results=update_stocks(stocks,self.settings,date(2026,9,4))
        fetch.assert_not_called()
        self.assertEqual(results[0]['status'],'skipped')
        self.assertEqual(results[0]['skip_reason'],'listed_after_cutoff')


class TestResearchMetadata(ResearchCase):
    def test_universe_snapshot_and_full_rankings_ignore_display_limit(self):
        from tw_stock_backtest.data.universe import save_universe
        universe=self.root/'universe.json'
        save_universe([{'stock_id':sid,'name':'測試公司','market':'上市','industry':'測試',
                        'listed_date':'2000-01-01','cfi_code':'ESVUFR'} for sid in ['2330','2317']],universe)
        for sid in ['2330','2317']: self.seed(sid,days=25)
        with redirect_stdout(io.StringIO()) as output:
            code=run_research.main(['--config',str(self.config),'--universe',str(universe),
                '--skip-download','--as-of','2026-09-25','--top','1',
                '--output-dir',str(self.root/'out')])
        self.assertEqual(code,0)
        run=next((self.root/'out').iterdir())
        self.assertEqual(len(json.loads((run/'rankings.json').read_text())),2)
        self.assertEqual(json.loads((run/'universe.json').read_text())['downloaded_at'],
                         json.loads(universe.read_text())['downloaded_at'])
        self.assertNotIn('2. ',output.getvalue())

    def test_old_automatic_date_is_explicitly_recorded(self):
        self.seed(); self.seed('BBB')
        result=evaluate_research(self.stocks,self.settings,date(2026,9,7))
        self.assertEqual(result['analysis_date'],'2026-09-04')
        self.assertEqual(result['lag_calendar_days'],3)
        # 無交易日曆，success 只保證基準日完整，不把週末推定為缺漏。
        self.assertEqual(result['exit_code'],0)

    def test_no_git_does_not_prevent_version_capture(self):
        from tw_stock_backtest.research import program_version
        with patch('tw_stock_backtest.research.subprocess.check_output',side_effect=FileNotFoundError()):
            self.assertEqual(program_version(),{'commit':None,'dirty':None})

    def test_csv_write_failure_leaves_incomplete_report(self):
        report=ResearchReport(self.root/'out')
        with patch('tw_stock_backtest.reporting.csv.DictWriter',side_effect=OSError('disk full')):
            with self.assertRaises(OSError): report.finish({'rankings':[]})
        self.assertTrue(report.path.exists())
        self.assertFalse(report.final_path.exists())
        self.assertFalse((report.path/'summary.json').exists())
