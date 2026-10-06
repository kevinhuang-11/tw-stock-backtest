"""Validated JSON selection strategies; no execution settings or executable expressions."""
import hashlib
import json
import os
import tempfile
import re
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from datetime import date

from tw_stock_backtest.config import apply_overrides
from tw_stock_backtest.reporting import _json_default
from tw_stock_backtest.analysis.factors import calculate_factor_row, rank_factor_candidates
from tw_stock_backtest.analysis.screening import evaluate_stock
from tw_stock_backtest.analysis.indicators import (
    relative_strength_index, moving_average_convergence_divergence,
    previous_high_breakout, simple_moving_average,
)
from tw_stock_backtest.cli.download_universe import save_progress

CONDITIONS = {'rsi': {'period', 'minimum', 'maximum'}, 'macd': {'fast', 'slow', 'signal'},
              'breakout': {'period'}, 'trend': {'short_window', 'long_window'},
              'volume': {'period', 'minimum'}}
FACTOR_FIELDS = {'short_window', 'long_window', 'momentum_window', 'volatility_window',
                 'momentum_weight', 'trend_weight', 'volatility_weight', 'top_n'}
RULE_FIELDS = {'short_window', 'long_window', 'momentum_window', 'volume_window', 'min_volume_ratio', 'top_n'}


