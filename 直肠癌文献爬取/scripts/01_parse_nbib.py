# -*- coding: utf-8 -*-
"""阶段 1：解析 PubMed .nbib（MEDLINE 格式）→ 索引信息.csv（全量元数据，UTF-8 with BOM）。

用法:
    python 01_parse_nbib.py [nbib路径] [csv路径]
默认读取项目根目录 pubmed-RectalNeop-set.nbib，写出 索引信息.csv。
"""

import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT

CSV_COLUMNS = ['PMID', 'Title', 'Abstract', 'Authors', 'FirstAuthor', 'Journal',
               'ISSN', 'Year', 'Volume', 'Issue', 'Pages', 'DOI', 'PMC', 'MeSH',
               'PubType', 'Language', 'Status', 'PDFPath', 'Note']

# 多值字段（每行一个值）与标量字段（续行拼接）——仅用于 CSV 输出选择；
# 解析时不做白名单限制（nbib 中任何 XXXX- 形式行均为字段行，续行以 6 空格开头）
MULTI_FIELDS = ('FAU', 'AD', 'MH', 'PT', 'IS')
SCALAR_FIELDS = ('PMID', 'TI', 'AB', 'DP', 'TA', 'JT', 'VI', 'IP', 'PG', 'LID', 'AID',
                 'PMC', 'LA', 'STAT', 'DCOM', 'LR', 'DEP', 'EDAT', 'OWN', 'PHST', 'CI')

FIELD_RE = re.compile(r'^([A-Z0-9]{2,4})\s*-?\s?(.*)$')


def parse_nbib(path):
    """逐行解析 nbib，返回记录列表（每条为 {'_f': {字段码: [值...]}}）。"""
    records = []
    cur = None
    with open(path, encoding='utf-8') as f:
        for raw in f:
            line = raw.rstrip('\n')
            m = FIELD_RE.match(line)
            if m:
                code, value = m.group(1), m.group(2).strip()
                if code == 'PMID':
                    cur = {'_f': {}}
                    records.append(cur)
                if cur is not None:
                    cur['_f'].setdefault(code, []).append(value)
            elif cur is not None and line.startswith(' ') and line.strip():
                # 续行：拼接到当前最后一个字段
                last_code = list(cur['_f'].keys())[-1]
                cur['_f'][last_code][-1] += ' ' + line.strip()
    return records


def clean(text):
    """折叠空白（含换行）。"""
    return re.sub(r'\s+', ' ', text).strip()


def get_doi(fields):
    """DOI 优先取 LID 字段的 [doi] 标签，其次 AID（老记录）。"""
    for code in ('LID', 'AID'):
        for v in fields.get(code, []):
            m = re.match(r'(\S+?)\s*\[doi\]', v)
            if m:
                return m.group(1).rstrip('.')
    return ''


def to_row(rec):
    """记录 → CSV 行（与 CSV_COLUMNS 对齐）。"""
    f = rec['_f']
    pmid = int(f['PMID'][0])
    title = clean(' '.join(f.get('TI', [])))
    abstract = clean(' '.join(f.get('AB', [])))
    authors = '; '.join(f.get('FAU', []))
    first_author = f.get('FAU', [''])[0].strip() if f.get('FAU') else ''
    journal = clean(' '.join(f.get('TA', [])))
    issn = ''
    if f.get('IS'):
        issn = re.sub(r'\s*\(.*\)$', '', f['IS'][0]).strip()
    dp = ' '.join(f.get('DP', []))
    ym = re.search(r'(\d{4})', dp)
    year = int(ym.group(1)) if ym else 0
    volume = clean(' '.join(f.get('VI', [])))
    issue = clean(' '.join(f.get('IP', [])))
    pages = clean(' '.join(f.get('PG', [])))
    doi = get_doi(f)
    pmc = clean(' '.join(f.get('PMC', [])))
    mesh = '; '.join(f.get('MH', []))
    pubtype = '; '.join(f.get('PT', []))
    language = clean(' '.join(f.get('LA', [])))
    return [pmid, title, abstract, authors, first_author, journal, issn, year,
            volume, issue, pages, doi, pmc, mesh, pubtype, language, 'pending', '', '']


def main():
    nbib = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'pubmed-RectalNeop-set.nbib')
    csv_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, '索引信息.csv')

    print(f'解析 {nbib} ...')
    records = parse_nbib(nbib)
    print(f'记录数: {len(records)}')

    rows = [to_row(r) for r in records]
    pre2022 = sum(1 for r in rows if 0 < r[7] < 2022)
    with_doi = sum(1 for r in rows if r[11])
    with_pmc = sum(1 for r in rows if r[12])
    no_year = sum(1 for r in rows if r[7] == 0)
    print(f'2022 以前: {pre2022} | 有 DOI: {with_doi} | 有 PMC: {with_pmc} | 无年份: {no_year}')

    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        w.writerows(rows)
    print(f'已写出 {csv_path}（{len(rows)} 行）')


if __name__ == '__main__':
    main()
