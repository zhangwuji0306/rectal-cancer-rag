# -*- coding: utf-8 -*-
"""阶段 6：DOI 反查——为无 DOI 文献补充 DOI（多源反查链 + 严格防误配校验）。

反查链（按可信度排序）:
    1. esummary by PMID        —— 同记录反查（PubMed 官方记录，直接采信）
    2. esearch 标题 → esummary  —— PubMed 库内标题匹配（三重校验）
    3. OpenAlex title.search   —— 跨库收录（三重校验）
    4. Crossref bibliographic  —— 兜底（三重校验）

防误配三重校验（对非同记录来源强制）:
    - 标题相似度 >= 0.90 且 年份一致(±1)
    - 或 标题相似度 >= 0.85 且 作者姓氏有交集
    实测教训: 同题不同文（Robotic/Robot-assisted 变体 + 副标题差异）可拿到 0.86 相似度，
    仅靠标题阈值会误配，必须叠加年份/作者信号。

用法:
    python 06_doi_lookup.py [--db tasks_sample.sqlite] [--limit 0] [--reset]
    --reset: 反查到 DOI 的任务同时重置为 pending（供下载引擎重试）
"""

import argparse
import os
import re
import sqlite3
import sys
import time
import urllib.parse

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, load_config, similarity, now_str

EUTILS = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils'
OPENALEX = 'https://api.openalex.org/works'
CROSSREF = 'https://api.crossref.org/works'


def _fau_surname(fau):
    """'Pedraza, Roberto' -> 'pedraza'（nbib 作者格式）。"""
    return (fau.split(',')[0] or '').strip().lower()


def _name_surname(name):
    """'Roberto Pedraza' -> 'pedraza'（OpenAlex/Crossref 作者名格式）。"""
    parts = re.split(r'\s+', (name or '').strip())
    return (parts[-1] if parts else '').strip('.').lower()


def _main_title(t):
    """主标题（冒号前部分），用于变体查询。"""
    return re.split(r'\s*[:：]\s*', (t or '').strip())[0]


