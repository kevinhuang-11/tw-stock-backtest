import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_workbench import TEST_CONFIG
from tw_stock_backtest.workbench import Workbench, create_app
from tw_stock_backtest.security import child_environment, public_data, safe_text
from tw_stock_backtest.notifications import build_message, preview_message
from test_research_digest import bundle
from tw_stock_backtest.research_digest import build_digest

SENTINEL = 'synthetic-sensitive-value'


class CredentialBoundaries(unittest.TestCase):
    def test_child_environment_excludes_all_credentials(self):
        with patch.dict('os.environ', {'PATH': '/bin', 'GMAIL_APP_PASSWORD': SENTINEL,
                                      'GITHUB_TOKEN': SENTINEL, 'OTHER_API_KEY': SENTINEL}, clear=True):
            self.assertEqual(child_environment(), {'PATH': '/bin', 'MPLBACKEND': 'Agg'})

    def test_nested_fields_logs_and_email_previews(self):
        value = {'settings': {'gmail_app_password': SENTINEL},
                 'counts': {'api_token': SENTINEL},
                 'excluded': [{'reason': 'api_key=' + SENTINEL}]}
        self.assertNotIn(SENTINEL, json.dumps(public_data(value)))
        self.assertNotIn(SENTINEL, safe_text('password="' + SENTINEL + '"'))
        message = build_message({'status': 'failed', **value})
        self.assertNotIn(SENTINEL, message.get_content())
        digest = build_digest(bundle())
        digest['execution']['credentials'] = SENTINEL
        digest['factor_settings']['gmail_app_password'] = SENTINEL
        with TemporaryDirectory() as temp:
            output = Path(temp) / 'preview.eml'
            preview_message(digest, output)
            for p in Path(temp).rglob('*'):
                if p.is_file():
                    self.assertNotIn(SENTINEL.encode(), p.read_bytes(), p.name)

    def test_web_reports_logs_errors_csrf_and_host(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / 'config.toml'
            config.write_text(TEST_CONFIG.replace('AAA', '2330'))
            wb = Workbench(config, root / 'reports', root / 'state')
            try:
                app = create_app(wb)
                client = app.test_client()
                job_id = 'a' * 32
                directory = wb.state / job_id
                directory.mkdir()
                (directory / 'job.json').write_text(json.dumps({'status': 'failed', 'token': SENTINEL}))
                (directory / 'output.log').write_text('GMAIL_APP_PASSWORD=' + SENTINEL)
                for name in ('job.json', 'output.log'):
                    response = client.get(f'/jobs/{job_id}/{name}')
                    self.assertEqual(response.status_code, 200)
                    self.assertNotIn(SENTINEL, response.text)
                self.assertEqual(client.get('/', headers={'Host': 'evil.example'}).status_code, 400)
                self.assertEqual(client.post('/jobs', headers={'Origin': 'https://evil.example'},
                                             data={'kind': 'research'}).status_code, 403)
                self.assertEqual(client.get('/.env').status_code, 404)
                self.assertEqual(client.get('/api/credentials').status_code, 404)
                import re
                # No job list needed here; render controlled blank index for the token.
                (directory / 'job.json').write_text(json.dumps({'run_id': job_id, 'kind': 'research',
                    'status': 'failed', 'notification': 'none'}))
                token = re.search(r'name="csrf" value="([^"]+)"', client.get('/').text)[1]
                with patch.object(wb, 'submit', side_effect=OSError(SENTINEL)):
                    response = client.post('/jobs', data={'csrf': token})
                self.assertEqual(response.status_code, 400)
                self.assertNotIn(SENTINEL, response.text)
                report = root / 'reports' / 'test'
                report.mkdir(parents=True)
                b = bundle()
                (report/'summary.json').write_text(json.dumps(b['summary']))
                (report/'rankings.json').write_text(json.dumps(b['rankings']))
                (report/'settings.json').write_text(json.dumps({'settings': {'gmail_app_password': SENTINEL}}))
                key = next(iter(wb.catalog()))
                response = client.get('/reports/' + key)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(SENTINEL, response.text)
            finally:
                wb.close()