def canonical(value):
    return json.dumps(value, default=_json_default, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def positive(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1000:
        raise ValueError('期間與候選數需為 1～1000 整數')
    return value


def numeric(value):
    try:
        result = Decimal(str(value))
    except Exception as error:
        raise ValueError('數值格式無效') from error
    if not result.is_finite() or result < 0:
        raise ValueError('數值需為有限非負數')
    return result


def validate_strategy(value):
    s = deepcopy(value)
    required = {'schema_version', 'id', 'name', 'description', 'ranking_mode', 'factors', 'screening', 'conditions'}
    if not isinstance(s, dict) or set(s) != required or type(s['schema_version']) is not int or s['schema_version'] != 1:
        raise ValueError('策略格式或 schema_version 無效；不可包含股票池、資金或其他欄位')
    if not isinstance(s['id'], str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', s['id']):
        raise ValueError('策略 ID 需為小寫英文開頭的英數、底線或連字號')
    for field, limit in [('name', 100), ('description', 1000)]:
        if not isinstance(s[field], str) or not s[field].strip() or len(s[field]) > limit:
            raise ValueError('策略名稱或說明無效')
    if s['ranking_mode'] not in ('factors', 'rules'):
        raise ValueError('不支援的排名模式')
    for group, fields in [('factors', FACTOR_FIELDS), ('screening', RULE_FIELDS)]:
        if not isinstance(s[group], dict) or set(s[group]) != fields:
            raise ValueError('策略期間與權重欄位不完整')
        for key, value in s[group].items():
            s[group][key] = str(numeric(value)) if key.endswith('_weight') or key == 'min_volume_ratio' else positive(value)
        if s[group]['short_window'] >= s[group]['long_window']:
            raise ValueError('短期均線必須小於長期均線')
    if sum(Decimal(s['factors'][k]) for k in FACTOR_FIELDS if k.endswith('_weight')) == 0:
        raise ValueError('權重不可全為零')
    if not isinstance(s['conditions'], list) or len(s['conditions']) > len(CONDITIONS):
        raise ValueError('條件需為有限列表')
    seen = set()
    for c in s['conditions']:
        if not isinstance(c, dict) or not isinstance(c.get('type'), str) or c['type'] not in CONDITIONS:
            raise ValueError('未知條件')
        kind = c['type']
        if kind in seen or set(c) != CONDITIONS[kind] | {'type'}:
            raise ValueError('條件不可重複或含額外欄位')
        seen.add(kind)
        for key in CONDITIONS[kind]:
            c[key] = str(numeric(c[key])) if key in ('minimum', 'maximum') else positive(c[key])
        if kind == 'rsi' and not Decimal(c['minimum']) <= Decimal(c['maximum']) <= 100:
            raise ValueError('RSI 區間須在 0～100')
        if kind == 'macd' and c['fast'] >= c['slow']:
            raise ValueError('MACD fast 必須小於 slow')
        if kind == 'trend' and c['short_window'] >= c['long_window']:
            raise ValueError('趨勢短期必須小於長期')
    s['conditions'].sort(key=lambda c: c['type'])
    return s


def snapshot(strategy):
    s = validate_strategy(strategy)
    return {'definition': s, 'sha256': hashlib.sha256(canonical(s).encode()).hexdigest()}


def load_strategy(path):
    return validate_strategy(json.loads(Path(path).read_text(encoding='utf-8')))


def from_settings(settings, mode='factors', strategy_id='baseline', name='基準示例'):
    return validate_strategy({'schema_version': 1, 'id': strategy_id, 'name': name,
        'description': '僅展示功能，未證明策略有效。', 'ranking_mode': mode,
        'factors': settings['factors'], 'screening': settings['screening'], 'conditions': []})


def effective_settings(settings, strategy, top=None):
    overrides = {k: deepcopy(strategy[k]) for k in ('factors', 'screening')}
    for group in overrides.values():
        for key in group:
            if key.endswith('_weight') or key == 'min_volume_ratio':
                group[key] = Decimal(group[key])
        if top is not None:
            group['top_n'] = top
    return apply_overrides(settings, overrides)


class StrategyLibrary:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def path(self, strategy_id):
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', strategy_id):
            raise ValueError('策略 ID 無效')
        path = self.root / (strategy_id + '.json')
        if path.is_symlink():
            raise ValueError('策略不可為符號連結')
        return path

    def list(self):
        return [load_strategy(p) for p in sorted(self.root.glob('*.json')) if not p.is_symlink()]

    def load(self, strategy_id):
        return load_strategy(self.path(strategy_id))

    def save(self, value, *, replace=False):
        s = validate_strategy(value)
        path = self.path(s['id'])
        self.root.mkdir(parents=True, exist_ok=True)
        if path.exists() and not replace:
            raise ValueError('策略已存在；修改時需明確 replace')
        if replace:
            save_progress(s, path)
        else:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.root, suffix='.tmp', delete=False) as file:
                temporary = Path(file.name)
                file.write(json.dumps(s, ensure_ascii=False, indent=2) + '\n')
            try:
                os.link(temporary, path)
            finally:
                temporary.unlink()
        return s

    def copy(self, source, new_id, name):
        s = self.load(source)
        s.update(id=new_id, name=name)
        return self.save(s)


def evaluate_conditions(records, as_of, conditions):
    history = [r for r in records if r['date'] <= as_of]
    results = []
    for c in conditions:
        result = {'type': c['type'], 'parameters': deepcopy(c)}
        try:
            if not history or history[-1]['date'] != as_of:
                raise ValueError('缺少訊號日行情')
            closes = [r['close'] for r in history]
            kind = c['type']
            if kind in ('rsi', 'macd'):
                result['input_range'] = {'start': history[0]['date'], 'end': history[-1]['date'],
                                         'rows': len(history), 'policy': 'entire_prefix_no_reset'}
                invalid = [{'date': row['date'], 'position': i + 1, 'field': 'close'}
                           for i, row in enumerate(history)
                           if not isinstance(row.get('close'), Decimal)
                           or not row['close'].is_finite() or row['close'] <= 0]
                if invalid:
                    result['invalid_inputs'] = invalid
                    raise ValueError(f"遞迴指標歷史收盤價缺失或無效：{invalid[0]['date']}，第 {invalid[0]['position']} 筆；不補值或重置")
            if kind == 'rsi':
                value = relative_strength_index(closes, c['period'])[-1]
                if value is None:
                    raise ValueError('RSI 暖機不足')
                result.update(value=value, minimum=c['minimum'], maximum=c['maximum'],
                              passed=Decimal(c['minimum']) <= value <= Decimal(c['maximum']))
            elif kind == 'macd':
                values = moving_average_convergence_divergence(closes, c['fast'], c['slow'], c['signal'])[-1]
                if values['signal'] is None:
                    raise ValueError('MACD 訊號線暖機不足')
                result.update(values, passed=values['macd'] > values['signal'])
            elif kind == 'breakout':
                result.update(previous_high_breakout(history, c['period']))
            elif kind == 'trend':
                recent = closes[-c['long_window']:]
                short = simple_moving_average(recent, c['short_window'])[-1]
                long = simple_moving_average(recent, c['long_window'])[-1]
                if long is None:
                    raise ValueError('均線暖機不足')
                result.update(close=closes[-1], short_ma=short, long_ma=long,
                              passed=closes[-1] > short > long)
            else:
                n = c['period']
                if len(history) < n + 1:
                    raise ValueError('成交量暖機不足')
                volumes = [r['volume'] for r in history[-n-1:]]
                if any(type(v) is not int or v < 0 for v in volumes) or sum(volumes[:-1]) == 0:
                    raise ValueError('成交量缺漏或基準均量為零')
                ratio = Decimal(volumes[-1]) / (Decimal(sum(volumes[:-1])) / n)
                result.update(value=ratio, threshold=c['minimum'], passed=ratio > Decimal(c['minimum']))
            result['status'] = 'passed' if result['passed'] else 'filtered'
            result['reason'] = None if result['passed'] else '未符合條件'
        except (ValueError, KeyError, TypeError, IndexError) as error:
            result.update(status='unavailable', passed=None, reason=str(error))
        results.append(result)
    return results


def analyze_strategy(records_by_stock, as_of, strategy, *, indicator_records=None):
    """Score the valid base population first, then AND-filter without renormalizing."""
    s = validate_strategy(strategy)
    factor_settings = {k: Decimal(v) if k.endswith('_weight') else v for k,v in s['factors'].items()}
    rule_settings = {k: Decimal(v) if k == 'min_volume_ratio' else v for k,v in s['screening'].items() if k != 'top_n'}
    rows, diagnostics, errors = [], {}, {}
    for sid, records in records_by_stock.items():
        try:
            # Validate identity/order even though indicators only see the eligible prefix.
            dates = [date.fromisoformat(r['date']) for r in records]
            if any(r['stock_id'] != sid for r in records) or any(a >= b for a,b in zip(dates,dates[1:])):
                raise ValueError('行情股票或日期順序無效')
            if s['ranking_mode'] == 'factors':
                row = calculate_factor_row(records, as_of, factor_settings=factor_settings)
            else:
                row = evaluate_stock(records, as_of, **rule_settings)
            rows.append(row)
            details = evaluate_conditions((indicator_records or records_by_stock)[sid], as_of, s['conditions'])
            if s['ranking_mode'] == 'rules':
                details.insert(0, {'type': 'legacy_rules', 'status': 'passed' if row['selected'] else 'filtered',
                                  'passed': row['selected'], 'value': row, 'parameters': rule_settings,
                                  'reason': None if row['selected'] else '未符合原有均線／量比規則'})
            state = 'unavailable' if any(c['status']=='unavailable' for c in details) else 'filtered' if any(c['status']=='filtered' for c in details) else 'eligible'
            diagnostics[sid] = {'status': state, 'conditions': details}
            if state == 'unavailable':
                errors[sid] = '條件資料不足：' + '；'.join(c['reason'] for c in details if c['status']=='unavailable')
        except ValueError as error:
            errors[sid] = str(error)
            diagnostics[sid] = {'status': 'unavailable', 'reason': str(error),
                                'conditions': evaluate_conditions((indicator_records or records_by_stock)[sid], as_of, s['conditions'])}
    if s['ranking_mode'] == 'factors':
        ranked = rank_factor_candidates(rows, weights={k: factor_settings[k+'_weight'] for k in ('momentum','trend','volatility')})
    else:
        ranked = [{**r, 'rank': i+1, 'score': r['momentum'], 'factors': {}, 'factor_scores': {}}
                  for i,r in enumerate(sorted((r for r in rows if r['selected']), key=lambda r:(-r['momentum'],r['stock_id'])))]
    candidates = [r for r in ranked if diagnostics[r['stock_id']]['status']=='eligible']
    return {'rankings': ranked, 'candidates': candidates, 'diagnostics': diagnostics, 'errors': errors,
            'ranking_population': [r['stock_id'] for r in ranked]}
