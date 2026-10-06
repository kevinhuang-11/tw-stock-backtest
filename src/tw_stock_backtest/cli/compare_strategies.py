"""同一執行設定、行情快照與基準的少量策略比較。"""
import argparse
import json
from pathlib import Path
from tw_stock_backtest.config import DEFAULT_CONFIG_PATH,load_config,apply_overrides
from tw_stock_backtest.strategies import load_strategy
from tw_stock_backtest.strategy_comparison import compare_strategies, compare_periods


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument('--strategies',nargs='+',required=True)
    parser.add_argument('--stocks',nargs='+')
    parser.add_argument('--start');parser.add_argument('--end')
    parser.add_argument('--periods', help='明確期間與用途的 JSON 檔；不可同時使用 start/end')
    parser.add_argument('--output-dir',default='reports/comparisons')
    args=parser.parse_args(argv)
    try:
        settings=apply_overrides(load_config(args.config),{'universe':{'stocks':args.stocks}})
        strategies = [load_strategy(p) for p in args.strategies]
        if args.periods:
            if args.start or args.end:
                raise ValueError('periods 與 start/end 互斥')
            path, code = compare_periods(settings, strategies,
                json.loads(Path(args.periods).read_text(encoding='utf-8')), args.output_dir)
            print(path)
            return code
        if not args.start or not args.end:
            raise ValueError('需提供 start/end 或 periods')
        print(compare_strategies(settings,strategies,args.start,args.end,args.output_dir))
        return 0
    except Exception as error:
        print(f'策略比較失敗（{type(error).__name__}）；未完成產物保留 .incomplete')
        return 1


if __name__=='__main__':
    raise SystemExit(main())
