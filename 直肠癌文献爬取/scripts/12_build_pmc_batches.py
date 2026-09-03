# -*- coding: utf-8 -*-
"""阶段 12：从 tasks.sqlite 构建 PMC 优先批次队列（从新到旧，每批 N 篇）。

选择范围：year>=2000 且未封存、pmc 非空、status IN (pending, not_found, failed)。
排序：year DESC, pmid DESC（新→旧，同年份按 PMID 降序保持稳定）。
批次库内 status 一律重置为 pending（引擎只处理 pending；not_found/failed 即重试对象）。
输出：tasks_pmc_b1.sqlite ...（每批一个独立队列库）+ reports/pmc_batches.csv（全部分批清单，Status 列保留原状态）。

用法:
    python 12_build_pmc_batches.py [--batch-size 400] [--max-batches 0] [--first-batch 1]
"""

import argparse
import csv
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, connect_db, now_str, norm_text


def main():
    ap = argparse.ArgumentParser(description='构建 PMC 优先批次队列')
    ap.add_argument('--src', default=os.path.join(ROOT, 'tasks.sqlite'))
    ap.add_argument('--batch-size', type=int, default=400)
    ap.add_argument('--max-batches', type=int, default=0, help='最多生成几批（0=全部）')
    ap.add_argument('--first-batch', type=int, default=1,
                    help='批次起始编号（续跑时用，如 3 表示从 b3 开始命名）')
    args = ap.parse_args()

    con = connect_db(args.src)
    con.row_factory = sqlite3.Row
    pool = con.execute(
        "SELECT pmid, doi, pmc, year, title, status, license FROM tasks "
        "WHERE year>=2000 AND status!='archived' AND pmc IS NOT NULL AND pmc!='' "
        "AND status IN ('pending','not_found','failed') "
        "ORDER BY year DESC, pmid DESC").fetchall()
    con.close()

    print(f'候选池: {len(pool)} 篇（有 PMC 且未完成）')
    from collections import Counter
    print('状态分布:', dict(Counter(r['status'] for r in pool)))

    n = args.batch_size
    batches = [pool[i:i + n] for i in range(0, len(pool), n)]
    if args.max_batches:
        batches = batches[:args.max_batches]
    print(f'批次: {len(batches)} 个（每批 {n}）')

    os.makedirs(os.path.join(ROOT, 'reports'), exist_ok=True)
    manifest = os.path.join(ROOT, 'reports', 'pmc_batches.csv')
    existing_lines = []
    if os.path.exists(manifest):
        with open(manifest, encoding='utf-8-sig', newline='') as f:
            existing_lines = list(csv.reader(f))
    with open(manifest, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['Batch', 'PMID', 'Year', 'DOI', 'PMC', 'Status', 'Title', 'License'])
        # Generated snapshot: never retain stale headers or prior batch rows.
        for i, batch in enumerate(batches, args.first_batch):
            db = os.path.join(ROOT, f'tasks_pmc_b{i}.sqlite')
            if os.path.exists(db):
                os.remove(db)
            conn = connect_db(db)
            for r in batch:
                conn.execute(
                    'INSERT INTO tasks (pmid, doi, pmc, year, title, title_norm, status, attempts, license, updated_at) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (r['pmid'], r['doi'], r['pmc'], r['year'], r['title'],
                     norm_text(r['title']), 'pending', 0, r['license'] or 'unverified', now_str()))
                w.writerow([f'b{i}'] + [r['pmid'], r['year'], r['doi'], r['pmc'], r['status'],
                                        r['title'], r['license'] or 'unverified'])
            conn.commit()
            counts = dict(conn.execute('SELECT status, COUNT(*) FROM tasks GROUP BY status').fetchall())
            conn.close()
            yrs = [r['year'] for r in batch]
            print(f'b{i}: {db} | {len(batch)} 篇 | 状态 {counts} | 年份 {max(yrs)}~{min(yrs)}')
    print(f'清单: {manifest}')


if __name__ == '__main__':
    main()
