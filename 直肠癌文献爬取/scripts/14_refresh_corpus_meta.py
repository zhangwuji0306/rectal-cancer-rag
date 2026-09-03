# -*- coding: utf-8 -*-
"""阶段 14：刷新 pdfs_merged 语料元数据（安全增量版，可重复运行）。

从 pdfs_merged\\ 扫描全部 PMID_*.pdf，联表 索引信息.csv 生成 语料元数据.csv：
- 来源批次：旧行保留原值；新出现的 PMID 用 --batch-label 标记；
- 校验：%PDF 头 + 大小 >= min_pdf_size → '有效'；
- 同时重写 summary.txt。

用法:
    python 14_refresh_corpus_meta.py --batch-label pmc_b1-20260823
"""

import argparse
import csv
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, load_config, validate_pdf

DEST = os.path.join(ROOT, 'pdfs_merged')
NAME_RE = re.compile(r'^PMID_(\d+)\.pdf$')
META_COLS = ['PMID', 'Year', 'Title', 'Authors', 'FirstAuthor', 'Journal', 'ISSN',
             'Volume', 'Issue', 'Pages', 'DOI', 'PMC', 'License', 'Language', 'PubType',
             '文件名', '大小KB', '来源批次', '校验', '备注']


def main():
    ap = argparse.ArgumentParser(description='刷新语料元数据')
    ap.add_argument('--batch-label', required=True, help='新 PMID 的来源批次标记，如 pmc_b1-20260823')
    args = ap.parse_args()

    cfg = load_config()
    min_size = int(cfg.get('min_pdf_size', 30720))

    files = sorted(os.listdir(DEST))
    pdfs = {}
    bad = []
    for fn in files:
        m = NAME_RE.match(fn)
        if m:
            pdfs[int(m.group(1))] = os.path.join(DEST, fn)
        elif fn not in ('语料元数据.csv', 'summary.txt'):
            bad.append(fn)
    print(f'pdfs_merged: {len(pdfs)} 篇 PDF' + (f'，非规范文件: {bad}' if bad else ''))

    # 旧元数据（保留来源批次/备注）
    meta_path = os.path.join(DEST, '语料元数据.csv')
    old = {}
    if os.path.exists(meta_path):
        with open(meta_path, encoding='utf-8-sig', newline='') as f:
            for r in csv.DictReader(f):
                old[int(r['PMID'])] = r

    # 索引信息.csv 元数据
    with open(os.path.join(ROOT, '索引信息.csv'), encoding='utf-8-sig', newline='') as f:
        idx = {int(r['PMID']): r for r in csv.DictReader(f)}

    rows = []
    valid = 0
    for pmid in sorted(pdfs):
        p = pdfs[pmid]
        r = idx.get(pmid, {})
        o = old.get(pmid, {})
        with open(p, 'rb') as f:
            ok = validate_pdf(f.read(), min_size)
        if ok:
            valid += 1
        rows.append([
            pmid,
            r.get('Year', ''), r.get('Title', ''), r.get('Authors', ''),
            r.get('FirstAuthor', ''), r.get('Journal', ''), r.get('ISSN', ''),
            r.get('Volume', ''), r.get('Issue', ''), r.get('Pages', ''),
            r.get('DOI', ''), r.get('PMC', ''), r.get('License', 'unverified') or 'unverified',
            r.get('Language', ''), r.get('PubType', ''),
            f'PMID_{pmid}.pdf',
            round(os.path.getsize(p) / 1024, 1),
            o.get('来源批次', '') or args.batch_label,
            '有效' if ok else '无效',
            o.get('备注', ''),
        ])

    with open(meta_path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(META_COLS)
        w.writerows(rows)
    total_bytes = sum(os.path.getsize(p) for p in pdfs.values())

    with open(os.path.join(DEST, 'summary.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join([
            '=== PDF 语料合并报告 ===',
            f'生成时间: {time.strftime("%Y-%m-%d %H:%M:%S")}',
            f'合并目录: {DEST}',
            f'唯一 PMID 总数: {len(rows)}',
            f'文件总体积: {total_bytes / 1048576:.1f} MB',
            f'PDF 校验: {valid}/{len(rows)} 有效',
            f'新标记批次: {args.batch_label}',
            '明细见同目录 语料元数据.csv。',
        ]) + '\n')
    print(f'语料元数据: {meta_path}（{len(rows)} 行, 有效 {valid}/{len(rows)}, '
          f'{total_bytes / 1048576:.1f} MB）')


if __name__ == '__main__':
    main()