class Lookup:
    def __init__(self, cfg):
        self.cfg = cfg
        self.min_sc = float(cfg.get('crossref_similarity', 0.85))
        mailto = cfg.get('crossref_mailto', '')
        self.ua = {'User-Agent': f'RectalCorpusBuilder/1.0 (mailto:{mailto})'}
        self.pubmed_cache = {}

    # ------------------------------------------------------------ 校验

    def verify(self, cand_title, cand_year, cand_authors, title, year, authors):
        """三重校验：标题相似度 + 年份一致 + 作者姓氏交集。
        规则（对非同记录来源强制）:
            - 标题 >= 0.90 且 候选年份已知且与目标一致(±1)
            - 或 标题 >= 0.95 且 作者姓氏有交集
        教训: 年份缺失时不得放行（书章节标题巧合同名即为误配案例）。"""
        nbib_surnames = {_fau_surname(a) for a in (authors or '').split(';') if a.strip()}
        cand_surnames = {_name_surname(a) for a in (cand_authors or []) if a.strip()}
        sc = similarity(title, cand_title or '')
        if sc >= 0.90 and year and cand_year and abs(int(cand_year) - int(year)) <= 1:
            return True, f'标题{sc:.2f}+年份一致'
        if sc >= 0.95 and nbib_surnames & cand_surnames:
            return True, f'标题{sc:.2f}+作者匹配'
        return False, f'标题{sc:.2f}，年份/作者校验不足'

    # ------------------------------------------------------------ 各来源

    def esummary_meta(self, pmid):
        """esummary 元数据（标题/作者/年份/DOI），带缓存。"""
        if pmid in self.pubmed_cache:
            return self.pubmed_cache[pmid]
        meta = None
        try:
            r = requests.get(f'{EUTILS}/esummary.fcgi',
                             params={'db': 'pubmed', 'id': pmid, 'retmode': 'json'},
                             timeout=30, headers=self.ua)
            res = r.json().get('result', {}).get(str(pmid), {})
            doi = ''
            for a in res.get('articleids', []):
                if a.get('idtype') == 'doi' and a.get('value'):
                    doi = a['value'].strip()
                    break
            meta = {'title': res.get('title') or '', 'doi': doi,
                    'authors': [a.get('name', '') for a in res.get('authors', [])],
                    'year': (re.search(r'(\d{4})', res.get('pubdate', '') or '') or [None, None])[1]}
        except Exception:
            meta = {'title': '', 'doi': '', 'authors': [], 'year': None}
        self.pubmed_cache[pmid] = meta
        return meta

    def esearch_hits(self, title):
        """PubMed 标题检索 → 候选 PMID 列表。"""
        try:
            term = '"' + title.replace('"', ' ') + '"[Title]'
            r = requests.get(f'{EUTILS}/esearch.fcgi',
                             params={'db': 'pubmed', 'term': term, 'retmode': 'json', 'retmax': 5},
                             timeout=30, headers=self.ua)
            if r.status_code != 200:
                return []
            return r.json().get('esearchresult', {}).get('idlist', [])[:3]
        except Exception:
            return []

    def openalex_hits(self, title):
        """OpenAlex 标题检索 → [(doi, title, year, authors)]（多变体查询去重）。"""
        out = {}
        for q in {title, _main_title(title)}:
            try:
                r = requests.get(OPENALEX,
                                 params={'filter': 'title.search:' + q, 'per-page': 8,
                                         'select': 'doi,title,publication_year,authorships'},
                                 timeout=30, headers=self.ua)
                items = r.json().get('results', [])
            except Exception:
                items = []
            for it in items:
                doi = re.sub(r'^https?://doi\.org/', '', (it.get('doi') or '').strip()).strip()
                if not doi:
                    continue
                authors = [a.get('author', {}).get('display_name', '')
                           for a in it.get('authorships', [])]
                out[doi] = (doi, it.get('title') or '', it.get('publication_year'),
                            [a for a in authors if a])
            time.sleep(0.2)
        return list(out.values())

    def crossref_hits(self, title):
        """Crossref 标题反查 → [(doi, title, year, authors)]。"""
        try:
            r = requests.get(CROSSREF,
                             params={'query.bibliographic': title, 'rows': 8},
                             timeout=30, headers=self.ua)
            if r.status_code != 200:
                return []
            items = r.json().get('message', {}).get('items', [])
        except Exception:
            return []
        out = []
        for it in items:
            doi = it.get('DOI', '')
            ct = ' '.join(it.get('title') or [])
            dp = it.get('issued', {}).get('date-parts') or []
            cy = dp[0][0] if dp and dp[0] else None
            authors = [a.get('family', '') for a in it.get('author', [])]
            out.append((doi, ct, cy, authors))
        return out

    # ------------------------------------------------------------ 主流程

    def lookup(self, pmid, title, year, authors):
        """反查链；返回 (doi, source, 校验说明) 或 (None, None, 原因)。"""
        # 1) 同记录反查：直接采信
        meta = self.esummary_meta(pmid)
        if meta['doi']:
            return meta['doi'], 'esummary(PMID)', '同记录，直接采信'
        # 2) esearch 标题匹配（PubMed 库内）
        for pid in self.esearch_hits(title):
            m = self.esummary_meta(pid)
            if not m['doi']:
                continue
            ok, why = self.verify(m['title'], m['year'], m['authors'], title, year, authors)
            if ok:
                return m['doi'], f'esearch[Title](PMID {pid})', why
            time.sleep(0.3)
        # 3) OpenAlex
        for doi, ct, cy, ca in self.openalex_hits(title):
            ok, why = self.verify(ct, cy, ca, title, year, authors)
            if ok:
                return re.sub(r'^https?://doi\.org/', '', doi), 'OpenAlex', why
            time.sleep(0.3)
        # 4) Crossref
        for doi, ct, cy, ca in self.crossref_hits(title):
            ok, why = self.verify(ct, cy, ca, title, year, authors)
            if ok:
                return doi, 'Crossref', why
            time.sleep(0.3)
        return None, None, '未命中'


def main():
    ap = argparse.ArgumentParser(description='为无 DOI 文献多源反查 DOI（含防误配校验）')
    ap.add_argument('--db', default=os.path.join(ROOT, 'tasks.sqlite'))
    ap.add_argument('--limit', type=int, default=0, help='最多反查 N 篇（0=全部）')
    ap.add_argument('--reset', action='store_true',
                    help='反查到 DOI 的任务重置为 pending（供下载引擎重试）')
    ap.add_argument('--sleep', type=float, default=0.6, help='请求间隔（秒）')
    args = ap.parse_args()

    cfg = load_config()
    lu = Lookup(cfg)

    conn = sqlite3.connect(args.db)
    rows = conn.execute("SELECT pmid, title, year FROM tasks WHERE doi IS NULL OR doi=''").fetchall()
    if args.limit > 0:
        rows = rows[:args.limit]
    print(f'待反查: {len(rows)} 篇')

    hit, miss = 0, 0
    for i, (pmid, title, year) in enumerate(rows, 1):
        meta = lu.esummary_meta(pmid)
        authors = '; '.join(meta['authors'])
        doi, source, why = lu.lookup(pmid, title, year, authors)
        if doi:
            conn.execute('UPDATE tasks SET doi=?, updated_at=? WHERE pmid=?',
                         (doi, now_str(), pmid))
            if args.reset:
                conn.execute("UPDATE tasks SET status='pending' WHERE pmid=?", (pmid,))
            conn.commit()
            hit += 1
            print(f'[{i}/{len(rows)}] PMID {pmid} -> {doi}  源: {source} | {why}')
        else:
            miss += 1
            print(f'[{i}/{len(rows)}] PMID {pmid} -> {why}')
        time.sleep(args.sleep)
    conn.close()
    print(f'完成: 命中 {hit} / 未命中 {miss}')


if __name__ == '__main__':
    main()
