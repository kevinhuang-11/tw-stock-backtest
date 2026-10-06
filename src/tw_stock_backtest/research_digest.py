"""Saved research → deterministic presentation. No prices fetched or ranks recomputed."""
import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path

from tw_stock_backtest.research import taipei_today
from tw_stock_backtest.security import public_data

FACTOR_NAMES = {'momentum': '動能', 'trend': '趨勢', 'volatility': '低波動'}
POLICY = {'default_top': 10, 'max_top': 50, 'high_score': '75',
          'low_score': '25', 'close_contribution_points': '1',
          'industry_concentration': '0.5'}
FACTOR_KEYS = ('short_window', 'long_window', 'momentum_window', 'volatility_window',
               'momentum_weight', 'trend_weight', 'volatility_weight')
MISSING = '未提供'


def read_json(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('不讀取符號連結報表')
    return public_data(json.loads(path.read_text(encoding='utf-8')))


def number(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def shown(value):
    return MISSING if value is None or value == '' else str(value)


def score_text(value):
    parsed = number(value)
    return MISSING if parsed is None else f'{parsed:.2f}'


def percent(value):
    parsed = number(value)
    return MISSING if parsed is None else f'{parsed * 100:.4f}%'


def load_bundle(path):
    """Accept existing job.json or summary.json, with no guessing among multiple runs."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError('不讀取符號連結報表')
    path = path.resolve()
    original = read_json(path)
    if path.name == 'job.json' and original.get('kind') == 'research':
        choices = sorted(path.parent.glob('reports/*/summary.json'))
        choices = [p for p in choices if not p.parent.name.endswith('.incomplete')]
        if len(choices) != 1:
            return None
        path = choices[0]
        original = read_json(path)
    if not original.get('report_complete') or path.parent.name.endswith('.incomplete'):
        return None
    root = path.parent
    def optional(name, default):
        return read_json(root / name) if (root / name).is_file() else default
    configuration = optional('settings.json', {})
    universe = optional('universe.json', {})
    rankings = optional('rankings.json', [])
    # Missing legacy metadata stays missing; do not infer a ranking mode.
    mode = original.get('ranking_mode')
    csv_path = root / 'rankings.csv'
    csv_bytes = csv_path.read_bytes() if csv_path.is_file() and not csv_path.is_symlink() else None
    return {'summary': original, 'settings': configuration.get('settings', {}),
            'universe': universe, 'rankings': rankings, 'mode': mode,
            'path': str(path), 'csv': csv_bytes.decode('utf-8') if csv_bytes else None,
            'csv_sha256': hashlib.sha256(csv_bytes).hexdigest() if csv_bytes else None}


def pool(bundle):
    stocks = bundle['universe'].get('stocks')
    return sorted(s['stock_id'] for s in stocks) if stocks is not None else None


def differences(current, previous):
    reasons = []
    a_strategy, b_strategy = (b['summary'].get('strategy_snapshot') for b in (current, previous))
    if (a_strategy or b_strategy) and (not a_strategy or not b_strategy or a_strategy.get('sha256') != b_strategy.get('sha256')):
        reasons.append('策略內容雜湊不同或未保存，不比較不同条件的名次')
    if current['mode'] is None or previous['mode'] is None or current['mode'] != previous['mode']:
        reasons.append('排名模式不同或未提供')
    a, b = (x['settings'].get('factors', {}) for x in (current, previous))
    if any(k not in a or k not in b or number(a[k]) is None or number(b[k]) is None or number(a[k]) != number(b[k]) for k in FACTOR_KEYS):
        reasons.append('因子期間或權重不同／未提供')
    if pool(current) is None or pool(previous) is None or pool(current) != pool(previous):
        reasons.append('指定股票池不同或未提供')
    a, b = (x['summary'].get('ranking_population') for x in (current, previous))
    if a is None or b is None or sorted(a) != sorted(b):
        reasons.append('實際排名母體不同或未提供；未評估股票不視為排名下跌')
    return reasons


def compare_history(current, histories, top):
    target = current['summary'].get('analysis_date')
    base = {'available': False, 'message': '尚無可比較的前期結果', 'entered': [],
            'exited': [], 'changes': {}, 'limitations': []}
    if not target or current['summary'].get('status') not in ('success', 'partial'):
        return base
    earlier = [b for b in histories if b and b['summary'].get('status') in ('success', 'partial')
               and b['summary'].get('analysis_date') and b['summary']['analysis_date'] < target]
    earlier.sort(key=lambda b: (b['summary']['analysis_date'], b['summary'].get('finished_at', ''),
                               b['summary'].get('run_id', '')), reverse=True)
    compatible = [b for b in earlier if not differences(current, b)]
    if not compatible:
        if earlier:
            previous = earlier[0]
            base['limitations'] = differences(current, previous)
            a = set(current['summary'].get('ranking_population', []))
            b = set(previous['summary'].get('ranking_population', []))
            base['previously_unranked'] = sorted(a - b)
            base['currently_unranked'] = sorted(b - a)
        return base
    previous = compatible[0]
    a = {r['stock_id']: r for r in previous['rankings']}
    b = {r['stock_id']: r for r in current['rankings']}
    old_top = {sid for sid, r in a.items() if r['rank'] <= top}
    new_top = {sid for sid, r in b.items() if r['rank'] <= top}
    return {**base, 'available': True, 'message': f"相較 {previous['summary']['analysis_date']} 的可比較結果",
            'previous_run_id': previous['summary'].get('run_id'), 'previous_path': previous['path'],
            'analysis_date': previous['summary']['analysis_date'],
            'entered': sorted(new_top - old_top), 'exited': sorted(old_top - new_top),
            'changes': {sid: a[sid]['rank'] - b[sid]['rank'] for sid in a.keys() & b.keys()}}


def explain(row, settings):
    raw, scores = row.get('factors', {}), row.get('factor_scores', {})
    weights = {key: number(settings.get(key + '_weight')) for key in FACTOR_NAMES}
    lines, hints, contributions = [], [], {}
    for key, label in FACTOR_NAMES.items():
        value, score = number(raw.get(key)), number(scores.get(key))
        lines.append(f'{label}原始值 {percent(value)}；相對分數 {score_text(score)}；權重 {shown(weights[key])}。')
        if key == 'momentum' and value is not None:
            lines.append('期間價格報酬為' + ('正。' if value > 0 else '負。' if value < 0 else '零。'))
        if score is not None and score >= Decimal(POLICY['high_score']):
            lines.append(f'{label}相對分數 ≥ {POLICY["high_score"]}，在本次股票池中較前。')
        if key in ('momentum', 'trend') and value is not None and value < 0:
            hints.append(f'{label}原始值為負（{percent(value)}）。' +
                         ('短均線仍低於長均線。' if key == 'trend' else
                          '相對分數雖高，期間價格報酬仍為負。' if score is not None and score >= Decimal(POLICY['high_score']) else ''))
        if key == 'volatility' and score is not None and score <= Decimal(POLICY['low_score']):
            hints.append(f'低波動分數 {score} ≤ {POLICY["low_score"]}，在本次母體中價格起伏相對較大。')
    valid = all(v is not None and v >= 0 for v in weights.values()) and sum(
        (v for v in weights.values() if v is not None), Decimal(0)) > 0
    if valid and all(number(scores.get(k)) is not None for k in FACTOR_NAMES):
        total = sum(weights.values())
        contributions = {k: number(scores[k]) * w / total for k, w in weights.items()}
        active = {k: v for k, v in contributions.items() if weights[k] > 0}
        tolerance = Decimal(POLICY['close_contribution_points'])
        high, low = max(active.values()), min(active.values())
        if high == 0:
            lines.append('有效因子的加權貢獻皆為零，無法從分數區分入選主因。')
        elif len(active) > 1 and high - low <= tolerance:
            lines.append('有效因子的加權貢獻接近（差距不超過 1 分），不區分主要因子。')
        else:
            leaders = [FACTOR_NAMES[k] for k, v in active.items() if high - v <= tolerance]
            lines.append('加權貢獻較大：' + '、'.join(leaders) + '。')
            weaker = [FACTOR_NAMES[k] for k, v in active.items() if high - v > tolerance]
            if weaker:
                lines.append('加權貢獻相對較小：' + '、'.join(weaker) + '。')
        lines.append('加權分數貢獻：' + '、'.join(f'{FACTOR_NAMES[k]} {v:.2f}' for k, v in contributions.items()) + '。')
        saved = number(row.get('score'))
        if saved is not None and abs(sum(contributions.values()) - saved) > Decimal('0.00000001'):
            lines.append('保存的總分與分項貢獻不一致，請檢查報表；未重新排名。')
    else:
        lines.append('分項分數或有效權重不足，無法判定入選主因。')
    return lines, hints, {k: str(v) for k, v in contributions.items()}


def build_digest(bundle, histories=(), *, top=10, today=None):
    if not isinstance(top, int) or isinstance(top, bool) or not 1 <= top <= POLICY['max_top']:
        raise ValueError('候選數需為 1～50')
    summary = bundle['summary']
    today = today or taipei_today()
    analysis = summary.get('analysis_date')
    lag = (today - date.fromisoformat(analysis)).days if analysis else None
    comparison = compare_history(bundle, histories, top)
    metadata = {s['stock_id']: s for s in bundle['universe'].get('stocks', [])}
    evaluations = {r['stock_id']: r for r in summary.get('evaluations', [])}
    candidates, hints = [], []
    settings = bundle['settings'].get('factors', {})
    for row in bundle['rankings'][:top]:
        sid = row['stock_id']
        info, evaluation = metadata.get(sid, {}), evaluations.get(sid, {})
        if bundle['mode'] == 'rules':
            lines, warnings, contributions = ['規則模式：依原有動能值排序；score 不是 0～100 的相對因子分數。'], [], {}
        else:
            lines, warnings, contributions = explain(row, settings)
        actual = evaluation.get('actual', {})
        snapshot = evaluation.get('signal') or {}
        candidates.append({**row, 'name': info.get('name') or None, 'industry': info.get('industry') or None,
                           'close': snapshot.get('close'), 'price_date': snapshot.get('date'),
                           'latest_date': actual.get('latest'), 'explanations': lines,
                           'hints': warnings, 'contributions': contributions,
                           'conditions': evaluation.get('conditions', []),
                           'rank_change': comparison['changes'].get(sid)})
    industries = {}
    for row in candidates:
        if row['industry']:
            industries[row['industry']] = industries.get(row['industry'], 0) + 1
    for industry, count in industries.items():
        if len(candidates) >= 2 and Decimal(count) / len(candidates) >= Decimal(POLICY['industry_concentration']):
            hints.append(f'候選股產業集中：{industry} {count}/{len(candidates)} 檔（至少 50%）。')
    unknown = sum(not c['industry'] for c in candidates)
    if unknown:
        hints.append(f'{unknown} 檔候選股產業未提供，不歸為同一產業。')
    if summary.get('excluded'):
        hints.append(f'{len(summary["excluded"])} 檔被排除，排名只代表實際成功評估母體。')
    if lag is not None and lag > 0:
        hints.append(f'行情基準距預覽日 {today} 已 {lag} 個日曆日；無交易日曆，不推定缺漏交易日數。')
    if any(c['close'] is None for c in candidates):
        hints.append('部分訊號日收盤價未保存，顯示未提供；未補查目前資料庫。')
    if bundle['mode'] is None:
        hints.append('舊報表未明確保存排名模式，標示未提供，不據此建立可比較前期。')
    if bundle['csv'] is None:
        hints.append('原報表未提供排名 CSV，本郵件沒有 CSV 附件。')
    hints.append('同分時沿用原排名的股票代號排序；名次不同未必代表總分不同。')
    hints.append('分數是本次股票池的相對排序，不是上漲機率、買進建議或策略有效性證明。')
    marker = {'success': '完整結果', 'partial': '部分結果', 'failed': '失敗／無排名'}.get(summary.get('status'), '狀態未提供')
    mode = '多因子排名' if bundle['mode'] == 'factors' else shown(bundle['mode'])
    title = f'{"【歷史行情】" if lag is not None and lag > 0 else ""}{shown(analysis)}｜{mode}｜{marker}'
    return {'digest_schema_version': 1, 'title': title, 'analysis_date': analysis,
            'strategy_snapshot': summary.get('strategy_snapshot'),
            'ranking_mode': bundle['mode'], 'status': summary.get('status'), 'top': top,
            'preview_date': str(today), 'lag_calendar_days': lag, 'counts': summary.get('counts', {}),
            'specified_pool': pool(bundle), 'ranking_population': summary.get('ranking_population'),
            'universe_source': bundle['universe'].get('source'),
            'universe_downloaded_at': bundle['universe'].get('downloaded_at'),
            'factor_settings': {k: settings[k] for k in (*FACTOR_KEYS, 'top_n') if k in settings}, 'candidates': candidates, 'comparison': comparison,
            'hints': hints, 'excluded': summary.get('excluded', []), 'evaluations': summary.get('evaluations', []),
            'downloads': summary.get('downloads', []), 'policy': POLICY,
            'execution': {k: summary.get(k) for k in ('run_id', 'started_at', 'finished_at', 'version', 'exit_code')},
            'source': bundle['path'], 'ranking_csv': bundle['csv'], 'csv_sha256': bundle['csv_sha256']}


def load_digest(path, *, top=10, history_roots=()):
    saved = read_json(path)
    if saved.get('digest_schema_version') == 1:
        return saved
    bundle = load_bundle(path)
    if bundle is None:
        return read_json(path)
    histories, seen = [], set()
    for root in history_roots:
        root = Path(root).resolve()
        for candidate in root.rglob('summary.json'):
            if not candidate.resolve().is_relative_to(root) or candidate.resolve() in seen:
                continue
            seen.add(candidate.resolve())
            try:
                previous = load_bundle(candidate)
                if previous:
                    histories.append(previous)
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return build_digest(bundle, histories, top=top)


def html_line(line, index):
    import re
    if not line:
        return '<hr style="border:0;border-top:1px solid #dde3ea;margin:20px 0">'
    if index == 0:
        return f'<h1 style="font-size:23px;line-height:1.4">{escape(line)}</h1>'
    if line in ('本次分析範圍', '排名變化', '資料限制與研究提示', '執行資訊') or line.startswith('前 '):
        return f'<h2 style="font-size:20px;color:#174f79">{escape(line)}</h2>'
    if re.match(r'^\d+\. ', line):
        return f'<h3 style="font-size:18px;background:#edf3fa;padding:10px">{escape(line)}</h3>'
    return f'<p style="margin:8px 0">{escape(line)}</p>'


def render_digest(digest):
    """One semantic text representation feeds both HTML and plain text."""
    d = public_data(digest)
    counts = d['counts']
    scope = d['specified_pool']
    settings = d['factor_settings']
    strategy = d.get('strategy_snapshot')
    strategy_lines = [] if not strategy else [f"策略：{strategy['definition']['name']}（{strategy['definition']['id']}；schema {strategy['definition']['schema_version']}）", '策略 SHA-256：' + strategy['sha256'], 'AND 條件：' + json.dumps(strategy['definition']['conditions'], ensure_ascii=False)]
    lines = [d['title'], *strategy_lines, '', '本次分析範圍',
             f"指定 {shown(counts.get('requested'))} 檔；成功評估 {shown(counts.get('evaluated'))} 檔；排除 {shown(counts.get('excluded'))} 檔。",
             '指定股票池：' + ((', '.join(scope[:20]) + (f' … 共 {len(scope)} 檔（完整清單見保存摘要）' if len(scope) > 20 else '')) if scope is not None else MISSING),
             f"行情基準：{shown(d['analysis_date'])}；各候選股行情日另列。",
             f"因子期間：動能 {shown(settings.get('momentum_window'))}；均線 {shown(settings.get('short_window'))}/{shown(settings.get('long_window'))}；波動 {shown(settings.get('volatility_window'))}。",
             '原始值定義：動能為期間價格報酬；趨勢為短均線／長均線－1；波動為每日報酬的母體標準差。',
             '有效權重：' + '、'.join(f'{label} {shown(settings.get(k + "_weight"))}' for k, label in FACTOR_NAMES.items()),
             '', f"前 {d['top']} 名候選股（實際 {len(d['candidates'])} 檔）"]
    for c in d['candidates']:
        change = '尚無可比較結果' if c['rank_change'] is None else f"{c['rank_change']:+d}（正數為上升）"
        lines.extend(['', f"{c['rank']}. {c['stock_id']} {shown(c['name'])}｜{shown(c['industry'])}",
                      f"訊號收盤 {shown(c['close'])}（{shown(c['price_date'])}）；行情最新日 {shown(c['latest_date'])}；總分 {score_text(c.get('score'))}；名次變化 {change}",
                      *c['explanations'], *['研究提示：' + hint for hint in c['hints']],
                      *['條件紀錄：' + json.dumps(condition, ensure_ascii=False) for condition in c.get('conditions', [])]])
    comparison = d['comparison']
    lines += ['', '排名變化', comparison['message']]
    if comparison['available']:
        lines += ['新進前 N：' + (', '.join(comparison['entered']) or '無'),
                  '退出前 N：' + (', '.join(comparison['exited']) or '無')]
    lines += comparison['limitations']
    for key, label in [('previously_unranked', '前次未列入排名，不能稱為新進前 N'),
                       ('currently_unranked', '本次未列入排名，不能稱為排名下跌')]:
        if comparison.get(key):
            lines.append(label + '：' + ', '.join(comparison[key]))
    lines += ['', '資料限制與研究提示', *d['hints']]
    lines += [f"排除 {e.get('stock_id', MISSING)}：{e.get('reason', MISSING)}" for e in d['excluded']]
    lines += ['', '執行資訊', *[f'{k}：{shown(v)}' for k, v in d['execution'].items()]]
    text = '\n'.join(lines)
    html = ('<!doctype html><html lang="zh-Hant"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<body style="margin:0;padding:16px;background:#f3f6fa;color:#203047">'
            '<main style="max-width:720px;margin:auto;background:white;padding:20px;'
            'font:16px/1.7 sans-serif;overflow-wrap:anywhere">'
            + ''.join(html_line(line, i) for i, line in enumerate(lines))
            + '</main></body></html>')
    return text, html
