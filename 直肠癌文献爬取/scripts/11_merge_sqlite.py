# -*- coding: utf-8 -*-
"""阶段 11：合并全部 sqlite 任务库 → 单一 tasks.sqlite。

设计：
- tasks         表：6289 行全量队列的"当前状态"（archived/done/not_found/failed/pending），
                  状态由全部批次运行推导：任一运行 done → done；否则取 updated_at 最近的一次运行；
- run_history   表：各批次库逐行明细（run, pmid, ..., status, route, attempts,
                  last_error, pdf_path, updated_at），保留完整可追溯性；
- reports/run_history.csv：run_history 的人工可读导出（"其他形式"保存），
  也是本脚本重建时的权威历史源（批次库删除后仍可重建）；
- 合并校验通过后删除 6 个批次库（含 -wal/-shm），tasks.sqlite 以临时文件重建后原子替换。

状态推导规则：
    year<2000            → archived（last_error='2000年前，封存不爬取'）
    任一运行 done        → done（route 取该运行；pdf_path 指向 pdfs_merged 实际文件）
    否则                 → updated_at 最近的一次运行的 status/last_error/route/pdf_path
    attempts = 各运行 attempts 之和；updated_at = 各运行 updated_at 最大值
"""

import csv
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT

BATCHES = [
    ('sample_50',   'tasks_sample_50.sqlite'),
    ('sample_1000', 'tasks_sample.sqlite'),
    ('template',    'tasks_template.sqlite'),
    ('batch810',    'tasks_batch810.sqlite'),
    ('test3',       'tasks_test3.sqlite'),
    ('test10',      'tasks_test10.sqlite'),
    ('validate400', 'tasks_validate400.sqlite'),
    ('pmc_b1',      'tasks_pmc_b1.sqlite'),
    ('pmc_b2',      'tasks_pmc_b2.sqlite'),
    ('pmc_b3',      'tasks_pmc_b3.sqlite'),
    ('pmc_b4',      'tasks_pmc_b4.sqlite'),
    ('pmc_b5',      'tasks_pmc_b5.sqlite'),
]
TASKS_COLS = ['pmid', 'doi', 'pmc', 'year', 'title', 'title_norm', 'status',
              'attempts', 'route', 'last_error', 'pdf_path', 'license', 'updated_at']
ARCHIVE_NOTE = '2000年前，封存不爬取'
MERGED_DIR = os.path.join(ROOT, 'pdfs_merged')


