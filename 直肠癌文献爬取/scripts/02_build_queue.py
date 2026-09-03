# -*- coding: utf-8 -*-
"""阶段 2：索引信息.csv → 任务队列 tasks.sqlite（默认只入队 2000 年及以后；
2000 年前已封存（Status='archived'）不入队）。

用法:
    全量队列: python 02_build_queue.py
    随机抽样: python 02_build_queue.py --sample 50 --seed 42 --from 2000 --to 2020
              （抽样时自动重建 tasks_sample.sqlite 并写出 reports/sample_50.csv）
"""

import argparse
import csv
import os
import random
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, connect_db, norm_text, now_str


def read_rows(csv_path):
    with open(csv_path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def build(db_path, rows, fresh=False):
    """把行列表写入任务队列；更新元数据但保留下载状态。"""
    conn = sqlite3.connect(db_path)
    if fresh:
        conn.execute('DROP TABLE IF EXISTS tasks')
        conn.commit()
    conn.close()

    conn = connect_db(db_path)
    for r in rows:
        pmid = int(r['PMID'])
        values = (r['DOI'] or None, r['PMC'] or None, int(r['Year']),
                  r['Title'], norm_text(r['Title']), r.get('License') or 'unverified')
        updated = conn.execute(
            'UPDATE tasks SET doi=?, pmc=?, year=?, title=?, title_norm=?, license=? '
            'WHERE pmid=?', values + (pmid,)).rowcount
        if not updated:
            conn.execute(
                'INSERT INTO tasks (pmid, doi, pmc, year, title, title_norm, status, license, updated_at) '
                'VALUES (?,?,?,?,?,?,?,?,?)',
                (pmid,) + values[:5] + ('pending', values[5], now_str()))
    conn.commit()
    counts = dict(conn.execute('SELECT status, COUNT(*) FROM tasks GROUP BY status').fetchall())
    conn.close()
    return counts


def main():
    ap = argparse.ArgumentParser(description='构建下载任务队列')
    ap.add_argument('--csv', default=os.path.join(ROOT, '索引信息.csv'))
    ap.add_argument('--db', default=os.path.join(ROOT, 'tasks.sqlite'))
    ap.add_argument('--sample', type=int, default=0, help='随机抽样数量（0=全量）')
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--from', dest='yfrom', type=int, default=2000)
    ap.add_argument('--to', dest='yto', type=int, default=2020)
    ap.add_argument('--pending-only', action='store_true',
                    help='抽样时仅从未尝试（Status=pending）记录中选取')
    ap.add_argument('--fresh', action='store_true', help='重建队列（清空旧状态）')
    args = ap.parse_args()

    rows = read_rows(args.csv)
    print(f'索引信息.csv 共 {len(rows)} 行')

    if args.sample > 0:
        pool = [r for r in rows if r.get('Status') != 'archived'
                and args.yfrom <= int(r['Year']) <= args.yto
                and (not args.pending_only or r.get('Status') == 'pending')]
        if len(pool) < args.sample:
            print(f'抽样范围仅 {len(pool)} 行，不足 {args.sample}，取全部')
            args.sample = len(pool)
        rng = random.Random(args.seed)
        sample = rng.sample(pool, args.sample)

        os.makedirs(os.path.join(ROOT, 'reports'), exist_ok=True)
        sample_csv = os.path.join(ROOT, 'reports', f'sample_{args.sample}.csv')
        with open(sample_csv, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow(['PMID', 'Year', 'DOI', 'PMC', 'Title'])
            for r in sample:
                w.writerow([r['PMID'], r['Year'], r['DOI'], r['PMC'], r['Title']])
        mix = {'有DOI': sum(1 for r in sample if r['DOI']),
               '有PMC': sum(1 for r in sample if r['PMC']),
               '无DOI': sum(1 for r in sample if not r['DOI'])}
        print(f'抽样清单: {sample_csv}（{len(sample)} 行, seed={args.seed}, 年份 {args.yfrom}-{args.yto}）')
        print(f'样本构成: {mix}')
        rows = sample
        if args.db == os.path.join(ROOT, 'tasks.sqlite'):
            args.db = os.path.join(ROOT, 'tasks_sample.sqlite')
        elif not os.path.isabs(args.db):
            args.db = os.path.join(ROOT, args.db)
        args.fresh = True

    if args.sample == 0:
        inrange = [r for r in rows if r.get('Status') != 'archived'
                   and r['Year'].strip().isdigit()
                   and int(r['Year']) >= 2000]
        print(f'2000 年以后记录: {len(inrange)}（2000 年前已封存不入队）')
        rows = inrange

    counts = build(args.db, rows, fresh=args.fresh)
    total = sum(counts.values())
    print(f'队列 {args.db}: 共 {total} 行 | 状态分布: {counts}')


if __name__ == '__main__':
    main()
