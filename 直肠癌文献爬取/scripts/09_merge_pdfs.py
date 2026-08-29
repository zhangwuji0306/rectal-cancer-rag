# -*- coding: utf-8 -*-
"""阶段 9：合并全部已下载 PDF → pdfs_merged\，并生成语料元数据报告。

来源目录（优先级从高到低，内容不同的重复文件取优先级高者）：
    pdfs_batch810 > pdfs_sample_1000 > pdfs_sample > pdfs_test3

流程：
    1) 收集各来源目录的 PMID_<pmid>.pdf（文件名不符合规范 → 中止，不删源目录）；
    2) 逐 PMID 选定来源副本，复制到 pdfs_merged\（幂等：目标已存在且哈希一致则跳过）；
    3) 校验每个复制文件：SHA-256 与源一致 + %PDF 头 + 大小 >= min_pdf_size；
    4) 全部校验通过 → 删除原 4 个来源目录（用户已确认"校验后删除"）；
       任一失败 → 中止删除，原目录保留；
    5) 生成 pdfs_merged\语料元数据.csv（按 PMID 联表 索引信息.csv）+ summary.txt；
    6) 更新 索引信息.csv 的 PDFPath 与各 tasks*.sqlite 的 pdf_path → pdfs_merged。
"""

import csv
import hashlib
import os
import re
import shutil
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, load_config

SOURCES = ['pdfs_batch810', 'pdfs_sample_1000', 'pdfs_sample', 'pdfs_test3']
DEST = 'pdfs_merged'
NAME_RE = re.compile(r'^PMID_(\d+)\.pdf$')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def is_valid_pdf(path, min_size):
    try:
        if os.path.getsize(path) < min_size:
            return False
        with open(path, 'rb') as f:
            return f.read(5) == b'%PDF-'
    except OSError:
        return False