def read_batch(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cols = [r[1] for r in cur.execute('PRAGMA table_info(tasks)').fetchall()]
    rows = [dict(zip(cols, r)) for r in cur.execute('SELECT * FROM tasks').fetchall()]
    for row in rows:
        row.setdefault('license', 'unverified')
    conn.close()
    return rows


def load_run_rows():
    """优先读 reports/run_history.csv（权威历史源），缺失时回退读批次库。"""
    hist_csv = os.path.join(ROOT, 'reports', 'run_history.csv')
    if os.path.exists(hist_csv):
        with open(hist_csv, encoding='utf-8-sig', newline='') as f:
            rd = csv.DictReader(f)
            rows = []
            for r in rd:
                d = {k: (None if v == '' else v) for k, v in r.items() if k != 'Run'}
                d.setdefault('license', 'unverified')
                for col in ('pmid', 'year'):
                    if d.get(col) is not None:
                        d[col] = int(d[col])
                if d.get('attempts') is not None:
                    d['attempts'] = int(d['attempts'])
                rows.append((r['Run'], d))
        print(f'run_history.csv 读取: {len(rows)} 行')
        return rows
    run_rows = []
    for run, fname in BATCHES:
        p = os.path.join(ROOT, fname)
        if not os.path.exists(p):
            continue
        run_rows += [(run, r) for r in read_batch(p)]
    return run_rows


def main():
    # 1) 读取全部批次运行记录
    run_rows = load_run_rows()
    per_batch = {}
    for run, r in run_rows:
        per_batch[run] = per_batch.get(run, 0) + 1
    print('各批次行数:', per_batch, '合计', len(run_rows))

    # 2) 读取旧 tasks.sqlite 作为基础行
    old = os.path.join(ROOT, 'tasks.sqlite')
    conn = sqlite3.connect(old)
    existing_cols = {row[1] for row in conn.execute('PRAGMA table_info(tasks)')}
    if 'license' not in existing_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN license TEXT NOT NULL DEFAULT 'unverified'")
        conn.commit()
    base = [dict(zip(TASKS_COLS, r)) for r in conn.execute(
        f'SELECT {",".join(TASKS_COLS)} FROM tasks').fetchall()]
    conn.close()
    print('tasks.sqlite 基础行:', len(base))
    # 3) 推导当前状态
    by_pmid = {}
    for run, r in run_rows:
        by_pmid.setdefault(r['pmid'], []).append((run, r))
    merged_pdf = {}   # pmid -> 合并目录实际文件
    for fn in os.listdir(MERGED_DIR):
        if fn.startswith('PMID_') and fn.endswith('.pdf'):
            merged_pdf[int(fn[5:-4])] = os.path.join(MERGED_DIR, fn)

    def derive(r):
        pmid = r['pmid']
        if r['year'] < 2000:
            return 'archived', None, None, ARCHIVE_NOTE, 0, None, r.get('license') or 'unverified'
        runs = by_pmid.get(pmid, [])
        if not runs:
            return 'pending', None, None, None, 0, None, r.get('license') or 'unverified'
        attempts = sum((rr['attempts'] or 0) for _, rr in runs)
        upd = max((rr['updated_at'] or '' for _, rr in runs), default=None)
        for run, rr in runs:                       # 任一 done → done
            if rr['status'] == 'done':
                pdf = merged_pdf.get(pmid) or rr['pdf_path']
                return 'done', rr['route'], pdf, None, attempts, upd, rr.get('license') or r.get('license') or 'unverified'
        latest = max(runs, key=lambda x: x[1]['updated_at'] or '')   # 最近一次运行
        run, rr = latest
        return rr['status'], rr['route'], rr['pdf_path'], rr['last_error'], attempts, upd, rr.get('license') or r.get('license') or 'unverified'

    derived = []
    for r in base:
        st, route, pdf, err, att, upd, license = derive(r)
        r.update({'status': st, 'route': route, 'pdf_path': pdf,
                  'last_error': err, 'attempts': att, 'license': license,
                  'updated_at': upd})
        derived.append(r)

    dist = {}
    for r in derived:
        dist[r['status']] = dist.get(r['status'], 0) + 1
    print('推导状态分布:', dist)

    # 4) 写新库（临时文件 → 原子替换）
    tmp = old + '.merge.tmp'
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = sqlite3.connect(tmp)
    conn.execute('DROP TABLE IF EXISTS tasks')
    conn.execute('DROP TABLE IF EXISTS run_history')
    conn.execute(f'''CREATE TABLE tasks (
        pmid INTEGER PRIMARY KEY, doi TEXT, pmc TEXT, year INTEGER, title TEXT,
        title_norm TEXT, status TEXT, attempts INTEGER, route TEXT,
        last_error TEXT, pdf_path TEXT, license TEXT, updated_at TEXT)''')
    conn.execute('''CREATE TABLE run_history (
        run TEXT, pmid INTEGER, doi TEXT, pmc TEXT, year INTEGER, title TEXT,
        title_norm TEXT, status TEXT, attempts INTEGER, route TEXT,
        last_error TEXT, pdf_path TEXT, license TEXT, updated_at TEXT)''')
    conn.execute('CREATE INDEX idx_history_pmid ON run_history(pmid)')
    conn.executemany(
        f'INSERT INTO tasks ({",".join(TASKS_COLS)}) VALUES '
        f'({",".join("?" * len(TASKS_COLS))})',
        [tuple(r[c] for c in TASKS_COLS) for r in derived])
    conn.executemany(
        'INSERT INTO run_history VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        [tuple([run] + [r[c] for c in TASKS_COLS]) for run, r in run_rows])
    conn.commit()
    new_dist = dict(conn.execute('SELECT status, COUNT(*) FROM tasks GROUP BY status').fetchall())
    hist_n = conn.execute('SELECT COUNT(*) FROM run_history').fetchone()[0]
    conn.close()
    print(f'新库写入: tasks={len(derived)}（状态 {new_dist}）, run_history={hist_n}')

    # 5) 导出 run_history.csv（其他形式）
    os.makedirs(os.path.join(ROOT, 'reports'), exist_ok=True)
    hist_csv = os.path.join(ROOT, 'reports', 'run_history.csv')
    with open(hist_csv, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['Run'] + TASKS_COLS)
        for run, r in run_rows:
            w.writerow([run] + [r[c] for c in TASKS_COLS])
    print(f'导出: {hist_csv}（{len(run_rows)} 行）')

    # 6) 校验通过 → 原子替换 + 删除批次库
    expect = len(base)
    ok = (len(derived) == expect and hist_n == len(run_rows)
          and new_dist.get('archived') == 1582)
    if not ok:
        print('!! 校验未通过，中止替换；临时文件保留:', tmp)
        return
    os.replace(tmp, old)
    print(f'tasks.sqlite 已替换（{len(derived)} 行）')

    removed = []
    for run, fname in BATCHES:
        for suf in ('', '-wal', '-shm'):
            p = os.path.join(ROOT, fname + suf)
            if os.path.exists(p):
                os.remove(p)
                removed.append(os.path.basename(p))
    print('已删除批次库文件:', removed)

    # 7) 抽查
    conn = sqlite3.connect(old)
    for pmid in (32355671, 946107, 11111101):
        hit = conn.execute('SELECT pmid,status,route,last_error,pdf_path FROM tasks WHERE pmid=?',
                           (pmid,)).fetchall()
        print('抽查 tasks', pmid, hit)
    conn.close()


if __name__ == '__main__':
    main()
