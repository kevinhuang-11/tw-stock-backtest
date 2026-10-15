"""Local report browser and serialized subprocess jobs; calculations stay in CLI."""
import fcntl
import json
import re
import secrets
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
from threading import Lock
from uuid import uuid4

from flask import Flask, abort, redirect, render_template, request, send_file, url_for
from tw_stock_backtest.cli.download_universe import save_progress
from tw_stock_backtest.config import load_config, apply_overrides
from tw_stock_backtest.web_forms import FormErrors, job_form, strategy_form, ratio_percent
from tw_stock_backtest.security import public_data, safe_text, child_environment
from tw_stock_backtest.strategies import StrategyLibrary, from_settings, snapshot, FACTOR_FIELDS, RULE_FIELDS
from tw_stock_backtest.notifications import preview_message
from tw_stock_backtest.strategy_comparison import validate_periods
from tw_stock_backtest.research import program_version
from tw_stock_backtest.research_digest import load_digest, render_digest, load_bundle, differences, score_text
from tw_stock_backtest.data.universe import NetworkDisabled, ensure_network_allowed, offline_reason


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return public_data(json.loads(Path(path).read_text(encoding='utf-8')))


def report_details(path):
    path = Path(path)
    if any(child.is_symlink() for child in path.iterdir()):
        raise ValueError('報表不可包含符號連結')
    if path.name.endswith('.incomplete'):
        return {'kind': 'research', 'status': 'incomplete',
                'progress': read_json(path / 'progress.json') if (path / 'progress.json').is_file() else None}
    if (path / 'supplemental_progress.json').exists():
        data = read_json(path / 'supplemental_progress.json')
        return {**data, 'kind':'supplemental', 'status':data.get('status','running') if data.get('complete') else 'incomplete'}
    if (path / 'comparison.json').exists():
        data = read_json(path / 'comparison.json')
        return {**data, 'status': data.get('status', 'complete')}
    if (path / 'summary.json').exists():
        summary = read_json(path / 'summary.json')
        if not summary.get('report_complete'):
            return {'kind': 'research', 'status': 'incomplete'}
        if summary.get('digest_kind')=='watchlist': return {**summary,'kind':'watchlist','status':'complete'}
        rankings = read_json(path / 'rankings.json')
        return {**summary, 'kind': 'research', 'rankings': rankings,
                'configuration': read_json(path / 'settings.json') if (path / 'settings.json').exists() else {}}
    if all((path / name).is_file() for name in
           ('settings.json', 'results.json', 'input_data.json', 'trades.csv', 'equity.csv')):
        result = read_json(path / 'results.json')
        return {'kind': 'backtest', 'status': 'complete',
                'strategy': result['strategy']['performance'],
                'benchmark': result['benchmark']['performance'],
                'market_events': result['strategy']['result'].get('market_events', []),
                'configuration': read_json(path / 'settings.json') if (path / 'settings.json').exists() else {}}
    return {'kind': 'unknown', 'status': 'incomplete'}


def compare_rankings(left, right):
    if any(x.get('kind') != 'research' or x.get('status') not in ('success', 'partial')
           for x in (left, right)):
        raise ValueError('只能比較已完成且有排名的研究報表')
    a = {row['stock_id']: row for row in left['rankings']}
    b = {row['stock_id']: row for row in right['rankings']}
    return [{'stock_id': sid, 'before': a.get(sid, {}).get('rank'),
             'after': b.get(sid, {}).get('rank'),
             'change': a[sid]['rank'] - b[sid]['rank'] if sid in a and sid in b else None}
            for sid in sorted(a.keys() | b.keys())]


