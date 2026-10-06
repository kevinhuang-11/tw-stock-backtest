"""Compare a bounded set of strategies against one frozen input and benchmark."""
import csv
import hashlib
import json
import re
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path

from tw_stock_backtest.strategies import validate_strategy, effective_settings, snapshot, canonical
from tw_stock_backtest.data.database import load_records
from tw_stock_backtest.backtesting.portfolio import run_portfolio_backtest
from tw_stock_backtest.backtesting.benchmark import run_buy_and_hold
from tw_stock_backtest.backtesting.costs import CostSettings
from tw_stock_backtest.backtesting.metrics import summarize_performance, summarize_exposure
from tw_stock_backtest.reporting import ResearchReport, export_portfolio_report
from tw_stock_backtest.plotting import prepare_series
from tw_stock_backtest.research import program_version
from tw_stock_backtest.date_range import parse_date_range


def performance(result):
    return {**summarize_performance(result['equity_curve'],result['initial_cash']),
            **summarize_exposure(result['equity_curve'])}


def compare_strategies(settings, strategies, start, end, output_root, *, frozen_records=None, tolerate_failures=False):
    parse_date_range(start,end)
    strategies = [validate_strategy(s) for s in strategies]
    if not 2 <= len(strategies) <= 5 or len({s['id'] for s in strategies}) != len(strategies):
        raise ValueError('一次比較 2～5 份不同 ID 的策略')
    records = {sid:load_records(sid,'1900-01-01',end,db_path=settings['storage']['database_path'])
               for sid in settings['universe']['stocks']} if frozen_records is None else {
                   sid: [r for r in frozen_records[sid] if r['date'] <= end]
                   for sid in settings['universe']['stocks']}
    backtest = settings['backtest']; costs = CostSettings(**settings['costs'])
    benchmark = run_buy_and_hold(records,start,end,initial_cash=backtest['initial_cash'],
                                cost_settings=costs,slippage_rate=backtest['slippage_rate'])
    bench_performance = performance(benchmark)
    report = ResearchReport(output_root)
    common = {'stocks':settings['universe']['stocks'],'start':start,'end':end,
              'backtest':backtest,'costs':settings['costs'],
              'input_sha256':hashlib.sha256(canonical(records).encode()).hexdigest()}
    report.write_json('common.json',{'execution':common,'version':program_version()})
    rows, series, failures = [], [], []
    for strategy in strategies:
        try:
            effective = effective_settings(settings,strategy)
            result = run_portfolio_backtest(records,start,end,screening_settings=effective['screening'],
                factor_settings=effective['factors'], ranking_method=strategy['ranking_mode'],strategy=strategy,
                initial_cash=backtest['initial_cash'],quantity=backtest['quantity'],max_positions=backtest['max_positions'],
                cost_settings=costs,sizing_mode=backtest['sizing_mode'],slippage_rate=backtest['slippage_rate'])
            if [p['date'] for p in result['equity_curve']] != [p['date'] for p in benchmark['equity_curve']]:
                raise ValueError('資產日期不一致，拒絕比較')
            metrics = performance(result)
            child = export_portfolio_report(report.path / strategy['id'],settings=effective,start_text=start,end_text=end,
                records_by_stock=records,result=result,performance=metrics,benchmark=benchmark,
                benchmark_performance=bench_performance,strategy=strategy)
            rows.append({'id':strategy['id'],'name':strategy['name'],'strategy_snapshot':snapshot(strategy),
                         'report':str(child.relative_to(report.path)), 'total_return':metrics['total_return'],
                         'max_drawdown':metrics['max_drawdown'],
                         'fees_and_taxes':result['total_commission']+result['total_tax'],
                         'trades':len(result['trades']),'average_exposure':metrics['average_exposure'],
                         'no_candidate_days':sum(not d['candidates'] for d in result['strategy_diagnostics']),
                         'unavailable_days':sum(bool(d['errors']) for d in result['strategy_diagnostics'])})
            series.append((strategy['id'],prepare_series(result)))
        except Exception as error:
            if not tolerate_failures:
                raise
            failures.append({'id': strategy['id'], 'strategy_snapshot': snapshot(strategy),
                             'error_type': type(error).__name__, 'reason': '子策略回測失敗；保留其他策略結果'})
    series.append(('Buy and hold',prepare_series(benchmark)))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter
    fig,axes=plt.subplots(2,1,sharex=True,figsize=(11,8))
    try:
        for name,points in series:
            axes[0].plot(points['dates'],[float(v) for v in points['equities']],label=name)
            axes[1].plot(points['dates'],[float(v) for v in points['drawdowns']],label=name)
        axes[0].set_ylabel('Equity');axes[1].set_ylabel('Drawdown')
        axes[1].yaxis.set_major_formatter(PercentFormatter(1))
        axes[0].legend();fig.autofmt_xdate();fig.tight_layout()
        fig.savefig(report.path/'comparison.png')
    finally:
        plt.close(fig)
    fields=['id','total_return','max_drawdown','fees_and_taxes','trades','average_exposure','no_candidate_days','unavailable_days']
    with (report.path/'comparison.csv').open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=fields);writer.writeheader()
        writer.writerows({k:r[k] for k in fields} for r in rows)
    report.write_json('comparison.json',{'schema_version':1,'kind':'comparison','report_complete':True,
        'run_id':report.run_id,'common':common,'strategies':rows,'benchmark':bench_performance,
        'failures': failures,
        'note':'固定示例的歷史比較，不選出最佳策略；尚未完成樣本外有效性驗證。'})
    report.path.rename(report.final_path)
    return report.final_path


