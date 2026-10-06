"""Gmail self-notifications. Preview is offline; sending is always explicit."""
import json
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from tw_stock_backtest.research_digest import render_digest
from tw_stock_backtest.security import public_data


def build_message(summary, address=None):
    summary = public_data(summary)
    address = address or 'preview@example.invalid'
    if not re.fullmatch(r'[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@(?:gmail\.com|example\.invalid)', address):
        raise ValueError('請使用自己的 Gmail 地址')
    message = EmailMessage()
    message['From'] = message['To'] = address
    if summary.get('digest_schema_version') == 1:
        text, html = render_digest(summary)
        message['Subject'] = summary['title']
        message.set_content(text)
        message.add_alternative(html, subtype='html')
        if summary.get('ranking_csv') is not None:
            message.add_attachment(summary['ranking_csv'].encode('utf-8'), maintype='text',
                                   subtype='csv', filename='rankings.csv')
        return message
    message['Subject'] = f"研究工作台：{summary.get('status', 'unknown')}"
    # Only known summary fields; never attach settings, environment or logs.
    fields = ('run_id', 'kind', 'status', 'exit_code', 'analysis_date',
              'started_at', 'finished_at', 'counts', 'excluded', 'error_type', 'strategy', 'benchmark', 'last_signal', 'strategy_comparison')
    body = ['本機研究工作台執行摘要', '']
    strategy = summary.get('strategy_snapshot')
    if strategy:
        body += [f"策略：{strategy['definition']['name']}（schema {strategy['definition']['schema_version']}）",
                 '策略 SHA-256：' + strategy['sha256'],
                 'AND 條件：' + json.dumps(strategy['definition']['conditions'], ensure_ascii=False), '']
    body += [f'{key}: {summary[key]}' for key in fields if key in summary]
    body += ['', '程式執行結果不代表策略已證明有效。']
    message.set_content('\n'.join(body))
    return message


def preview_message(summary, output, address=None):
    summary = public_data(summary)
    message = build_message(summary, address)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Old status-only notifications retain the original single-file behavior.
    if summary.get('digest_schema_version') != 1:
        with output.open('xb') as stream:
            stream.write(message.as_bytes())
        return output
    stage = output.with_name(output.name + '.preview.incomplete')
    final = output.with_name(output.name + '.preview')
    if output.exists() or final.exists():
        raise FileExistsError('預覽已存在，請使用新的輸出名稱')
    stage.mkdir()
    text, html = render_digest(summary)
    (stage / 'report.txt').write_text(text, encoding='utf-8')
    (stage / 'report.html').write_text(html, encoding='utf-8')
    (stage / 'research-summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    if summary.get('ranking_csv') is not None:
        (stage / 'rankings.csv').write_bytes(summary['ranking_csv'].encode('utf-8'))
    (stage / 'message.eml').write_bytes(message.as_bytes())
    # Hard link is atomic and refuses overwrite; an interrupted bundle stays marked.
    os.link(stage / 'message.eml', output)
    stage.rename(final)
    return output


def send_message(summary):
    address = os.environ.get('GMAIL_ADDRESS', '')
    password = os.environ.get('GMAIL_APP_PASSWORD', '')
    if not address.endswith('@gmail.com') or not password:
        raise ValueError('需要 GMAIL_ADDRESS 與 GMAIL_APP_PASSWORD')
    message = build_message(summary, address)
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=30,
                          context=ssl.create_default_context()) as smtp:
        smtp.login(address, password)
        smtp.send_message(message)
    # No automatic retry: a lost response may mean the message was already sent.
