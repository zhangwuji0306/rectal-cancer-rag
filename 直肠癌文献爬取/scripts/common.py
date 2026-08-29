# -*- coding: utf-8 -*-
"""公共模块：配置加载、日志、SQLite 连接、PDF 校验、挑战检测、标题归一化与相似度。"""

import difflib
import json
import logging
import os
import re
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config():
    """加载项目根目录 config.json。"""
    with open(os.path.join(ROOT, 'config.json'), encoding='utf-8') as f:
        return json.load(f)


def setup_logger(name, log_file=None):
    """控制台 + 文件双通道日志（UTF-8）。"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        fh = logging.FileHandler(log_file, encoding='utf-8')
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger


def connect_db(db_path):
    """打开（必要时创建）任务队列 SQLite 库。"""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('''CREATE TABLE IF NOT EXISTS tasks (
        pmid INTEGER PRIMARY KEY,
        doi TEXT,
        pmc TEXT,
        year INTEGER,
        title TEXT,
        title_norm TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        route TEXT,
        last_error TEXT,
        pdf_path TEXT,
        updated_at TEXT
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)')
    conn.commit()
    return conn


def validate_pdf(data, min_size=30720):
    """校验 PDF 字节流：%PDF 头 + 最小体积。"""
    return bool(data) and data[:5] == b'%PDF-' and len(data) >= min_size


CHALLENGE_MARKERS = (
    'checking your browser',
    'just a moment',
    'attention required',
    'recaptcha',
    'cf-challenge',
    'ddos-guard',
    'access denied',
    'verify you are human',
)

# Altcha 工作量证明挑战（sci-hub 新版 "你是机器人吗" 页，status 200 返回）
ALTCHA_MARKERS = ('altcha-widget', '你是机器人吗', 'isrobot')


def is_challenge(status, headers, body_text):
    """判断响应是否为反爬挑战页（DDoS-Guard / Cloudflare / reCAPTCHA / Altcha）。"""
    server = (headers.get('Server') or '').lower()
    if 'ddos-guard' in server and status in (403, 429):
        return True
    low = (body_text or '').lower()
    # 正常文章页即使嵌了 altcha-widget（页面模板常驻）也不判挑战：
    # 挑战页（~7KB）绝不含 PDF 链接，文章页（~26KB+）必有
    if ('citation_pdf_url' in low or
            ('<embed' in low and '.pdf' in low) or
            ('location.href' in low and '.pdf' in low)):
        return False
    if status in (403, 429, 503):
        return any(m in low for m in CHALLENGE_MARKERS)
    # 部分挑战以 200 返回（如 SHA-256 PoW 挑战页、Altcha "你是机器人吗" 页）
    if 'checking your browser' in low or 'just a moment' in low:
        return True
    if any(m in low for m in ALTCHA_MARKERS):
        return True
    return False


def norm_text(s):
    """标题归一化：小写、去标点、压缩空白（用于检索键与相似度比较）。"""
    s = (s or '').lower()
    s = re.sub(r'["\'«»()\[\]{}.,;:!?\-_/\\|*+&^%$#@~`=<>]', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def similarity(a, b):
    """归一化后的字符串相似度（difflib ratio，0~1）。"""
    return difflib.SequenceMatcher(None, norm_text(a), norm_text(b)).ratio()


def abs_url(base, u):
    """把页面内相对/协议相对链接解析为绝对 URL。"""
    u = u.strip()
    if u.startswith('//'):
        return 'https:' + u
    if u.startswith('/'):
        return base.rstrip('/') + u
    if u.startswith(('http://', 'https://')):
        return u
    return base.rstrip('/') + '/' + u


def now_str():
    return time.strftime('%Y-%m-%d %H:%M:%S')