def validate_periods(periods):
    """Explicit bounds and research labels; labels never certify unseen data."""
    if not isinstance(periods, list) or not 1 <= len(periods) <= 12:
        raise ValueError('需指定 1～12 個期間')
    seen = set()
    for period in periods:
        if not isinstance(period, dict) or set(period) != {'id', 'start', 'end', 'purpose', 'previously_seen'}:
            raise ValueError('期間需包含 id、start、end、purpose、previously_seen')
        if not isinstance(period['id'], str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', period['id']) or period['id'] in seen:
            raise ValueError('期間 ID 需唯一且只含小寫英數、底線或連字號')
        seen.add(period['id'])
        parse_date_range(period['start'], period['end'])
        if period['purpose'] not in ('development', 'validation') or type(period['previously_seen']) is not bool:
            raise ValueError('用途需為 development／validation；previously_seen 需為布林值')
    return json.loads(json.dumps(periods))


def summarize_periods(rows, strategies):
    """Only fully evaluable periods enter ranges and outperform denominators."""
    result = []
    for strategy in strategies:
        completed = [r for r in rows if r['id'] == strategy['id']]
        valid = [r for r in completed if r['unavailable_days'] == 0]
        ranges = {}
        for key in ('total_return', 'benchmark_return', 'max_drawdown', 'benchmark_drawdown',
                    'return_difference_pp', 'fees_and_taxes', 'trades', 'average_exposure'):
            values = [Decimal(str(r[key])) for r in valid]
            ranges[key] = {'minimum': min(values), 'maximum': max(values)} if values else None
        result.append({'id': strategy['id'], 'completed_periods': len(completed),
                       'valid_periods': len(valid),
                       'outperform_periods': sum(Decimal(str(r['return_difference_pp'])) > 0 for r in valid),
                       'ranges': ranges})
    return result


def compare_periods(settings, strategies, periods, output_root):
    """Independent engine calls, one frozen database input, resumable diagnostics.

    Without a trading calendar, require an actual row at both requested boundaries
    for every stock. Never infer missing trading days or silently move a boundary.
    """
    periods = validate_periods(periods)
    strategies = [validate_strategy(s) for s in strategies]
    if not 2 <= len(strategies) <= 5 or len({s['id'] for s in strategies}) != len(strategies):
        raise ValueError('一次比較 2～5 份不同 ID 的策略')
    report = ResearchReport(output_root)
    started = datetime.now(timezone.utc).isoformat()
    max_end = max(p['end'] for p in periods)
    records = {sid: load_records(sid, '1900-01-01', max_end,
                                db_path=settings['storage']['database_path'])
               for sid in settings['universe']['stocks']}
    coverage = {sid: {'first': rs[0]['date'] if rs else None, 'last': rs[-1]['date'] if rs else None,
                      'rows': len(rs), 'missing_close': [r['date'] for r in rs if r['close'] is None]}
                for sid, rs in records.items()}
    common = {'stocks': settings['universe']['stocks'], 'backtest': settings['backtest'],
              'costs': settings['costs'], 'data_source': 'local SQLite / TWSE monthly prices',
              'coverage': coverage, 'input_sha256': hashlib.sha256(canonical(records).encode()).hexdigest(),
              'strategy_snapshots': [snapshot(s) for s in strategies], 'version': program_version()}
    overlap = [[a['id'], b['id']] for i, a in enumerate(periods) for b in periods[i+1:]
               if max(a['start'], b['start']) <= min(a['end'], b['end'])]
    report.write_json('common.json', common)
    rows, results = [], []
    for period in periods:
        item = dict(period, status='running')
        results.append(item)
        report.write_json('progress.json', {'periods': results, 'report_complete': False})
        try:
            missing = {sid: [day for day in (period['start'], period['end'])
                             if day not in {r['date'] for r in rs}] for sid, rs in records.items()}
            missing = {sid: days for sid, days in missing.items() if days}
            if missing:
                item.update(status='failed', reason='指定邊界缺少行情；未縮短期間', missing_boundaries=missing)
                continue
            child = compare_strategies(settings, strategies, period['start'], period['end'],
                                       report.path / period['id'], frozen_records=records, tolerate_failures=True)
            data = json.loads((child / 'comparison.json').read_text(encoding='utf-8'))
            item.update(status='success', report=str(child.relative_to(report.path)), failures=data.get('failures', []))
            for row in data['strategies']:
                entry = dict(row, period_id=period['id'], purpose=period['purpose'],
                             previously_seen=period['previously_seen'], start=period['start'], end=period['end'],
                             benchmark_return=data['benchmark']['total_return'],
                             benchmark_drawdown=data['benchmark']['max_drawdown'])
                entry['report'] = str(child.relative_to(report.path) / row['report'])
                entry['return_difference_pp'] = (Decimal(str(row['total_return'])) -
                                                 Decimal(str(entry['benchmark_return']))) * 100
                rows.append(entry)
            if data.get('failures') or any(r['unavailable_days'] for r in data['strategies']):
                item['status'] = 'partial' if data['strategies'] else 'failed'
        except Exception as error:
            # No arbitrary exception message or settings can leak into public outputs.
            item.update(status='failed', reason='子回測失敗，請檢查行情與設定', error_type=type(error).__name__)
        finally:
            report.write_json('progress.json', {'periods': results, 'report_complete': False})
    status = 'failed' if not rows else ('success' if all(p['status'] == 'success' for p in results) else 'partial')
    code = {'success': 0, 'partial': 2, 'failed': 1}[status]
    fields = ['period_id', 'id', 'purpose', 'previously_seen', 'start', 'end', 'total_return',
              'benchmark_return', 'max_drawdown', 'benchmark_drawdown', 'return_difference_pp',
              'fees_and_taxes', 'trades', 'average_exposure', 'no_candidate_days', 'unavailable_days', 'report']
    with (report.path / 'comparison.csv').open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction='ignore')
        writer.writeheader(); writer.writerows(rows)
    report.write_json('comparison.json', {
        'schema_version': 1, 'kind': 'period_comparison', 'run_id': report.run_id,
        'report_complete': True, 'status': status, 'exit_code': code, 'started_at': started,
        'finished_at': datetime.now(timezone.utc).isoformat(), 'common': common,
        'periods': results, 'strategies': rows, 'aggregate': summarize_periods(rows, strategies),
        'overlapping_periods': overlap,
        'note': '各期間獨立重置資金與持股；有無法評估日的策略不納入有效期間分母。報酬差單位為百分點。用途標記不保證樣本外；重疊期間不是獨立樣本，不加總報酬或推論獲利機率。'})
    report.path.rename(report.final_path)
    return report.final_path, code