def main():
    cfg = load_config()
    min_size = int(cfg.get('min_pdf_size', 30720))
    dest_dir = os.path.join(ROOT, DEST)
    os.makedirs(dest_dir, exist_ok=True)

    # 1) 收集来源文件（目录内有不符合规范的文件 → 直接中止）
    files = {}          # pmid -> [(source, path), ...]
    per_source = {}     # source -> set(pmid)
    for src in SOURCES:
        d = os.path.join(ROOT, src)
        if not os.path.isdir(d):
            print(f'!! 来源目录不存在: {d}（跳过删除该目录）')
            continue
        s = set()
        for fn in sorted(os.listdir(d)):
            m = NAME_RE.match(fn)
            if not m:
                print(f'!! {src} 含非规范文件: {fn} —— 中止本次合并')
                return
            pmid = int(m.group(1))
            files.setdefault(pmid, []).append((src, os.path.join(d, fn)))
            s.add(pmid)
        per_source[src] = s
        print(f'来源 {src}: {len(s)} 篇')

    union = sorted(files)
    print(f'合并目标: 唯一 PMID {len(union)} 篇')

    # 2) 逐 PMID 选来源 + 复制 + 3) 校验
    winner = {}         # pmid -> source
    dup_notes = {}      # pmid -> '原批次: a/b'
    verified, failed = [], []
    for pmid in union:
        cands = files[pmid]
        cands.sort(key=lambda c: SOURCES.index(c[0]))
        src, path = cands[0]
        winner[pmid] = src
        if len(cands) > 1:
            others = '/'.join(c[0] for c in cands[1:])
            dup_notes[pmid] = f'重复去重（原批次: {others}）'
        dst = os.path.join(dest_dir, f'PMID_{pmid}.pdf')
        try:
            if os.path.exists(dst):
                if sha256(dst) == sha256(path):
                    pass  # 已合并且一致，跳过复制
                else:
                    shutil.copy2(path, dst)
            else:
                shutil.copy2(path, dst)
            if sha256(dst) != sha256(path):
                raise RuntimeError('SHA-256 与源不一致')
            if not is_valid_pdf(dst, min_size):
                raise RuntimeError(f'PDF 校验失败（<{min_size}B 或非 %PDF）')
            verified.append(pmid)
        except Exception as e:
            failed.append((pmid, src, str(e)))
            print(f'!! 失败 {pmid} ({src}): {e}')

    if failed:
        print(f'共 {len(failed)} 篇校验失败，中止删除来源目录，保留现场:')
        for pmid, src, err in failed:
            print(f'   {pmid} ({src}): {err}')
        return

    total_bytes = sum(os.path.getsize(os.path.join(dest_dir, f'PMID_{p}.pdf')) for p in union)
    print(f'校验通过: {len(verified)}/{len(union)}，共 {total_bytes / 1048576:.1f} MB')

    # 4) 删除来源目录（先确认目录内文件全部为已合并的规范文件）
    for src, s in per_source.items():
        d = os.path.join(ROOT, src)
        left = os.listdir(d)
        ok = all(NAME_RE.match(fn) and int(NAME_RE.match(fn).group(1)) in union for fn in left)
        if not ok:
            print(f'!! 目录 {src} 存在未合并文件，保留不删: {left}')
            continue
        shutil.rmtree(d)
        print(f'已删除来源目录: {src}')

    # 5) 语料元数据报告（联表 索引信息.csv）
    csv_path = os.path.join(ROOT, '索引信息.csv')
    with open(csv_path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        idx_header = reader.fieldnames
        idx_rows = list(reader)
    meta = {r['PMID']: r for r in idx_rows}

    report_path = os.path.join(dest_dir, '语料元数据.csv')
    with open(report_path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['PMID', 'Year', 'Title', 'Authors', 'FirstAuthor', 'Journal', 'ISSN',
                    'Volume', 'Issue', 'Pages', 'DOI', 'PMC', 'Language', 'PubType',
                    '文件名', '大小KB', '来源批次', '校验', '备注'])
        for pmid in union:
            r = meta.get(str(pmid))
            if r is None:
                r = {}
            w.writerow([
                pmid,
                r.get('Year', ''),
                r.get('Title', ''),
                r.get('Authors', ''),
                r.get('FirstAuthor', ''),
                r.get('Journal', ''),
                r.get('ISSN', ''),
                r.get('Volume', ''),
                r.get('Issue', ''),
                r.get('Pages', ''),
                r.get('DOI', ''),
                r.get('PMC', ''),
                r.get('Language', ''),
                r.get('PubType', ''),
                f'PMID_{pmid}.pdf',
                round(os.path.getsize(os.path.join(dest_dir, f'PMID_{pmid}.pdf')) / 1024, 1),
                winner[pmid],
                '有效',
                '测试文件，无元数据' if r == {} else dup_notes.get(pmid, ''),
            ])
    print(f'语料元数据: {report_path}（{len(union)} 行）')

    summary_path = os.path.join(dest_dir, 'summary.txt')
    test_n = sum(1 for p in union if str(p) not in meta)
    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join([
            '=== PDF 语料合并报告 ===',
            f'生成时间: {time.strftime("%Y-%m-%d %H:%M:%S")}',
            f'合并目录: {dest_dir}',
            f'唯一 PMID 总数: {len(union)}',
            f'文件总体积: {total_bytes / 1048576:.1f} MB',
            f'来源分布: ' + '; '.join(f'{src}={len(s)}' for src, s in per_source.items()),
            f'重复去重: {len(dup_notes)} 篇（内容哈希一致）',
            f'测试文件（无元数据）: {test_n} 篇',
            f'PDF 校验: {len(verified)}/{len(union)} 有效',
            '',
            '明细见同目录 语料元数据.csv；索引信息.csv 的 PDFPath 已同步指向本目录。',
        ]) + '\n')
    print(f'合并汇总: {summary_path}')

    # 6a) 更新索引信息.csv PDFPath
    merged_set = {str(p) for p in union}
    updated = 0
    for r in idx_rows:
        if r['PMID'] in merged_set:
            new_p = os.path.join(dest_dir, f'PMID_{r["PMID"]}.pdf')
            if r.get('PDFPath') != new_p:
                r['PDFPath'] = new_p
                updated += 1
    if updated:
        with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=idx_header)
            w.writeheader()
            w.writerows(idx_rows)
        print(f'索引信息.csv PDFPath 已更新 {updated} 行 → {DEST}\\')
    else:
        print('索引信息.csv PDFPath 无需更新')

    # 6b) 更新各任务库 done 行的 pdf_path
    dbs = ['tasks_sample_50.sqlite', 'tasks_sample.sqlite', 'tasks_template.sqlite',
           'tasks_batch810.sqlite', 'tasks_test3.sqlite']
    for db in dbs:
        p = os.path.join(ROOT, db)
        if not os.path.exists(p):
            continue
        conn = sqlite3.connect(p)
        cur = conn.cursor()
        done = cur.execute('SELECT pmid FROM tasks WHERE status=?', ('done',)).fetchall()
        pairs = [(os.path.join(dest_dir, f'PMID_{pmid}.pdf'), pmid) for (pmid,) in done]
        cur.executemany('UPDATE tasks SET pdf_path=? WHERE pmid=?', pairs)
        conn.commit()
        n = cur.execute("SELECT COUNT(*) FROM tasks WHERE status='done' "
                        "AND pdf_path LIKE ?", (f'%\\{DEST}\\%',)).fetchone()[0]
        conn.close()
        print(f'{db}: done 行 pdf_path 已同步（{n}/{len(done)}）')


if __name__ == '__main__':
    main()
