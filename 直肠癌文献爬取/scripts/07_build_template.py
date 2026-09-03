# -*- coding: utf-8 -*-
"""阶段 7：合并试运行批次 → 正式运行模板队列 tasks_template.sqlite。

来源:
    tasks_sample.sqlite     1000 篇试运行队列（含运行后状态）
    tasks_sample_50.sqlite  50 篇验证样本队列备份（36 done / 14 not_found）

合并规则:
    - 按 PMID 去重；重叠文献优先取 1000 批次行（状态更新）
    - pdf_path 取磁盘上实际存在的文件（优先 1000 批次）
    - 保留各自状态/通道/错误明细，不做状态重置（正式运行前按需
      UPDATE tasks SET status='pending' 重试未完成文献）

输出:
    tasks_template.sqlite        去重合并队列（1041 篇唯一）
    reports/template_merged.csv  PMID/Year/DOI/PMC/Title/Source/Status/Route/PDFPath

用法:
    python scripts\07_build_template.py
"""

import csv
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, connect_db

COLS = ['pmid', 'doi', 'pmc', 'year', 'title', 'title_norm', 'status',
        'attempts', 'route', 'last_error', 'pdf_path', 'license', 'updated_at']


def load(db_path):
    conn = sqlite3.connect(db_path)
    rows = {}
    for r in conn.execute('SELECT %s FROM tasks' % ', '.join(COLS)):
        rows[r[0]] = dict(zip(COLS, r))
    conn.close()
    return rows


def pick_pdf(p1000, p50):
    for p in (p1000, p50):
        if p and os.path.exists(p):
            return p
    return p1000 or p50


def main():
    a = load(os.path.join(ROOT, 'tasks_sample.sqlite'))      # 1000 批次
    b = load(os.path.join(ROOT, 'tasks_sample_50.sqlite'))   # 50 批次
    print('来源: 1000 批次 %d 行 | 50 批次 %d 行' % (len(a), len(b)))

    merged = {}
    for pmid in set(a) | set(b):
        ra, rb = a.get(pmid), b.get(pmid)
        if ra and rb:
            row = dict(ra)
            row['pdf_path'] = pick_pdf(ra['pdf_path'], rb['pdf_path'])
            source = 'both'
        elif ra:
            row = dict(ra)
            source = '1000'
        else:
            row = dict(rb)
            source = '50'
        merged[pmid] = (row, source)

    db_path = os.path.join(ROOT, 'tasks_template.sqlite')
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = connect_db(db_path)
    for pmid, (row, source) in sorted(merged.items()):
        conn.execute(
            'INSERT INTO tasks (%s) VALUES (%s)' % (', '.join(COLS), ', '.join('?' * len(COLS))),
            [row[c] for c in COLS])
    conn.commit()
    dist = dict(conn.execute('SELECT status, COUNT(*) FROM tasks GROUP BY status').fetchall())
    conn.close()

    csv_path = os.path.join(ROOT, 'reports', 'template_merged.csv')
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['PMID', 'Year', 'DOI', 'PMC', 'Title', 'Source',
                    'Status', 'Route', 'Attempts', 'LastError', 'PDFPath'])
        for pmid, (row, source) in sorted(merged.items()):
            w.writerow([pmid, row['year'], row['doi'] or '', row['pmc'] or '',
                        row['title'], source, row['status'], row['route'] or '',
                        row['attempts'] or 0, row['last_error'] or '', row['pdf_path'] or ''])

    overlap = sum(1 for _, s in merged.values() if s == 'both')
    print('模板 %s: %d 篇唯一（重叠 %d）| 状态分布: %s' % (db_path, len(merged), overlap, dist))
    print('清单: %s' % csv_path)


if __name__ == '__main__':
    main()
