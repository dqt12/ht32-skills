import argparse
import sys
import time


parser = argparse.ArgumentParser()
parser.add_argument("--stdout")
parser.add_argument("--stderr")
parser.add_argument("--exit-code", type=int, default=0)
parser.add_argument("--sleep", type=float, default=0)
parser.add_argument("--password")
parser.add_argument("--token")
args = parser.parse_args()

if args.stdout:
    print(args.stdout, flush=True)
if args.stderr:
    print(args.stderr, file=sys.stderr, flush=True)
if args.sleep:
    time.sleep(args.sleep)
raise SystemExit(args.exit_code)
