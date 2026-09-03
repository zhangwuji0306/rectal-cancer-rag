# -*- coding: utf-8 -*-
"""阶段 13：批次结果同步 —— 批次库 → reports/run_history.csv（权威历史源）。

用法:
    python 13_sync_batch.py --run pmc_b1 --db tasks_pmc_b1.sqlite

说明:
    - 从批次库读取全部行，以 Run=<run> 追加到 run_history.csv（同 run 已存在则整段替换，
      支持批次重跑）；11_merge_sqlite 将据此重建 tasks.sqlite 与 run_history 表。
"""

import argparse
import csv
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, connect_db

COLS = ['pmid', 'doi', 'pmc', 'year', 'title', 'title_norm', 'status',
        'attempts', 'route', 'last_error', 'pdf_path', 'license', 'updated_at']


def main():
    ap = argparse.ArgumentParser(description='同步批次结果到 run_history.csv')
    ap.add_argument('--run', required=True, help='批次名，如 pmc_b1')
    ap.add_argument('--db', required=True, help='批次库文件名（相对 ROOT）')
    args = ap.parse_args()

    db = args.db if os.path.isabs(args.db) else os.path.join(ROOT, args.db)
    conn = connect_db(db)
    rows = conn.execute(f'SELECT {",".join(COLS)} FROM tasks').fetchall()
    conn.close()
    print(f'{args.run}: 批次库 {len(rows)} 行')

    hist = os.path.join(ROOT, 'reports', 'run_history.csv')
    if os.path.exists(hist):
        with open(hist, encoding='utf-8-sig', newline='') as f:
            rd = csv.DictReader(f)
            header = rd.fieldnames
            existing = list(rd)
    else:
        header = ['Run'] + COLS
        existing = []
    if 'license' not in header:
        header.insert(header.index('updated_at'), 'license')
        for row in existing:
            row['license'] = row.get('license') or 'unverified'
    # 同 run 替换（先删旧行）
    kept = [r for r in existing if r.get('Run') != args.run]
    removed = len(existing) - len(kept)

    with open(hist, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in kept:
            w.writerow([r.get(c, '') for c in header])
        for r in rows:
            w.writerow([args.run] + list(r))
    print(f'run_history.csv: 移除旧 {removed} 行, 追加 {len(rows)} 行（Run={args.run}）→ 共 {len(kept) + len(rows)} 行')


if __name__ == '__main__':
    main()
