# -*- coding: utf-8 -*-
"""阶段 10：封存 2000 年以前文献（暂不爬取）。

- 扫描 索引信息.csv 中 Year < 2000 的行 → 写 reports/archive_pre2000.csv 封存清单；
- 回写 索引信息.csv：Status='archived'、Note='2000年前，封存不爬取'（其余列不动）；
- 更新 tasks.sqlite：status='archived'（last_error 同步写入，防止日后 05_report
  回写时清空该行的封存说明）。

幂等：重复运行只重写同样的值。
解除封存：
    UPDATE tasks SET status='pending' WHERE year < 2000          （tasks.sqlite）
    索引信息.csv 相应行 Status 改回 pending、Note 清空即可。
"""

import csv
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT

ARCHIVE_NOTE = '2000年前，封存不爬取'
MIN_YEAR = 2000


def main():
    csv_path = os.path.join(ROOT, '索引信息.csv')
    manifest = os.path.join(ROOT, 'reports', 'archive_pre2000.csv')

    with open(csv_path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames
        rows = list(reader)

    pre = [r for r in rows if r['Year'].strip().isdigit() and int(r['Year']) < MIN_YEAR]
    pre_pmids = {r['PMID'] for r in pre}
    print(f'索引信息.csv 共 {len(rows)} 行；2000 年以前 {len(pre)} 行')

    # 1) 封存清单
    os.makedirs(os.path.join(ROOT, 'reports'), exist_ok=True)
    with open(manifest, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['PMID', 'Year', 'Title', 'Journal', 'DOI', 'PMC'])
        for r in sorted(pre, key=lambda x: int(x['Year'])):
            w.writerow([r['PMID'], r['Year'], r['Title'],
                        r['Journal'], r['DOI'] or '', r['PMC'] or ''])
    print(f'封存清单: {manifest}（{len(pre)} 行，按年份升序）')

    # 2) 回写索引信息.csv
    changed = 0
    for r in rows:
        if r['PMID'] in pre_pmids and (r['Status'] != 'archived' or r['Note'] != ARCHIVE_NOTE):
            changed += 1
        if r['PMID'] in pre_pmids:
            r['Status'] = 'archived'
            r['Note'] = ARCHIVE_NOTE
    if changed:
        with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            w.writerows(rows)
        print(f'索引信息.csv 已回写：{changed} 行标记为 archived')
    else:
        print('索引信息.csv 无需改动（封存标记已存在）')

    # 3) 更新 tasks.sqlite
    db = os.path.join(ROOT, 'tasks.sqlite')
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    n = cur.execute('SELECT COUNT(*) FROM tasks WHERE year < ?', (MIN_YEAR,)).fetchone()[0]
    cur.execute('UPDATE tasks SET status=?, last_error=? WHERE year < ?',
                ('archived', ARCHIVE_NOTE, MIN_YEAR))
    conn.commit()
    st = dict(cur.execute('SELECT status, COUNT(*) FROM tasks GROUP BY status').fetchall())
    conn.close()
    print(f'tasks.sqlite：{n} 行(year<2000) → archived；全库状态分布: {st}')

    # 4) 复核
    with open(csv_path, encoding='utf-8-sig', newline='') as f:
        back = list(csv.DictReader(f))
    arch = sum(1 for r in back if r['Status'] == 'archived')
    print(f'复核：索引信息.csv archived 行数 = {arch}（预期 {len(pre)}）')


if __name__ == '__main__':
    main()
