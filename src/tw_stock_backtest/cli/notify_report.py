"""預覽研究摘要郵件；只有明確 --send 才連線 Gmail。"""
import argparse
from tw_stock_backtest.notifications import preview_message, send_message
from tw_stock_backtest.research_digest import load_digest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', required=True, help='summary.json、工作台 job.json 或保存的 research-summary.json')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--send', action='store_true')
    mode.add_argument('--dry-run', action='store_true')
    parser.add_argument('--output', default='reports/mail-preview.eml')
    parser.add_argument('--top', type=int, default=10, help='候選數 1～50，預設 10')
    parser.add_argument('--history-dir', action='append', default=None, help='搜尋前期研究報表，可重複指定；預設 reports')
    args = parser.parse_args(argv)
    try:
        summary = load_digest(args.summary, top=args.top, history_roots=args.history_dir or ['reports'])
        if args.send:
            send_message(summary)
            print('已寄送至自己的 Gmail')
        else:
            print(preview_message(summary, args.output))
        return 0
    except Exception as error:
        # SMTP replies can contain account information. Do not print raw errors.
        print(f'通知失敗：{type(error).__name__}；請檢查檔案或憑證設定')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
