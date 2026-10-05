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
from tw_stock_backtest.config import load_config
from tw_stock_backtest.security import public_data, safe_text, child_environment
from tw_stock_backtest.notifications import preview_message
from tw_stock_backtest.research import program_version
from tw_stock_backtest.research_digest import load_digest, render_digest, load_bundle, differences, score_text


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return public_data(json.loads(Path(path).read_text(encoding='utf-8')))


def report_details(path):
    path = Path(path)
    if any(child.is_symlink() for child in path.iterdir()):
        raise ValueError('報表不可包含符號連結')
    if path.name.endswith('.incomplete'):
        return {'kind': 'research', 'status': 'incomplete'}
    if (path / 'summary.json').exists():
        summary = read_json(path / 'summary.json')
        if not summary.get('report_complete'):
            return {'kind': 'research', 'status': 'incomplete'}
        rankings = read_json(path / 'rankings.json')
        return {**summary, 'kind': 'research', 'rankings': rankings,
                'configuration': read_json(path / 'settings.json') if (path / 'settings.json').exists() else {}}
    if all((path / name).is_file() for name in
           ('settings.json', 'results.json', 'input_data.json', 'trades.csv', 'equity.csv')):
        result = read_json(path / 'results.json')
        return {'kind': 'backtest', 'status': 'complete',
                'strategy': result['strategy']['performance'],
                'benchmark': result['benchmark']['performance'],
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
    def __init__(self, config, reports, state, runner=None):
        self.config = Path(config).resolve()
        self.settings = load_config(self.config)
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
                for name in ('summary.json', 'results.json', 'failure.json', 'progress.json'):
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
        if kind not in ('research', 'backtest'):
            raise ValueError('未知工作類型')
        stocks = form.get('stocks', '').split() or self.settings['universe']['stocks']
        if len(stocks) > 10 or len(set(stocks)) != len(stocks) or any(
                not re.fullmatch(r'[0-9]{4}', s) for s in stocks):
            raise ValueError('股票池需為不重複的四位數代號，最多 10 檔')
        args = ['--config', str(self.config), '--stocks', *stocks]
        if kind == 'research':
            as_of = form.get('as_of', '')
            if as_of:
                args += ['--as-of', date.fromisoformat(as_of).isoformat()]
            if form.get('download') != 'yes':
                args += ['--skip-download']
            if form.get('partial') == 'yes':
                args += ['--allow-partial']
            args += ['--initial-days', '90']
        else:
            start, end = (date.fromisoformat(form.get(k, '')) for k in ('start', 'end'))
            if start > end:
                raise ValueError('起日不可晚於迄日')
            ranking = form.get('ranking', 'factors')
            if ranking not in ('rules', 'factors'):
                raise ValueError('未知排名方式')
            args += ['--start', str(start), '--end', str(end), '--ranking', ranking, '--export']
        with self.lock:
            if self.pending >= 4:
                raise ValueError('佇列已滿，請等待現有工作完成')
            job_id = uuid4().hex
            directory = self.state / job_id
            directory.mkdir()
            job = {'run_id': job_id, 'kind': kind, 'status': 'queued', 'started_at': now(),
                   'stocks': stocks, 'notification': 'pending', 'queued_at': now(),
                   'arguments': args, 'version': program_version()}
            save_progress(job, directory / 'job.json')
            self.pending += 1
            self.pool.submit(self.execute, job, directory, args)
        return job_id

    def execute(self, job, directory, args):
        try:
            job['status'] = 'running'
            job['started_at'] = now()
            save_progress(job, directory / 'job.json')
            module = 'run_research' if job['kind'] == 'research' else 'run_portfolio'
            command = [sys.executable, '-m', f'tw_stock_backtest.cli.{module}',
                       *args, '--output-dir', str(directory / 'reports')]
            # Credentials are unnecessary for research child processes.
            env = child_environment()
            with (directory / 'output.log').open('w', encoding='utf-8') as log:
                result = self.runner(command, stdout=log, stderr=subprocess.STDOUT,
                                     timeout=1200, env=env, check=False)
                code = result.returncode
                if code == 0 and job['kind'] == 'backtest':
                    reports = list((directory / 'reports').glob('*/results.json'))
                    if len(reports) != 1:
                        raise ValueError('回測未產生唯一報表')
                    plotted = self.runner([sys.executable, '-m', 'tw_stock_backtest.cli.plot_report',
                                           '--report-dir', str(reports[0].parent)],
                                          stdout=log, stderr=subprocess.STDOUT, timeout=120,
                                          env=env, check=False)
                    code = plotted.returncode
            job.update(exit_code=code, status='success' if code == 0 else
                       'partial' if code == 2 and job['kind'] == 'research' else 'failed')
        except Exception as error:
            job.update(status='failed', exit_code=1, error_type=type(error).__name__)
        finally:
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
                                       benchmark=data['benchmark']['performance'])
                preview_message(summary, directory / 'notification.eml')
                job['notification'] = 'dry-run'
            except Exception as error:
                job['notification'] = f'failed:{type(error).__name__}'
            try:
                save_progress(job, directory / 'job.json')
            finally:
                with self.lock:
                    self.pending -= 1


