import json
import os
import re
import smtplib
import subprocess
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch, Mock

from tw_stock_backtest.data.database import save_records
from tw_stock_backtest.notifications import build_message, preview_message, send_message
from tw_stock_backtest.cli.notify_report import main as notify_main
from tw_stock_backtest.workbench import Workbench, create_app, compare_rankings, report_details
from test_end_to_end import TEST_CONFIG


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'config.toml'
        self.config.write_text(TEST_CONFIG.replace('AAA', '2330'))
        self.wb = Workbench(self.config, self.root / 'reports', self.root / 'state')
        self.addCleanup(self.wb.close)
        self.client = create_app(self.wb).test_client()

    def token(self):
        return re.search(r'name="csrf" value="([^"]+)"', self.client.get('/').text)[1]

    def test_local_web_protection_and_input(self):
        self.assertEqual(self.client.get('/', headers={'Host': 'evil.example'}).status_code, 400)
        self.assertEqual(self.client.post('/jobs', data={'kind': 'research'}).status_code, 403)
        response = self.client.post('/jobs', data={'csrf': self.token(), 'kind': 'research',
                                                  'stocks': '2330;echo'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.wb.jobs(), [])
        self.assertEqual(self.client.get('/jobs/../../config.toml').status_code, 404)

    def test_single_instance_and_restart(self):
        with self.assertRaises(ValueError):
            Workbench(self.config, self.root / 'reports', self.root / 'state')
        stale = self.root / 'other' / 'old'
        stale.mkdir(parents=True)
        (stale / 'job.json').write_text(json.dumps({'status': 'running'}))
        other = Workbench(self.config, self.root / 'reports', self.root / 'other')
        try:
            self.assertEqual(other.jobs()[0]['status'], 'interrupted')
        finally:
            other.close()

    def test_exit_codes_and_failure_previews(self):
        for code, status in [(0, 'success'), (2, 'partial'), (1, 'failed')]:
            with self.subTest(code=code):
                self.wb.runner = Mock(return_value=Mock(returncode=code))
                job_id = self.wb.submit({'kind': 'research'})
                self.wb.pool.submit(lambda: None).result(timeout=5)
                job = json.loads((self.wb.state / job_id / 'job.json').read_text())
                self.assertEqual(job['status'], status)
                self.assertEqual(job['notification'], 'dry-run')
                command = self.wb.runner.call_args.args[0]
                self.assertIn('--skip-download', command)
                self.assertNotIn('shell', self.wb.runner.call_args.kwargs)
        self.wb.runner = Mock(side_effect=subprocess.TimeoutExpired('test', 1))
        self.wb.submit({'kind': 'research'})
        self.wb.pool.submit(lambda: None).result(timeout=5)
        self.assertTrue(any(j.get('error_type') == 'TimeoutExpired' for j in self.wb.jobs()))

    def test_queue_is_bounded_and_notification_failure_is_separate(self):
        release = Event()
        entered = Event()
        def blocked(*args, **kwargs):
            entered.set()
            release.wait(timeout=5)
            return Mock(returncode=0)
        self.wb.runner = blocked
        with patch('tw_stock_backtest.workbench.preview_message', side_effect=OSError('disk')):
            try:
                self.wb.submit({'kind': 'research'})
                self.assertTrue(entered.wait(timeout=2))
                for _ in range(3):
                    self.wb.submit({'kind': 'research'})
                with self.assertRaises(ValueError):
                    self.wb.submit({'kind': 'research'})
                self.assertEqual(sum(j['status'] == 'running' for j in self.wb.jobs()), 1)
            finally:
                release.set()
                self.wb.pool.submit(lambda: None).result(timeout=10)
        for job in self.wb.jobs():
            self.assertEqual(job['status'], 'success')
            self.assertEqual(job['notification'], 'failed:OSError')

    def test_offline_research_and_backtest(self):
        records = []
        for i in range(30):
            price = Decimal(100 + i)
            records.append({'stock_id': '2330', 'date': str(date(2026, 1, 1) + timedelta(days=i)),
                            'open': price, 'high': price, 'low': price, 'close': price,
                            'volume': 1000, 'turnover': 100000, 'trade_count': 10,
                            'change': Decimal(1)})
        save_records(records, db_path=self.root / 'data/stocks.db')
        self.wb.submit({'kind': 'research', 'as_of': '2026-01-30'})
        self.wb.submit({'kind': 'backtest', 'start': '2026-01-25', 'end': '2026-01-30'})
        self.wb.pool.submit(lambda: None).result(timeout=30)
        self.assertEqual([j['status'] for j in self.wb.jobs()], ['success', 'success'])
        catalog = self.wb.catalog()
        self.assertEqual(len(catalog), 2)
        for key, entry in catalog.items():
            response = self.client.get('/reports/' + key)
            self.assertEqual(response.status_code, 200)
            if entry['details']['kind'] == 'research':
                self.assertEqual(len(entry['details']['rankings']), 1)
                self.assertEqual(entry['details']['evaluations'][0]['signal'], {'date': '2026-01-30', 'close': '129'})
                self.assertIn('候選股總覽', response.text)
                preview = self.client.get(f'/reports/{key}/preview')
                self.assertIn('本次分析範圍', preview.text)
                csv_response = self.client.get(f'/reports/{key}/csv')
                self.assertEqual(csv_response.status_code, 200)
                csv_response.close()
                self.assertEqual(self.client.get(f'/compare?left={key}&right={key}').status_code, 200)
            else:
                image = self.client.get(f'/reports/{key}/image')
                self.assertEqual(image.status_code, 200)
                image.close()
        for job in self.wb.jobs():
            response = self.client.get(f"/jobs/{job['run_id']}/notification.eml")
            self.assertEqual(response.status_code, 200)
            self.assertIn('本次分析範圍' if job['kind'] == 'research' else '本機研究工作台執行摘要', response.text)
            response.close()

    def test_ranking_entries_and_incomplete(self):
        left = {'kind': 'research', 'status': 'success', 'rankings': [
            {'stock_id': '2330', 'rank': 2}, {'stock_id': '2317', 'rank': 1}]}
        right = {'kind': 'research', 'status': 'partial', 'rankings': [
            {'stock_id': '2330', 'rank': 1}, {'stock_id': '2454', 'rank': 2}]}
        rows = compare_rankings(left, right)
        self.assertEqual(rows[1]['change'], 1)
        self.assertIsNone(rows[0]['after'])
        with self.assertRaises(ValueError):
            compare_rankings(left, {'kind': 'research', 'status': 'incomplete'})
        incomplete = self.root / 'a.incomplete'
        incomplete.mkdir()
        self.assertEqual(report_details(incomplete)['status'], 'incomplete')
        (incomplete / 'secret').symlink_to(self.config)
        with self.assertRaises(ValueError):
            report_details(incomplete)


class NotificationTests(unittest.TestCase):
    def test_preview_without_credentials_or_network(self):
        with TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True), \
                patch('smtplib.SMTP_SSL') as smtp:
            path = Path(temp) / 'preview.eml'
            preview_message({'status': 'partial', 'counts': {'成功': 2}, 'password': 'secret'}, path)
            self.assertNotIn('secret', path.read_text())
            self.assertEqual(smtp.call_count, 0)
            with self.assertRaises(FileExistsError):
                preview_message({}, path)
            with self.assertRaises(ValueError):
                send_message({})

    def test_mocked_gmail_and_same_recipient(self):
        with patch.dict(os.environ, {'GMAIL_ADDRESS': 'tester@gmail.com',
                                     'GMAIL_APP_PASSWORD': 'fake-app-password'}), \
                patch('smtplib.SMTP_SSL') as smtp:
            send_message({'status': 'success'})
            client = smtp.return_value.__enter__.return_value
            client.login.assert_called_once_with('tester@gmail.com', 'fake-app-password')
            message = client.send_message.call_args.args[0]
            self.assertEqual(message['To'], message['From'])
            self.assertNotIn('fake-app-password', message.as_string())
            self.assertEqual(smtp.call_args.args, ('smtp.gmail.com', 465))

    def test_header_injection_and_cli_error_redaction(self):
        with self.assertRaises(ValueError):
            build_message({}, 'test@gmail.com\nBcc:bad@example.com')
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'summary.json'
            path.write_text('{"status":"failed"}')
            with patch('tw_stock_backtest.cli.notify_report.send_message',
                       side_effect=smtplib.SMTPException('SECRET')), patch('builtins.print') as out:
                self.assertEqual(notify_main(['--summary', str(path), '--send']), 1)
                self.assertNotIn('SECRET', str(out.call_args))


if __name__ == '__main__':
    unittest.main()
