# -*- coding: utf-8 -*-
"""阶段 5：汇总报告 + 更新索引信息.csv 下载状态。

用法:
    python 05_report.py [--db tasks.sqlite] [--pdf-dir pdfs_merged] [--out reports] [--csv 索引信息.csv]
输出:
    reports/download_report.csv   逐篇明细（含状态/通道/错误）
    reports/summary.txt           汇总统计
    索引信息.csv 的 Status / PDFPath / Note 列同步更新（仅涉及 db 中的 PMID）
"""

import argparse
import csv
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, connect_db, load_config, validate_pdf


def main():
    ap = argparse.ArgumentParser(description='生成下载报告并回写索引信息.csv')
    ap.add_argument('--db', default=os.path.join(ROOT, 'tasks.sqlite'))
    ap.add_argument('--pdf-dir', default=os.path.join(ROOT, 'pdfs_merged'))
    ap.add_argument('--out', default=os.path.join(ROOT, 'reports'))
    ap.add_argument('--csv', default=os.path.join(ROOT, '索引信息.csv'))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    cfg = load_config()
    min_size = int(cfg.get('min_pdf_size', 30720))

    conn = connect_db(args.db)
    rows = conn.execute(
        'SELECT pmid, doi, pmc, year, title, status, route, attempts, last_error, pdf_path, license '
        'FROM tasks').fetchall()
    conn.close()
    total = len(rows)

    by_status = {}
    reasons = {}
    for r in rows:
        by_status[r[5]] = by_status.get(r[5], 0) + 1
        if r[5] != 'done':
            err = (r[8] or '').strip()
            key = err.split(':')[0][:40] if err else '(无错误信息)'
            reasons[key] = reasons.get(key, 0) + 1

    # 落盘 PDF 校验
    done_pdfs, valid_pdfs = 0, 0
    invalid = []
    for r in rows:
        if r[5] != 'done':
            continue
        done_pdfs += 1
        p = r[9] or os.path.join(args.pdf_dir, f'PMID_{r[0]}.pdf')
        if os.path.exists(p):
            with open(p, 'rb') as f:
                if validate_pdf(f.read(), min_size):
                    valid_pdfs += 1
                    continue
        invalid.append(r[0])

    # 逐篇明细
    report_csv = os.path.join(args.out, 'download_report.csv')
    with open(report_csv, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['PMID', 'Year', 'DOI', 'PMC', 'Title', 'Status', 'Route',
                    'Attempts', 'Error', 'PDFPath', 'License'])
        for r in rows:
            w.writerow([r[0], r[3], r[1] or '', r[2] or '', r[4], r[5],
                        r[6] or '', r[7], r[8] or '', r[9] or '', r[10] or 'unverified'])

    # 汇总
    summary = [
        '=== 文献爬取下载报告 ===',
        f'生成时间: {__import__("time").strftime("%Y-%m-%d %H:%M:%S")}',
        f'任务总数: {total}',
        f'状态分布: {by_status}',
        f'成功率: {by_status.get("done", 0)}/{total} = '
        f'{by_status.get("done", 0) / total * 100:.1f}%' if total else '成功率: 0/0',
        f'PDF 落盘校验: {valid_pdfs}/{done_pdfs} 有效',
        '',
        '失败原因分布:',
    ]
    for k, v in sorted(reasons.items(), key=lambda x: -x[1]):
        summary.append(f'  {k}: {v}')
    if invalid:
        summary.append(f'校验未通过 PMID: {invalid[:20]}')
    summary_path = os.path.join(args.out, 'summary.txt')
    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(summary) + '\n')

    # 回写索引信息.csv
    if os.path.exists(args.csv):
        status_map = {'done': 'done', 'not_found': 'not_found', 'failed': 'failed'}
        with open(args.csv, encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames
            lines = list(reader)
        idx = {int(r['PMID']): r for r in lines}
        for r in rows:
            target = idx.get(r[0])
            if target is None:
                continue
            target['Status'] = status_map.get(r[5], r[5])
            target['PDFPath'] = r[9] or ''
            if 'License' not in header:
                header.append('License')
                for line in lines:
                    line['License'] = line.get('License') or 'unverified'
            target['License'] = r[10] or 'unverified'
            note = (r[8] or '') if r[5] != 'done' else (f'通道: {r[6]}' if r[6] else '')
            target['Note'] = note
        with open(args.csv, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            w.writerows(lines)
        print(f'已回写 {args.csv}')

    license_csv = os.path.join(args.out, 'source_licenses.csv')
    with open(license_csv, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['PMID', 'SourceRoute', 'License', 'LicenseStatus'])
        for r in rows:
            license_value = r[10] or 'unverified'
            status = 'recorded' if license_value != 'unverified' else 'unverified'
            w.writerow([r[0], r[6] or '', license_value, status])
    print(f'许可证来源清单: {license_csv}')

    print('\n'.join(summary))
    print(f'明细: {report_csv}')
    print(f'汇总: {summary_path}')


if __name__ == '__main__':
    main()
