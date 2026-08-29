# -*- coding: utf-8 -*-
"""阶段 3：文献 PDF 下载引擎（核心）。

通道链（按优先级，OA 优先）:
    1. Europe PMC OA 直连（有 PMC 字段一律最优先；无 PMC 且无 DOI 时按 PMID 搜索；
       官方通道，无反爬检测）
    2. sci-hub DOI 检索（原 DOI）
    3. Crossref 标题反查 DOI → sci-hub（无 DOI 文献，相似度 >= 阈值才采信）
    4. sci-hub 标题检索（最后兜底，文章页标题相似度校验防误配）

注：NCBI PMC 直连（pmc.ncbi.nlm.nih.gov/.../pdf/main.pdf）已弃用——2026-08 起对
全新会话返回 JS "Preparing to download" 中转页，requests 无法直取；由 Europe PMC
（europepmc.org/articles/<PMCID>?pdf=render）替代，同为官方 OA 通道且无 bot 检测。

反爬处理:
    - DDoS-Guard 被动模式: GET + Chrome UA 自动下发 __ddg* cookies，无需解 JS 挑战
    - 主动挑战（403/挑战页）: 镜像临时标记死亡 + 计数，达阈值后自动调用
      selenium_fallback.harvest_cookies 用本机 Chrome 过挑战收割 cookies
    - 每镜像限速（request_interval_min/max 随机）+ 周期防检测暂停：连续镜像访问
      200-300 次（每轮随机）后全池暂停 2-3 分钟（每轮随机），规避自动检测
    - cookie 定时刷新

用法:
    python 03_downloader.py [--db tasks.sqlite] [--pdf-dir pdfs_merged] [--workers 4]
                            [--min-year 2000] [--max-year N] [--limit N]
"""

import argparse
import html
import os
import random
import re
import sys
import threading
import time
import urllib.parse

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (ROOT, abs_url, connect_db, is_challenge, load_config, now_str,
                    setup_logger, similarity, validate_pdf)
from altcha_solver import solve_altcha

NOT_FOUND_MARKERS = ('article not found', 'ничего не найдено', 'no paper found',
                     'не найдена', 'not found', 'нет статьи')


class DownloadError(Exception):
    """下载链路基类异常。"""


class NetworkError(DownloadError):
    """网络层错误。"""


class NotfoundError(DownloadError):
    """文献未收录（404/空响应/无 PDF 链接）。"""


class ChallengeError(DownloadError):
    """反爬挑战拦截。"""

    def __init__(self, mirror):
        self.mirror = mirror
        super().__init__(f'challenge: {mirror}')


class PdfInvalidError(DownloadError):
    """下载到的文件非有效 PDF。"""


class TitleMismatchError(DownloadError):
    """文章页标题与目标不符（防误配）。"""


class MirrorPool:
    """镜像池：轮换 + 健康状态 + 每镜像限速 + 周期防检测暂停。"""

    def __init__(self, mirrors, interval_min, interval_max,
                 pause_after_min=200, pause_after_max=300,
                 pause_duration_min=120, pause_duration_max=180,
                 logger=None):
        self.mirrors = list(mirrors)
        # 每次请求间隔在 [min, max] 内均匀随机（降低请求速率 + 时序随机化，减少反爬触发）
        self.interval_min = min(interval_min, interval_max)
        self.interval_max = max(interval_min, interval_max)
        # 周期防检测暂停：每连续访问 pause_after（200-300 随机）次后，全池暂停
        # pause_duration（120-180 随机）秒；每轮阈值与时长均由随机数决定
        self.pause_after_min = int(pause_after_min)
        self.pause_after_max = int(pause_after_max)
        self.pause_duration_min = float(pause_duration_min)
        self.pause_duration_max = float(pause_duration_max)
        self.log = logger
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.last_use = {m: 0.0 for m in self.mirrors}
        self.dead_until = {m: 0.0 for m in self.mirrors}
        self.idx = 0
        self.req_count = 0
        self.pause_until = 0.0
        self.pause_after = random.randint(self.pause_after_min, self.pause_after_max)

    def _pause_gate(self):
        """计数 + 暂停门：每次镜像访问前调用。到阈值时全池暂停随机时长。

        返回触发暂停的提示字符串（仅触发者收到），否则 None。
        暂停期间所有 worker 阻塞在条件变量上，保证窗口内零镜像请求。
        """
        msg = None
        with self.cond:
            self.req_count += 1
            if self.req_count >= self.pause_after:
                dur = random.uniform(self.pause_duration_min, self.pause_duration_max)
                self.pause_until = time.time() + dur
                self.req_count = 0
                self.pause_after = random.randint(self.pause_after_min, self.pause_after_max)
                msg = (f'连续镜像访问达阈值，全池暂停 {dur:.0f}s 规避检测'
                       f'（下一轮阈值: {self.pause_after} 次）')
            while self.pause_until > time.time():
                self.cond.wait(self.pause_until - time.time())
        return msg

    def acquire(self):
        """取一个可用镜像（内部等待该镜像的限速槽位 + 周期防检测暂停）。"""
        while True:
            msg = self._pause_gate()
            if msg:
                if self.log:
                    self.log.info('%s', msg)
                else:
                    print(msg, flush=True)
            with self.lock:
                now = time.time()
                cands = [m for m in self.mirrors if self.dead_until[m] < now]
                if not cands:
                    # 全部死亡则重置，宁可慢不可停
                    self.dead_until = {m: 0.0 for m in self.mirrors}
                    cands = self.mirrors
                m = cands[self.idx % len(cands)]
                self.idx += 1
                wait = random.uniform(self.interval_min, self.interval_max) - (now - self.last_use[m])
            if wait > 0:
                time.sleep(wait)
            with self.lock:
                if self.dead_until[m] > time.time():
                    continue  # 等待期间被标记死亡，重新选
                self.last_use[m] = time.time()
                return m

    def mark_dead(self, mirror, seconds=600):
        with self.lock:
            self.dead_until[mirror] = time.time() + seconds