class Workbench:
    def __init__(self, config, reports, state, runner=None, strategy_root=None):
        self.config = Path(config).resolve()
        self.settings = load_config(self.config)
        self.library = StrategyLibrary(strategy_root or self.config.parent / "strategies")
        self.reports = Path(reports).resolve()
        self.state = Path(state).resolve()
        self.state.mkdir(parents=True, exist_ok=True)
        self.runner = runner or subprocess.run
        self.lockfile = (self.state / '.lock').open('a')
        try:
            fcntl.flock(self.lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lockfile.close()
            raise ValueError('同一工作目錄已有工作台執行中')
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.lock = Lock()
        self.pending = 0
        self.processes = {}
        for path in self.state.glob('*/job.json'):
            job = read_json(path)
            if job['status'] in ('queued', 'running'):
                job.update(status='interrupted', finished_at=now())
                save_progress(job, path)

    def close(self):
        self.pool.shutdown(wait=True)
        self.lockfile.close()

    def catalog(self):
        paths = set()
        for root in (self.reports, self.state):
            if root.exists():
                for name in ('summary.json', 'results.json', 'failure.json', 'progress.json', 'comparison.json', 'supplemental_progress.json'):
                    paths.update(p.parent for p in root.rglob(name)
                                 if p.resolve().is_relative_to(root))
        result = {}
        for path in sorted(paths):
            import hashlib
            key = hashlib.sha256(str(path).encode()).hexdigest()[:24]
            try:
                details = report_details(path)
            except (OSError, ValueError, KeyError, TypeError):
                details = {'kind': 'unknown', 'status': 'unreadable'}
            result[key] = {'path': path, 'details': details}
        return result

    def jobs(self):
        return sorted((read_json(p) for p in self.state.glob('*/job.json')),
                      key=lambda job: job.get('queued_at', job.get('started_at', '')), reverse=True)

    def submit(self, form):
        kind = form.get('kind')
        if kind not in ('research', 'backtest', 'comparison', 'period_comparison', 'supplemental', 'watchlist_update'):
            raise ValueError('未知工作類型')
        stocks = form.get('stocks', '').split() or self.settings['universe']['stocks']
        if len(stocks) > 10 or len(set(stocks)) != len(stocks) or any(
                not re.fullmatch(r'[0-9]{4}', s) for s in stocks):
            raise ValueError('股票池需為不重複的四位數代號，最多 10 檔')
        try:
            selected = self.library.load(form['strategy_id']) if form.get('strategy_id') else None
        except (ValueError, OSError):
            raise FormErrors({'strategy_id':'所選策略不存在或無法讀取'})
        try:
            selected_many = [self.library.load(sid) for sid in form.get('strategies', '').split()] if kind in ('comparison', 'period_comparison') else []
        except (ValueError, OSError):
            raise FormErrors({'strategies':'所選策略不存在或無法讀取'})
        if kind in ('comparison', 'period_comparison') and (not 2 <= len(selected_many) <= 5 or len({s['id'] for s in selected_many}) != len(selected_many)):
            raise ValueError('比較需要 2～5 份不同策略')
        periods = validate_periods(json.loads(form.get('periods', '[]'))) if kind == 'period_comparison' else None
        args = ['--config', str(self.config), '--stocks', *stocks]
        if kind in ('supplemental','watchlist_update'):
            from tw_stock_backtest.stock_research import today
            from tw_stock_backtest.data.supplemental import Store, institution_plan, listed_scope, validate_month_range
            listed_scope(self.settings, stocks)
            end = date.fromisoformat(form.get('end') or today())
            start = date.fromisoformat(form['start']) if form.get('start') else None
            if end.isoformat() > today(): raise ValueError('截止日不可在未來')
            supplemental_kind=form.get('supplemental_kind') or 'both'
            if supplemental_kind not in (('all','prices','both','revenue','institution') if kind=='watchlist_update' else ('both','revenue','institution')): raise ValueError('更新種類無效')
            revenue_start=form.get('revenue_start') or None
            revenue_end=form.get('revenue_end') or None
            validate_month_range(revenue_start,revenue_end)
            if supplemental_kind not in ('revenue','prices'):
                institution_plan(Store(Path(self.settings['storage']['database_path']).with_name('supplemental.sqlite3')),stocks,end,start)
            args += ['--end',str(end),'--kind',supplemental_kind]
            if start: args += ['--start',str(start)]
            if revenue_start: args += ['--revenue-start',revenue_start,'--revenue-end',revenue_end]
            revenue_source=form.get('revenue_source') or 'latest'
            if revenue_source not in ('latest','history'): raise ValueError('营收來源無效')
            args += ['--revenue-source',revenue_source]
            supplemental_key=[revenue_source,str(start) if start else '',str(end),supplemental_kind,revenue_start,revenue_end]
        elif kind == 'research':
            as_of = form.get('as_of', '')
            if as_of:
                args += ['--as-of', date.fromisoformat(as_of).isoformat()]
            if form.get('download') != 'yes':
                args += ['--skip-download']
            else:
                ensure_network_allowed(self.settings['storage']['database_path'])
            if form.get('partial') == 'yes':
                args += ['--allow-partial']
            args += ['--initial-days', '90']
        elif kind != 'period_comparison':
            start, end = (date.fromisoformat(form.get(k, '')) for k in ('start', 'end'))
            if start > end:
                raise ValueError('起日不可晚於迄日')
            ranking = form.get('ranking') or (selected['ranking_mode'] if selected else 'factors')
            if selected and ranking != selected['ranking_mode']:
                raise FormErrors({'ranking':'排名模式與所選策略不一致；請選擇策略模式'})
            if ranking not in ('rules', 'factors'):
                raise ValueError('未知排名方式')
            args += ['--start', str(start), '--end', str(end)]
            if kind == 'backtest':
                args += ['--ranking', ranking, '--export']
                overrides = {}
                for field, key in (('cash','initial_cash'), ('quantity','quantity'),
                                   ('max_positions','max_positions'), ('sizing','sizing_mode'),
                                   ('slippage','slippage_rate')):
                    if form.get(field):
                        value = int(form[field]) if field in ('quantity','max_positions') else form[field]
                        overrides[key] = value
                        args += ['--'+field.replace('_','-'), str(value)]
                apply_overrides(self.settings, {'backtest':overrides})
        with self.lock:
            if kind in ('supplemental','watchlist_update'):
                for existing in self.jobs():
                    if existing['kind']==kind and existing['status'] in ('queued','running') and set(existing['stocks'])==set(stocks) and existing.get('supplemental_range')==supplemental_key:
                        return existing['run_id']
            if self.pending >= 4:
                raise ValueError('佇列已滿，請等待現有工作完成')
            job_id = uuid4().hex
            directory = self.state / job_id
            directory.mkdir()
            # Freeze validated execution settings so queued jobs cannot drift if the
            # original config.toml is edited after the confirmation screen.
            frozen_config = directory / 'config.toml'
            text = '\n'.join('[' + section + ']\n' + '\n'.join(
                key + ' = ' + json.dumps(value, default=str, ensure_ascii=False)
                for key, value in values.items()) for section, values in self.settings.items())
            frozen_config.write_text(text, encoding='utf-8')
            args[args.index('--config') + 1] = str(frozen_config)
            if periods:
                save_progress(periods, directory / 'periods.json')
                args += ['--periods', str(directory / 'periods.json')]
            if selected:
                save_progress(selected, directory / 'strategy.json')
                args += ['--strategy', str(directory / 'strategy.json')]
            if selected_many:
                paths = []
                for s in selected_many:
                    path = directory / (s['id'] + '.strategy.json')
                    save_progress(s, path); paths.append(str(path))
                args += ['--strategies', *paths]
            job = {'run_id': job_id, 'kind': kind, 'status': 'queued', 'started_at': now(),
                   'stocks': stocks, 'notification': 'pending', 'queued_at': now(),
                   'arguments': args, 'version': program_version()}
            if kind in ('supplemental','watchlist_update'): job['supplemental_range']=supplemental_key
            save_progress(job, directory / 'job.json')
            self.pending += 1
            self.pool.submit(self.execute, job, directory, args)
        return job_id

    def cancel(self, job_id):
        if not re.fullmatch(r'[0-9a-f]{32}', job_id):
            raise ValueError('工作 ID 無效')
        with self.lock:
            path = self.state / job_id / 'job.json'
            if not path.is_file() or not path.resolve().is_relative_to(self.state):
                raise ValueError('工作不存在')
            job = read_json(path)
            if job['status'] not in ('queued', 'running'):
                raise ValueError('工作已結束')
            job['cancel_requested'] = True
            save_progress(job, path)
            process = self.processes.get(job_id)
            if process is not None:
                process.terminate()

    def run_job_process(self, command, job, directory, **kwargs):
        # Injected test runners keep their existing interface; real jobs are cancellable.
        if self.runner is not subprocess.run:
            return self.runner(command, **kwargs)
        timeout = kwargs.pop('timeout')
        kwargs.pop('check', None)
        with self.lock:
            if read_json(directory / 'job.json').get('cancel_requested'):
                return subprocess.CompletedProcess(command, -15)
            process = subprocess.Popen(command, **kwargs)
            self.processes[job['run_id']] = process
        try:
            return subprocess.CompletedProcess(command, process.wait(timeout=timeout))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
        finally:
            with self.lock:
                self.processes.pop(job['run_id'], None)

    def execute(self, job, directory, args):
        try:
            with self.lock:
                if read_json(directory / 'job.json').get('cancel_requested'):
                    job.update(status='cancelled', exit_code=1, cancel_requested=True)
                    return
                job['status'] = 'running'
                job['started_at'] = now()
                save_progress(job, directory / 'job.json')
            module = {'watchlist_update':'update_watchlist','supplemental':'update_supplemental','research':'run_research', 'backtest':'run_portfolio', 'comparison':'compare_strategies', 'period_comparison':'compare_strategies'}[job['kind']]
            command = [sys.executable, '-m', f'tw_stock_backtest.cli.{module}',
                       *args, '--output-dir', str(directory / 'reports')]
            # Credentials are unnecessary for research child processes.
            env = child_environment()
            with (directory / 'output.log').open('w', encoding='utf-8') as log:
                result = self.run_job_process(command, job, directory, stdout=log, stderr=subprocess.STDOUT,
                                     timeout=1200, env=env, check=False)
                code = result.returncode
                if code == 0 and job['kind'] == 'backtest':
                    reports = list((directory / 'reports').glob('*/results.json'))
                    if len(reports) != 1:
                        raise ValueError('回測未產生唯一報表')
                    plotted = self.run_job_process([sys.executable, '-m', 'tw_stock_backtest.cli.plot_report',
                                           '--report-dir', str(reports[0].parent)], job, directory,
                                          stdout=log, stderr=subprocess.STDOUT, timeout=120,
                                          env=env, check=False)
                    code = plotted.returncode
            job.update(exit_code=code, status='success' if code == 0 else
                       'partial' if code == 2 and job['kind'] in ('watchlist_update', 'supplemental', 'research', 'period_comparison') else 'failed')
        except Exception as error:
            job.update(status='failed', exit_code=1, error_type=type(error).__name__)
        finally:
            if read_json(directory / 'job.json').get('cancel_requested'):
                job.update(status='cancelled', exit_code=1, cancel_requested=True)
            job['finished_at'] = now()
            log_path = directory / 'output.log'
            if log_path.is_file():
                try:
                    log_path.write_text(safe_text(log_path.read_text(encoding='utf-8', errors='replace')), encoding='utf-8')
                except OSError:
                    pass
            try:
                summary = dict(job)
                candidates = list((directory / 'reports').glob('*/summary.json'))
                if len(candidates) == 1:
                    summary = load_digest(candidates[0], history_roots=[self.reports, self.state])
                if job['kind'] == 'backtest' and job['status'] == 'success':
                    results = list((directory / 'reports').glob('*/results.json'))
                    if len(results) == 1:
                        data = read_json(results[0])
                        summary.update(strategy=data['strategy']['performance'],
                                       benchmark=data['benchmark']['performance'],
                                       strategy_snapshot=read_json(results[0].parent / 'settings.json').get('strategy_snapshot'),
                                       last_signal=(data['strategy']['result'].get('strategy_diagnostics') or [None])[-1])
                if job['kind'] in ('comparison', 'period_comparison') and job['status'] in ('success', 'partial'):
                    compared = list((directory / 'reports').glob('*/comparison.json'))
                    if len(compared) == 1:
                        summary['strategy_comparison'] = read_json(compared[0])['strategies']
                preview_message(summary, directory / 'notification.eml')
                job['notification'] = 'dry-run'
            except Exception as error:
                job['notification'] = f'failed:{type(error).__name__}'
            try:
                with self.lock:
                    if read_json(directory / 'job.json').get('cancel_requested'):
                        job.update(status='cancelled', exit_code=1, cancel_requested=True)
                    save_progress(job, directory / 'job.json')
            finally:
                with self.lock:
                    self.pending -= 1


def create_app(workbench, dev=False):
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=8192, TRUSTED_HOSTS=['127.0.0.1', 'localhost'])
    token = secrets.token_urlsafe(32)
    app.jinja_env.filters['score_text'] = score_text
    app.jinja_env.filters['percent_input'] = ratio_percent
    app.jinja_env.filters['pretty'] = lambda value: json.dumps(value, ensure_ascii=False, indent=2)
    # 顯示用格式化（千分位、百分比、狀態中文）；不影響保存與匯出的數值。
    from tw_stock_backtest.web_format import FILTERS
    app.jinja_env.filters.update(FILTERS)

    @app.before_request
    def guard():
        if request.method == 'POST' and not secrets.compare_digest(request.form.get('csrf', ''), token):
            abort(403)

    @app.context_processor
    def offline():
        # 合成示例環境：模板據此停用會連網的按鈕並顯示原因。
        return {'offline_reason': offline_reason(workbench.settings['storage']['database_path']),
                'nav_items': navigation(request.path)}

    def lag_for(day):
        # 「落後幾個行情日」以本機行情庫的日期推算；同一次請求只讀一次行情庫。
        from flask import g
        from tw_stock_backtest.data_status import market_lag, price_summary
        if 'price_dates' not in g:
            g.price_dates = price_summary(workbench.settings['storage']['database_path'])[0]
        return market_lag(day, g.price_dates)

    # 以全域函式提供，匯入的巨集（_components.html）也能使用。
    app.jinja_env.globals['market_lag'] = lag_for

    # 導覽列：(圖示, 標籤, 連結, 視為同一區域的路徑開頭)。只列出已註冊路由的項目，不會連到不存在的頁面。
    NAVIGATION = [('home', '總覽', '/', ()), ('research', '研究', '/new/research', ('/new/research',)),
                  ('backtest', '回測', '/new/backtest', ('/new/backtest',)), ('watchlist', '自選', '/watchlist', ('/watchlist',)),
                  ('stock', '個股', '/stocks', ('/stocks',)), ('strategy', '策略', '/strategies', ('/strategies',)),
                  ('report', '報表', '/reports', ('/reports', '/compare', '/jobs'))]

    def navigation(path):
        registered = {rule.rule for rule in app.url_map.iter_rules()}
        return [{'icon': icon, 'label': label, 'href': href,
                 'active': path == href or any(path.startswith(prefix) for prefix in prefixes)}
                for icon, label, href, prefixes in NAVIGATION if href in registered]

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; form-action 'self'"
        return response

    from tw_stock_backtest.supplemental_views import register
    register(app, workbench, token)

    @app.get('/')
    def index():
        catalog = workbench.catalog()
        jobs = workbench.jobs()
        for job in jobs:
            root = workbench.state / job['run_id'] / 'reports'
            job['report_links'] = [{'key': key, 'name': entry['path'].name}
                                   for key, entry in catalog.items() if entry['path'].is_relative_to(root)]
        from tw_stock_backtest.stock_research import Watchlist
        from tw_stock_backtest.web_views import report_list
        reports = report_list(catalog)
        research = [r for r in report_list(catalog, 'research') if r['status'] in ('success', 'partial')]
        try:
            watchlist_count = len(Watchlist(workbench.state / 'private' / 'watchlist.sqlite3').items())
        except (OSError, ValueError):
            watchlist_count = 0
        latest = max((entry['details'].get('analysis_date') or '' for entry in catalog.values()
                      if entry['details'].get('kind') == 'research'), default='')
        from tw_stock_backtest.data_status import data_status
        return render_template('home.html', jobs=jobs, reports=reports, csrf=token,
                               status=data_status(workbench.settings['storage']['database_path']),
                               report_count=len(reports), watchlist_count=watchlist_count,
                               active_count=sum(job['status'] in ('queued', 'running') for job in jobs),
                               latest_research=latest or None, research_reports=research,
                               first_visit=not jobs and not research)

    def prefilled_stocks():
        # 其他頁面可用 ?stocks= 帶入股票；格式不符就退回設定檔的股票池。
        codes = request.args.get('stocks', '').split()
        if 1 <= len(codes) <= 10 and len(set(codes)) == len(codes) and all(re.fullmatch(r'[0-9]{4}', c) for c in codes):
            return ' '.join(codes)
        return ' '.join(workbench.settings['universe']['stocks'])

    @app.get('/new/research')
    def new_research():
        from tw_stock_backtest.data_status import data_status
        return render_template('new_research.html', csrf=token, strategies=workbench.library.list(),
                               status=data_status(workbench.settings['storage']['database_path']),
                               stocks=prefilled_stocks(), selected_strategy=request.args.get('strategy_id', ''),
                               prefill_download=request.args.get('download') == 'yes')

    @app.get('/new/backtest')
    def new_backtest():
        from tw_stock_backtest.data_status import data_status
        return render_template('new_backtest.html', csrf=token, strategies=workbench.library.list(),
                               status=data_status(workbench.settings['storage']['database_path']),
                               execution=workbench.settings['backtest'], stocks=prefilled_stocks(),
                               selected_strategy=request.args.get('strategy_id', ''))

    @app.get('/strategies')
    def strategies():
        selected = request.args.get('id')
        try:
            value = workbench.library.load(selected) if selected else from_settings(workbench.settings, strategy_id='new_strategy', name='新策略')
        except (ValueError, OSError):
            abort(404)
        return render_template('strategies.html', strategies=workbench.library.list(), strategy=value,
                               fingerprint=snapshot(value)['sha256'], csrf=token, editing=bool(selected))

    @app.get('/api/stock-options')
    def stock_options():
        from tw_stock_backtest.stock_research import identities
        try:
            return {'stocks': list(identities(workbench).values())}
        except (OSError, ValueError):
            return {'error':'股票名單無法讀取'}, 503

    @app.post('/strategies')
    def save_strategy():
        try:
            if request.form.get('action') == 'copy':
                from uuid import uuid4
                new_id=request.form.get('id') or 'strategy_'+uuid4().hex[:16]
                workbench.library.copy(request.form['source'],new_id,request.form['name'])
                saved_id=new_id
            else:
                value=strategy_form(request.form)
                workbench.library.save(value,replace=request.form.get('replace')=='yes')
                saved_id=value['id']
        except FormErrors as error:
            return {'errors':error.errors},400
        except (ValueError, OSError, KeyError):
            field='strategy_json' if request.form.get('action')=='import' else 'name'
            return {'errors':{field:'無法保存：策略無效、識別已存在或檔案不可寫入；原設定未變'}},400
        return redirect(url_for('strategies',id=saved_id))

    @app.post('/jobs')
    def submit():
        try:
            workbench.submit(job_form(request.form))
        except FormErrors as error:
            return {'errors':error.errors},400
        except NetworkDisabled as error:
            return {'errors':{'_form':str(error)+'；未建立工作。'}},400
        except (ValueError, OSError) as error:
            return {'errors':{'_form':'工作參數或檔案無法使用；未執行。請核對日期與策略。'}},400
        return redirect(url_for('index'))

    @app.get('/reports')
    def reports():
        from tw_stock_backtest.web_views import REPORT_KINDS, kind_counts, report_list
        catalog = workbench.catalog()
        selected = request.args.get('kind') or None
        if selected is not None and selected not in REPORT_KINDS:
            abort(400)
        research = [r for r in report_list(catalog, 'research') if r['status'] in ('success', 'partial')]
        return render_template('reports.html', reports=report_list(catalog, selected), selected=selected,
                               kinds=REPORT_KINDS, counts=kind_counts(catalog), total=len(catalog),
                               research_reports=research)

    @app.get('/reports/<key>')
    def report(key):
        entry = workbench.catalog().get(key)
        if not entry:
            abort(404)
        if entry['details'].get('kind')=='watchlist':
            text, html=render_digest(entry['details'])
            body=html.split('<body',1)[1].split('>',1)[1].rsplit('</body>',1)[0]
            # 郵件內容自帶一級標題；嵌入頁面時各級標題降一級，頁面只保留一個 h1。郵件本身不變。
            for level in (3, 2, 1):
                body=body.replace(f'<h{level} ',f'<h{level+1} ').replace(f'</h{level}>',f'</h{level+1}>')
            return render_template('watchlist_result.html',body=body,report_key=key,rows=entry['details']['rows'])
        digest = None
        if entry['details'].get('kind') == 'research' and entry['details'].get('status') in ('success', 'partial', 'failed'):
            try:
                digest = load_digest(entry['path'] / 'summary.json',
                                     top=int(request.args.get('top', 10)),
                                     history_roots=[workbench.reports, workbench.state])
            except ValueError:
                abort(400)
        visible = [r for r in entry['details'].get('rankings', [])
                   if request.args.get('q', '').strip() in r['stock_id']]
        ordering = request.args.get('sort', 'rank')
        if ordering == 'stock_id':
            visible.sort(key=lambda r: r['stock_id'])
        elif ordering == 'score':
            from decimal import Decimal
            visible.sort(key=lambda r: (-Decimal(str(r['score'])), r['stock_id']))
        if entry['details'].get('kind') == 'research':
            return render_template('report_research.html', digest=digest, visible_rankings=visible,
                                   detail=entry['details'], report_key=key)
        image = any((entry['path'] / name).is_file() for name in ('performance.png', 'comparison.png'))
        if entry['details'].get('kind') in ('backtest', 'comparison', 'period_comparison'):
            trades, fees = [], 0
            trades_path = entry['path'] / 'trades.csv'
            if entry['details']['kind'] == 'backtest' and trades_path.is_file() and not trades_path.is_symlink():
                import csv
                from decimal import Decimal, InvalidOperation
                with trades_path.open(encoding='utf-8-sig', newline='') as file:
                    trades = [public_data(row) for row in csv.DictReader(file)]
                for row in trades:
                    if row.get('source') == 'strategy':
                        try:
                            fees += Decimal(row.get('commission') or 0) + Decimal(row.get('tax') or 0)
                        except InvalidOperation:
                            pass
                trades = [row for row in trades if row.get('source') in ('strategy', 'benchmark')]
            return render_template('report_backtest.html', detail=entry['details'], report_key=key,
                                   image=image, trades=trades, fees=fees)
        return render_template('report_other.html', detail=entry['details'], report_key=key,
                               image=any((entry['path'] / name).is_file() for name in ('performance.png','comparison.png')))

    @app.get('/reports/<key>/csv')
    def ranking_csv(key):
        entry = workbench.catalog().get(key)
        if not entry or entry['details'].get('kind') not in ('watchlist', 'research', 'comparison', 'period_comparison'):
            abort(404)
        filename = 'rankings.csv' if entry['details']['kind'] in ('research','watchlist') else 'comparison.csv'
        path = entry['path'] / filename
        if not path.is_file() or path.is_symlink():
            abort(404)
        return app.response_class(safe_text(path.read_bytes().decode('utf-8')).encode('utf-8'),
                                  mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename={filename}'})

    @app.get('/reports/<key>/preview')
    def research_preview(key):
        entry = workbench.catalog().get(key)
        if not entry or not (entry['path'] / 'summary.json').is_file():
            abort(404)
        try:
            digest = load_digest(entry['path'] / 'summary.json', top=int(request.args.get('top', 10)),
                                 history_roots=[workbench.reports, workbench.state])
            if not digest.get('digest_schema_version'):
                abort(400)
            text, html = render_digest(digest)
        except ValueError:
            abort(400)
        return (text, 200, {'Content-Type': 'text/plain; charset=utf-8'}) if request.args.get('format') == 'text' else html

    @app.get('/reports/<key>/image')
    def report_image(key):
        entry = workbench.catalog().get(key)
        if not entry:
            abort(404)
        path = entry['path'] / ('comparison.png' if entry['details']['kind']=='comparison' else 'performance.png')
        if not path.resolve().is_relative_to(entry['path'].resolve()) or not path.is_file():
            abort(404)
        return send_file(path, mimetype='image/png')

    @app.get('/compare')
    def compare():
        catalog = workbench.catalog()
        try:
            left, right = [catalog[request.args[k]]['details'] for k in ('left', 'right')]
            rows = compare_rankings(left, right)
            bundles = [load_bundle(catalog[request.args[k]]['path'] / 'summary.json') for k in ('left', 'right')]
            changed = (any(b is None for b in bundles) or
                       bool(differences(bundles[1], bundles[0])) or
                       not left.get('analysis_date') or not right.get('analysis_date') or
                       left['analysis_date'] >= right['analysis_date'])
        except (KeyError, ValueError):
            abort(400)
        return render_template('compare.html', comparison=rows, left=left, right=right, changed=changed)

    @app.post('/jobs/<job_id>/cancel')
    def cancel_job(job_id):
        try:
            workbench.cancel(job_id)
        except (ValueError, OSError):
            return '無法取消：工作已結束或不可存取', 400
        return redirect(url_for('index'))

    @app.get('/jobs/<job_id>/summary')
    def job_summary(job_id):
        if not re.fullmatch(r'[0-9a-f]{32}',job_id): abort(404)
        path=workbench.state/job_id/'job.json'
        if not path.is_file() or path.is_symlink(): abort(404)
        job=read_json(path)
        links=[{'key':k,'name':e['path'].name} for k,e in workbench.catalog().items()
               if e['path'].is_relative_to(workbench.state/job_id/'reports')]
        return render_template('job_summary.html',job=job,links=links)

    @app.get('/jobs/<job_id>/<name>')
    def job_file(job_id, name):
        if not re.fullmatch(r'[0-9a-f]{32}', job_id) or name not in ('job.json', 'output.log', 'notification.eml'):
            abort(404)
        path = workbench.state / job_id / name
        if not path.is_file() or not path.resolve().is_relative_to(workbench.state):
            abort(404)
        if name == 'notification.eml':
            message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
            return render_template('mail_preview.html', mail={
                'subject': safe_text(str(message['Subject'])), 'to': str(message['To']),
                'body': safe_text(message.get_body(preferencelist=('plain',)).get_content() if message.is_multipart() else message.get_content())})
        content = json.dumps(read_json(path), ensure_ascii=False, indent=2) if name == 'job.json' else safe_text(path.read_text(encoding='utf-8', errors='replace'))
        return content, 200, {'Content-Type': 'text/plain; charset=utf-8'}

    from tw_stock_backtest.stock_research import register_stock_routes
    register_stock_routes(app, workbench, token)

    if dev:
        # 開發用的元件展示頁；正式啟動時不註冊，也不出現在導覽列。
        @app.get('/dev/components')
        def dev_components():
            return render_template('dev_components.html', demo_stocks=' '.join(workbench.settings['universe']['stocks']))
    return app