def create_app(workbench):
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=8192, TRUSTED_HOSTS=['127.0.0.1', 'localhost'])
    token = secrets.token_urlsafe(32)
    app.jinja_env.filters['score_text'] = score_text
    app.jinja_env.filters['pretty'] = lambda value: json.dumps(value, ensure_ascii=False, indent=2)

    @app.before_request
    def guard():
        if request.method == 'POST' and not secrets.compare_digest(request.form.get('csrf', ''), token):
            abort(403)

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; form-action 'self'"
        return response

    @app.get('/')
    def index():
        return render_template('workbench.html', catalog=workbench.catalog(), jobs=workbench.jobs(),
                               csrf=token, stocks=' '.join(workbench.settings['universe']['stocks']))

    @app.post('/jobs')
    def submit():
        try:
            workbench.submit(request.form)
        except (ValueError, OSError) as error:
            return render_template('workbench.html', error=f'工作參數或檔案無法使用（{type(error).__name__}）'), 400
        return redirect(url_for('index'))

    @app.get('/reports/<key>')
    def report(key):
        entry = workbench.catalog().get(key)
        if not entry:
            abort(404)
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
        return render_template('workbench.html', digest=digest, visible_rankings=visible, detail=entry['details'], report_key=key,
                               image=(entry['path'] / 'performance.png').is_file())

    @app.get('/reports/<key>/csv')
    def ranking_csv(key):
        entry = workbench.catalog().get(key)
        if not entry or entry['details'].get('kind') != 'research':
            abort(404)
        path = entry['path'] / 'rankings.csv'
        if not path.is_file() or path.is_symlink():
            abort(404)
        return app.response_class(safe_text(path.read_bytes().decode('utf-8')).encode('utf-8'),
                                  mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=rankings.csv'})

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
        path = entry['path'] / 'performance.png'
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
        return render_template('workbench.html', comparison=rows, left=left, right=right, changed=changed)

    @app.get('/jobs/<job_id>/<name>')
    def job_file(job_id, name):
        if not re.fullmatch(r'[0-9a-f]{32}', job_id) or name not in ('job.json', 'output.log', 'notification.eml'):
            abort(404)
        path = workbench.state / job_id / name
        if not path.is_file() or not path.resolve().is_relative_to(workbench.state):
            abort(404)
        if name == 'notification.eml':
            message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
            return render_template('workbench.html', mail={
                'subject': safe_text(str(message['Subject'])), 'to': str(message['To']),
                'body': safe_text(message.get_body(preferencelist=('plain',)).get_content() if message.is_multipart() else message.get_content())})
        content = json.dumps(read_json(path), ensure_ascii=False, indent=2) if name == 'job.json' else safe_text(path.read_text(encoding='utf-8', errors='replace'))
        return content, 200, {'Content-Type': 'text/plain; charset=utf-8'}

    return app