class Crawler:
    def __init__(self, cfg, db_path, pdf_dir, min_year=None, max_year=None,
                 limit=None, workers=None):
        self.cfg = cfg
        self.db_path = db_path
        self.pdf_dir = pdf_dir
        os.makedirs(pdf_dir, exist_ok=True)
        self.min_year = min_year
        self.max_year = max_year
        self.limit = limit
        self.workers = workers or int(cfg.get('workers', 4))
        self.verify_ssl = bool(cfg.get('verify_ssl', True))
        if not self.verify_ssl:
            try:
                import urllib3
                urllib3.disable_warnings()
            except Exception:
                pass
        self.conn = connect_db(db_path)
        self.db_lock = threading.Lock()
        self.log = setup_logger('downloader', os.path.join(ROOT, 'logs', 'downloader.log'))
        self.pool = MirrorPool(
            cfg['mirrors'],
            float(cfg.get('request_interval_min', 3.0)),
            float(cfg.get('request_interval_max', 5.0)),
            pause_after_min=int(cfg.get('pause_after_min', 200)),
            pause_after_max=int(cfg.get('pause_after_max', 300)),
            pause_duration_min=float(cfg.get('pause_duration_min', 120)),
            pause_duration_max=float(cfg.get('pause_duration_max', 180)),
            logger=self.log)

        # cookies 与镜像会话
        self.cookie_lock = threading.Lock()
        self.global_cookies = {}
        self.mirror_sessions = {}  # mirror -> [session, lock, created_at]

        # 挑战兜底
        self.stop = threading.Event()
        self.challenge_lock = threading.Lock()
        self.challenge_hits = 0
        self.selenium_busy = threading.Lock()
        self.altcha_lock = threading.Lock()  # Altcha PoW 解算串行化

        # 统计
        self.stats_lock = threading.Lock()
        self.stats = {}
        self.done_count = 0

        # 外部服务限速（Europe PMC / Crossref）
        self.epmc_lock = threading.Lock()
        self.epmc_last = 0.0
        self.crossref_lock = threading.Lock()
        self.crossref_last = 0.0

    # ---------------------------------------------------------------- 会话层

    def session_for(self, mirror):
        """取镜像专用会话（惰性创建，带上全局 cookies）。"""
        with self.cookie_lock:
            entry = self.mirror_sessions.get(mirror)
            if entry is None:
                s = requests.Session()
                s.headers.update({
                    'User-Agent': self.cfg['user_agent'],
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                })
                s.verify = self.verify_ssl  # 镜像自签名证书
                if self.global_cookies:
                    s.cookies.update(self.global_cookies)
                entry = [s, threading.Lock(), time.time()]
                self.mirror_sessions[mirror] = entry
            return entry

    def refresh_cookies_once(self):
        """GET 镜像首页刷新 DDoS-Guard cookies；成功后重建所有会话。"""
        for m in self.cfg['mirrors']:
            try:
                s = requests.Session()
                s.headers.update({'User-Agent': self.cfg['user_agent']})
                r = s.get(m + '/', timeout=30, verify=self.verify_ssl)
                if r.status_code == 200 and not is_challenge(r.status_code, r.headers, r.text):
                    with self.cookie_lock:
                        self.global_cookies = s.cookies.get_dict()
                        self.mirror_sessions = {}
                    self.log.info('cookie 刷新成功: %s（%d 个）', m, len(self.global_cookies))
                    return True
            except Exception as e:
                self.log.warning('cookie 刷新失败 %s: %s', m, e)
        self.log.warning('全部镜像 cookie 刷新失败')
        return False

    def _cookie_loop(self):
        minutes = int(self.cfg.get('cookie_refresh_minutes', 30))
        while not self.stop.wait(minutes * 60):
            try:
                self.refresh_cookies_once()
            except Exception as e:
                self.log.warning('cookie 刷新异常: %s', e)

    # ---------------------------------------------------------------- 任务调度

    def _pick_task(self):
        with self.db_lock:
            stale_min = int(self.cfg.get('stale_requeue_minutes', 15))
            cutoff = time.strftime('%Y-%m-%d %H:%M:%S',
                                   time.localtime(time.time() - stale_min * 60))
            # 崩溃/中断残留的 downloading 任务回收
            self.conn.execute(
                "UPDATE tasks SET status='pending' WHERE status='downloading' AND updated_at < ?",
                (cutoff,))
            sql = ("SELECT pmid, doi, pmc, year, title, title_norm, attempts "
                   "FROM tasks WHERE status='pending'")
            params = []
            if self.min_year is not None:
                sql += ' AND year >= ?'
                params.append(self.min_year)
            if self.max_year is not None:
                sql += ' AND year <= ?'
                params.append(self.max_year)
            sql += ' ORDER BY pmid LIMIT 1'
            row = self.conn.execute(sql, params).fetchone()
            if row:
                self.conn.execute(
                    "UPDATE tasks SET status='downloading', updated_at=? WHERE pmid=?",
                    (now_str(), row[0]))
                self.conn.commit()
                return {'pmid': row[0], 'doi': row[1], 'pmc': row[2], 'year': row[3],
                        'title': row[4], 'title_norm': row[5], 'attempts': (row[6] or 0) + 1}
            self.conn.commit()
            return None

    def _worker(self, wid):
        while not self.stop.is_set():
            if self.limit and self.done_count >= self.limit:
                return
            task = self._pick_task()
            if task is None:
                return
            try:
                self.process(task)
            except Exception as e:
                self.log.exception('worker %d 处理 PMID %s 发生未捕获异常', wid, task['pmid'])
                self._finish(task, 'failed', error=f'异常: {e}')

    # ---------------------------------------------------------------- 主流程

    def process(self, task):
        """通道链执行（OA 优先）：europepmc → scihub:doi/crossref → scihub:title。"""
        routes = []
        if task['pmc']:
            # 只要有 PMC 字段，一律最优先走 Europe PMC（OA 官方通道，无反爬）
            routes.append(('europepmc:pdf', lambda: self.try_europepmc(task)))
        elif not task['doi']:
            # 无 PMC 且无 DOI：Europe PMC 按 PMID 搜索（无 DOI 文献的唯一 OA 机器通道）
            routes.append(('europepmc:pdf', lambda: self.try_europepmc(task)))
        if task['doi']:
            routes.append(('scihub:doi', lambda: self._scihub_by_key(task['doi'], task, False)))
        else:
            cdoi = None
            try:
                cdoi = self.crossref_lookup(task['title'], task['year'])
            except Exception as e:
                self.log.warning('crossref 查询异常 PMID %s: %s', task['pmid'], e)
            if cdoi:
                routes.append(('crossref:scihub', lambda: self._scihub_by_key(cdoi, task, True)))
        routes.append(('scihub:title', lambda: self._scihub_by_key(task['title_norm'], task, True)))

        last_exc = None
        last_error = None
        for name, fn in routes:
            try:
                path = fn()
                return self._finish(task, 'done', route=name, pdf_path=path)
            except ChallengeError as e:
                last_exc = e
                last_error = f'{name}: {e}'
                self._bump_challenge(e.mirror)
            except DownloadError as e:
                last_exc = e
                last_error = f'{name}: {e}'
            except Exception as e:
                last_exc = e
                last_error = f'{name}: 异常: {e}'
                self.log.exception('PMID %s 通道 %s 异常', task['pmid'], name)

        status = 'not_found' if isinstance(last_exc, NotfoundError) else 'failed'
        self._finish(task, status, route=routes[-1][0], error=last_error)

    # ---------------------------------------------------------------- 通道实现

    def try_europepmc(self, task):
        """Europe PMC OA 全文 PDF（官方通道，无反爬）。

        - 有 PMC 字段：直接下载 ?pdf=render（快速路径，不搜索）；
        - 无 PMC（无 DOI 文献）：搜索 EXT_ID:<pmid> 定位 pmcid，isOpenAccess='Y' 才下载；
        - 非 OA / 未收录 / 非 PDF → NotfoundError（静默回退下一通道）。
        """
        with self.epmc_lock:                      # 全通道 1s 礼貌限速
            wait = 1.0 - (time.time() - self.epmc_last)
            if wait > 0:
                time.sleep(wait)
            self.epmc_last = time.time()

        pmcid = task['pmc']
        if not pmcid:
            try:
                r = requests.get(
                    'https://www.ebi.ac.uk/europepmc/webservices/rest/search',
                    params={'query': f'EXT_ID:{task["pmid"]}', 'format': 'json',
                            'resultType': 'core'},
                    timeout=30, headers={'User-Agent': self.cfg['user_agent']})
                res = r.json().get('resultList', {}).get('result', []) if r.status_code == 200 else []
            except requests.RequestException as e:
                raise NetworkError(f'europepmc network: {e}')
            except ValueError:
                res = []
            if not res:
                raise NotfoundError('europepmc 未收录')
            hit = res[0]
            if hit.get('isOpenAccess') != 'Y':
                raise NotfoundError('europepmc 非 OA')
            pmcid = hit.get('pmcid')
            if not pmcid:
                raise NotfoundError('europepmc 无 pmcid')

        url = f'https://europepmc.org/articles/{pmcid}?pdf=render'
        try:
            r = requests.get(url, timeout=int(self.cfg.get('download_timeout', 90)),
                             headers={'User-Agent': self.cfg['user_agent'],
                                      'Accept': 'application/pdf,*/*'})
            data = r.content
        except requests.RequestException as e:
            raise NetworkError(f'europepmc pdf 网络错误: {e}')
        if validate_pdf(data, self.cfg['min_pdf_size']):
            return self._save_pdf(task['pmid'], data)
        if r.status_code == 500:
            raise NotfoundError('europepmc 非 OA（render 500）')
        raise NotfoundError(f'europepmc 非 PDF（http {r.status_code}, {len(data)}B）')

    def _scihub_by_key(self, key, task, verify_title):
        """按 DOI 或标题检索 sci-hub 并下载。verify_title=True 时校验文章页标题。"""
        last = None
        for _ in range(2):
            mirror = self.pool.acquire()
            if mirror is None:
                raise DownloadError('无可用镜像')
            entry = self.session_for(mirror)
            url = mirror.rstrip('/') + '/' + urllib.parse.quote(key, safe='/')
            try:
                with entry[1]:
                    r = entry[0].get(url, timeout=self.cfg['page_timeout'])
                status, headers, body = r.status_code, r.headers, r.text
            except requests.RequestException as e:
                last = NetworkError(f'network: {e}')
                continue

            if is_challenge(status, headers, body):
                # 先尝试纯 Python Altcha PoW 解算绕过（无需浏览器）
                if self._solve_challenge(entry, mirror, body):
                    continue  # 解算成功，重试本请求
                self.pool.mark_dead(mirror, int(self.cfg.get('mirror_dead_seconds', 600)))
                raise ChallengeError(mirror)
            if status == 404 or len(body) < 500:
                last = NotfoundError('404/空响应（文献未收录）')
                continue
            if status != 200:
                last = DownloadError(f'http {status}')
                continue

            pdf_url = self.extract_pdf(body)
            if pdf_url:
                if verify_title and not self._title_matches(body, task['title']):
                    last = TitleMismatchError('文章页标题与目标不符')
                    continue
                return self.download_pdf(abs_url(mirror, pdf_url), task['pmid'])

            # 标题搜索命中结果页：提取候选 DOI 逐个尝试
            cands = self.extract_candidates(body)
            if cands:
                for cdoi in cands[:3]:
                    try:
                        return self._scihub_by_key(cdoi, task, verify_title=True)
                    except (TitleMismatchError, NotfoundError, DownloadError) as e:
                        last = e
                        continue
                continue
            last = NotfoundError('页面无 PDF 链接')
        raise last or NotfoundError('sci-hub 无结果')

    def download_pdf(self, url, pmid):
        """下载 PDF 并校验；返回落盘路径。"""
        last = None
        for _ in range(2):
            mirror = self.pool.acquire()
            if mirror is None:
                raise DownloadError('无可用镜像')
            entry = self.session_for(mirror)
            try:
                with entry[1]:
                    r = entry[0].get(url, timeout=self.cfg['download_timeout'])
                data = r.content
            except requests.RequestException as e:
                last = NetworkError(f'pdf 下载网络错误: {e}')
                continue
            if validate_pdf(data, self.cfg['min_pdf_size']):
                return self._save_pdf(pmid, data)
            last = PdfInvalidError(f'pdf 无效或过小（{len(data)}B）')
        raise last or PdfInvalidError('pdf 下载失败')

    def crossref_lookup(self, title, year):
        """Crossref 标题反查 DOI；相似度 >= crossref_similarity 才采信。"""
        with self.crossref_lock:
            wait = 1.0 - (time.time() - self.crossref_last)
            if wait > 0:
                time.sleep(wait)
            self.crossref_last = time.time()
        url = 'https://api.crossref.org/works'
        params = {'query.bibliographic': title, 'rows': 8}
        ua = f'RectalCorpusBuilder/1.0 (mailto:{self.cfg.get("crossref_mailto", "")})'
        try:
            r = requests.get(url, params=params, timeout=30, headers={'User-Agent': ua})
            if r.status_code != 200:
                return None
            items = r.json().get('message', {}).get('items', [])
        except Exception as e:
            self.log.warning('crossref 请求失败: %s', e)
            return None
        best, best_score = None, 0.0
        for it in items:
            t = ' '.join(it.get('title') or [])
            sc = similarity(title, t)
            if sc > best_score:
                best, best_score = it, sc
        if best and best_score >= float(self.cfg.get('crossref_similarity', 0.85)):
            self.log.info('crossref 反查 DOI: PMID 命中 %.2f -> %s', best_score, best['DOI'])
            return best['DOI']
        return None

    # ---------------------------------------------------------------- 解析与校验

    @staticmethod
    def extract_pdf(body):
        """从 sci-hub 文章页提取 PDF 链接。"""
        m = re.search(r'citation_pdf_url"\s+content="([^"]+)"', body)
        if m:
            return html.unescape(m.group(1))
        m = re.search(r'<embed[^>]+src="([^"]+)"', body)
        if m:
            return html.unescape(m.group(1))
        m = re.search(r"location\.href\s*=\s*'([^']+)'", body)
        if m:
            return html.unescape(m.group(1))
        m = re.search(r'<a[^>]+id="pdf"[^>]*href="([^"]+)"', body)
        if m:
            return html.unescape(m.group(1))
        m = re.search(r'<iframe[^>]+src="([^"]+)"', body)
        if m and 'pdf' in m.group(1).lower():
            return html.unescape(m.group(1))
        return None

    @staticmethod
    def extract_candidates(body):
        """从标题搜索结果页提取候选 DOI。"""
        seen, out = set(), []
        for m in re.finditer(r'href="(/10\.\d{4,9}/[^"]+)"', body):
            u = html.unescape(m.group(1)).lstrip('/')
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out

    def _title_matches(self, body, title):
        """比较 sci-hub 文章页标题与 nbib 标题（防误配）。"""
        m = re.search(r'<title>([^<]*)</title>', body)
        t = m.group(1) if m else ''
        t = re.sub(r'^sci-hub\.\s*', '', t, flags=re.I)
        t = re.split(r'\s+/\s+', t)[0]
        if not t:
            return False
        return similarity(t, title) >= float(self.cfg.get('title_similarity_accept', 0.7))

    def _save_pdf(self, pmid, data):
        path = os.path.join(self.pdf_dir, f'PMID_{pmid}.pdf')
        tmp = path + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, path)
        return path

    # ---------------------------------------------------------------- 挑战兜底

    def _solve_challenge(self, entry, mirror, body):
        """纯 Python 解算 Altcha 挑战（SHA-256 PoW + POST verify）。

        解算串行化（共享会话，避免并发竞争）；成功返回 True 并放行会话。
        """
        with self.altcha_lock:
            self.log.warning('触发 Altcha PoW 解算: %s', mirror)
            try:
                with entry[1]:
                    ok = solve_altcha(entry[0], body, mirror,
                                      verify=self.verify_ssl, timeout=30)
            except Exception as e:
                self.log.warning('Altcha 解算异常: %s', e)
                return False
            self.log.warning('Altcha 解算结果: %s', ok)
            return ok

    def _bump_challenge(self, mirror):
        with self.challenge_lock:
            self.challenge_hits += 1
            threshold = int(self.cfg.get('challenge_threshold', 5))
            if self.challenge_hits >= threshold:
                self.challenge_hits = 0
                if not self.selenium_busy.locked():
                    threading.Thread(target=self._selenium_harvest, daemon=True).start()

    def _selenium_harvest(self):
        """Selenium + 本机 Chrome 过挑战，收割 cookies 供 requests 复用。"""
        if not self.selenium_busy.acquire(blocking=False):
            return
        try:
            self.log.warning('触发 Selenium 挑战兜底（Chrome 自动过验证）...')
            try:
                from selenium_fallback import harvest_cookies
            except Exception as e:
                self.log.warning('selenium_fallback 不可用: %s', e)
                return
            for m in self.cfg['mirrors']:
                try:
                    cookies = harvest_cookies(m, timeout=int(self.cfg.get('selenium_timeout', 90)))
                except Exception as e:
                    self.log.warning('selenium 收割 %s 失败: %s', m, e)
                    continue
                if cookies:
                    with self.cookie_lock:
                        self.global_cookies.update(cookies)
                        self.mirror_sessions = {}
                    self.log.warning('selenium 收割 %s cookies 成功（%d 个）', m, len(cookies))
                    return
            self.log.warning('selenium 兜底未取得任何 cookies')
        finally:
            self.selenium_busy.release()

    # ---------------------------------------------------------------- 收尾

    def _finish(self, task, status, route=None, error=None, pdf_path=None):
        with self.db_lock:
            self.conn.execute(
                'UPDATE tasks SET status=?, attempts=?, route=?, last_error=?, pdf_path=?, updated_at=? '
                'WHERE pmid=?',
                (status, task['attempts'], route, error, pdf_path, now_str(), task['pmid']))
            self.conn.commit()
        with self.stats_lock:
            self.stats[status] = self.stats.get(status, 0) + 1
            self.done_count += 1
            n = self.done_count
        msg = f'PMID {task["pmid"]} -> {status}'
        if route:
            msg += f' [{route}]'
        if error:
            msg += f' | {error}'
        self.log.info(msg)
        if n % 10 == 0:
            self.log.info('进度: %d 篇完成 %s', n, self.stats)

    def run(self):
        self.log.info('启动下载引擎: workers=%d, pdf_dir=%s, db=%s',
                      self.workers, self.pdf_dir, self.db_path)
        if not self.refresh_cookies_once():
            self.log.warning('初始 cookie 刷新失败，仍将尝试直接请求')
        threading.Thread(target=self._cookie_loop, daemon=True).start()
        threads = [threading.Thread(target=self._worker, args=(i,), daemon=True)
                   for i in range(self.workers)]
        for t in threads:
            t.start()
        try:
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            self.log.warning('收到中断信号，正在保存状态...')
            self.stop.set()
            for t in threads:
                t.join(timeout=10)
        self.log.info('引擎结束: %s', self.stats)
        with self.db_lock:
            dist = dict(self.conn.execute(
                'SELECT status, COUNT(*) FROM tasks GROUP BY status').fetchall())
        self.log.info('数据库状态分布: %s', dist)


def main():
    ap = argparse.ArgumentParser(description='sci-hub 文献 PDF 下载引擎')
    ap.add_argument('--db', default=os.path.join(ROOT, 'tasks.sqlite'))
    ap.add_argument('--pdf-dir', default=os.path.join(ROOT, 'pdfs_merged'))
    ap.add_argument('--workers', type=int, default=0)
    ap.add_argument('--min-year', type=int, default=None)
    ap.add_argument('--max-year', type=int, default=None)
    ap.add_argument('--limit', type=int, default=0, help='最多处理 N 篇（0=不限制）')
    args = ap.parse_args()

    cfg = load_config()
    crawler = Crawler(cfg, args.db, args.pdf_dir,
                      min_year=args.min_year, max_year=args.max_year,
                      limit=args.limit or None, workers=args.workers or None)
    crawler.run()


if __name__ == '__main__':
    main()
