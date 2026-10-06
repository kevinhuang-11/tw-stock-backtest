"""啟動僅供本機使用的研究工作台。"""
import argparse
from tw_stock_backtest.config import DEFAULT_CONFIG_PATH
from tw_stock_backtest.workbench import Workbench, create_app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument('--reports-dir', default='reports')
    parser.add_argument('--state-dir', default='reports/workbench')
    parser.add_argument('--strategy-dir', default='strategies')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args(argv)
    workbench = Workbench(args.config, args.reports_dir, args.state_dir, strategy_root=args.strategy_dir)
    try:
        create_app(workbench).run(host='127.0.0.1', port=args.port, debug=False, use_reloader=False)
    finally:
        workbench.close()


if __name__ == '__main__':
    main()
