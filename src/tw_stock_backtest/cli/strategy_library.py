"""建立、載入、複製或明確更新 JSON 策略。"""
import argparse
from tw_stock_backtest.config import DEFAULT_CONFIG_PATH,load_config
from tw_stock_backtest.strategies import StrategyLibrary,from_settings,load_strategy,canonical,snapshot


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',default='strategies')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('list')
    show=sub.add_parser('show');show.add_argument('id')
    init=sub.add_parser('create');init.add_argument('id');init.add_argument('--name',required=True)
    init.add_argument('--config',default=str(DEFAULT_CONFIG_PATH));init.add_argument('--ranking',choices=['factors','rules'],default='factors')
    copy=sub.add_parser('copy');copy.add_argument('source');copy.add_argument('id');copy.add_argument('--name',required=True)
    save=sub.add_parser('save');save.add_argument('--file',required=True);save.add_argument('--replace',action='store_true')
    args=parser.parse_args(argv);library=StrategyLibrary(args.library)
    try:
        if args.command=='list':
            for s in library.list(): print(s['id'],s['name'],snapshot(s)['sha256'])
        elif args.command=='show': print(canonical(snapshot(library.load(args.id))))
        elif args.command=='create': library.save(from_settings(load_config(args.config),args.ranking,args.id,args.name))
        elif args.command=='copy': library.copy(args.source,args.id,args.name)
        else: library.save(load_strategy(args.file),replace=args.replace)
        return 0
    except (ValueError,OSError) as error:
        print(f'策略操作失敗（{type(error).__name__}）');return 1


if __name__=='__main__':
    raise SystemExit(main())
