# -*- coding: utf-8 -*-
"""阶段 8：构建批次运行组（上轮 1000 篇中的 not_found 重跑 + 新抽 500 条）。

组成：
    - 重跑集：tasks_sample.sqlite 中 status='not_found' 的全部条目（2026-08-21 千篇试运行）
    - 新抽集：tasks.sqlite 的 2000-2021 池，剔除模板（tasks_template.sqlite 全部 1041 条）
      后按 seed=42 随机抽取 500 条（确定性可复现）
输出：
    - tasks_batch810.sqlite：干净队列（全部 status='pending'、attempts=0）
    - reports/batch810.csv：逐条组成清单（source=retry/new）
"""

import csv
import os
import random
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, connect_db

SEED = 42
NEW_COUNT = 500
MIN_YEAR, MAX_YEAR = 2000, 2021

FIELDS = ('pmid', 'doi', 'pmc', 'year', 'title', 'title_norm')


def main():
    sample_db = os.path.join(ROOT, 'tasks_sample.sqlite')
    template_db = os.path.join(ROOT, 'tasks_template.sqlite')
    full_db = os.path.join(ROOT, 'tasks.sqlite')
    out_db = os.path.join(ROOT, 'tasks_batch810.sqlite')
    out_csv = os.path.join(ROOT, 'reports', 'batch810.csv')

    # 1) 重跑集：上轮 1000 篇中的 not_found
    c = sqlite3.connect(sample_db)
    retry = c.execute(
        "SELECT pmid, doi, pmc, year, title, title_norm FROM tasks WHERE status='not_found'"
    ).fetchall()
    c.close()

    # 2) 模板排除集（避免与任何已跑条目重叠）
    c = sqlite3.connect(template_db)
    exclude = {r[0] for r in c.execute('SELECT pmid FROM tasks')}
    c.close()

    # 3) 新抽集：2000-2021 池剔除模板后 seed=42 固定随机抽 500
    c = sqlite3.connect(full_db)
    pool = c.execute(
        'SELECT pmid, doi, pmc, year, title, title_norm FROM tasks '
        'WHERE year BETWEEN ? AND ?', (MIN_YEAR, MAX_YEAR)).fetchall()
    c.close()
    cand = [r for r in pool if r[0] not in exclude]
    rnd = random.Random(SEED)
    rnd.shuffle(cand)
    new500 = cand[:NEW_COUNT]

    # 4) 组装 + 校验
    rows = [('retry',) + r for r in retry] + [('new',) + r for r in new500]
    retry_pmids = {r[0] for r in retry}
    new_pmids = {r[0] for r in new500}
    assert not (retry_pmids & new_pmids), '重跑集与新抽集重叠（不应发生，新抽已剔除模板）'

    conn = connect_db(out_db)
    conn.execute('DELETE FROM tasks')
    conn.executemany(
        'INSERT INTO tasks (pmid, doi, pmc, year, title, title_norm) '
        'VALUES (?,?,?,?,?,?)', [r[1:] for r in rows])
    conn.commit()
    dist = dict(conn.execute('SELECT status, COUNT(*) FROM tasks GROUP BY status').fetchall())
    conn.close()

    # 5) 组成清单 CSV
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    with open(out_csv, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['source'] + list(FIELDS))
        for row in rows:
            w.writerow(row)

    # 6) 组成统计
    def comp(rs):
        doi = sum(1 for r in rs if r[1])
        pmc = sum(1 for r in rs if r[2])
        no_doi = sum(1 for r in rs if not r[1])
        return f'有DOI {doi} / 有PMC {pmc} / 无DOI {no_doi}'

    print('重跑集（上轮1000 not_found）:', len(retry), '条 |', comp(retry))
    print('新抽集（剔除模板后 seed=42）:', len(new500), '条 |', comp(new500))
    print('候选池大小（2000-2021 剔除模板）:', len(cand))
    print('合计:', len(rows), '条 | 输出库状态分布:', dist)
    yrs = sorted({r[4] for r in rows if r[4]})
    print('年份范围:', yrs[0], '-', yrs[-1])
    print('输出:', out_db, '|', out_csv)


if __name__ == '__main__':
    main()
