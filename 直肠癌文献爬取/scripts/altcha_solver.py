# -*- coding: utf-8 -*-
"""Altcha 工作量证明解算器（sci-hub 文章页 "你是机器人吗" 挑战）。

协议（从 sci-hub.st/scripts/altcha.min.js 源码逆向确认）：
    1. 挑战页含 <altcha-widget challengeurl="/captcha/challenge/<id>">
    2. GET /captcha/challenge/<id> -> JSON {algorithm, challenge, maxNumber, salt, signature}
    3. 解算：服务端在 [0, maxNumber] 内随机选数 n，challenge = sha256(salt + str(n))
       客户端暴力遍历 n=0..maxNumber 找到 sha256(salt + str(n)) == challenge
    4. POST /captcha/solution/<id>，JSON body {"captcha": "<payload JSON 字符串>"}
       payload = {algorithm, challenge, number, salt, signature}
    5. 响应 {"success": true} 后会话即被放行，重试文章请求即可

独立测试: python altcha_solver.py [--doi 10.1200/JCO.2005.01.032]
"""

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib3
from urllib.parse import urljoin

import requests

CHALLENGEURL_RE = re.compile(r'challengeurl\s*=\s*["\']([^"\']+)["\']')


def _sha256_hex(s):
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def extract_challenge_id(page_html):
    """从挑战页提取 challenge ID；返回 (challenge_path, id) 或 (None, None)。"""
    m = CHALLENGEURL_RE.search(page_html or '')
    if not m:
        return None, None
    path = m.group(1)
    # 路径形如 /captcha/challenge/78614596；兼容无 ID 形式
    mid = re.search(r'/captcha/challenge/(\d+)', path)
    return path, (mid.group(1) if mid else None)


def solve_number(challenge, salt, max_number):
    """暴力遍历 n in [0, maxNumber]：sha256(salt + str(n)) == challenge。"""
    for n in range(max_number + 1):
        if _sha256_hex(salt + str(n)) == challenge:
            return n
    return None


def build_payload(challenge_data, number, took_ms):
    """按 widget 源码 Ae() 组装 payload：base64(JSON{...})，含 took 字段。

    widget: btoa(JSON.stringify({algorithm, challenge, number, salt, signature, test, took}))
    test 字段在生产挑战中为 undefined，JSON.stringify 会丢弃。
    """
    obj = {
        'algorithm': challenge_data.get('algorithm', 'SHA-256'),
        'challenge': challenge_data['challenge'],
        'number': number,
        'salt': challenge_data['salt'],
        'signature': challenge_data.get('signature', ''),
        'took': took_ms,
    }
    return base64.b64encode(json.dumps(obj).encode('utf-8')).decode('ascii')


def solve_altcha(session, page_html, base_url, verify=True, timeout=30):
    """解一个挑战页：取 challenge JSON -> 暴力解数 -> POST 验证。

    返回 True 表示验证通过（会话已被放行）；False 表示任一环节失败。
    """
    path, cid = extract_challenge_id(page_html)
    if not cid:
        return False
    chal_url = urljoin(base_url + '/', path)
    try:
        r = session.get(chal_url, timeout=timeout, verify=verify)
        if r.status_code != 200:
            return False
        data = r.json()
    except Exception:
        return False

    algorithm = data.get('algorithm', 'SHA-256')
    challenge = data.get('challenge', '')
    salt = data.get('salt', '')
    max_number = int(data.get('maxNumber', 200000))
    signature = data.get('signature', '')
    if not challenge or not salt:
        return False

    n = solve_number(challenge, salt, max_number)
    if n is None:
        return False

    sol_url = urljoin(base_url + '/', f'/captcha/solution/{cid}')
    payload = build_payload(data, n, took_ms=0)
    try:
        resp = session.post(
            sol_url,
            json={'captcha': payload},
            headers={'Content-Type': 'application/json'},
            timeout=timeout,
            verify=verify)
        if resp.status_code != 200:
            return False
        out = resp.json()
        return bool(out.get('success'))
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description='Altcha PoW 解算器独立测试')
    ap.add_argument('--mirror', default='https://sci-hub.st')
    ap.add_argument('--doi', default='10.1200/JCO.2005.01.032')
    ap.add_argument('--no-verify', action='store_true', help='跳过 TLS 证书校验（镜像自签名证书）')
    args = ap.parse_args()

    urllib3.disable_warnings()
    ua = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')
    s = requests.Session()
    s.headers.update({'User-Agent': ua,
                      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'})
    v = not args.no_verify
    r = s.get(args.mirror + '/', timeout=30, verify=v)
    print('首页:', r.status_code, len(r.text))

    url = args.mirror + '/' + args.doi
    r = s.get(url, timeout=45, verify=v)
    low = r.text.lower()
    print('文章页:', r.status_code, len(r.text), '| altcha:', 'altcha-widget' in low,
          '| pdf:', 'citation_pdf_url' in low)
    if 'altcha-widget' not in low:
        print('无挑战，无需解算')
        return

    ok = solve_altcha(s, r.text, args.mirror, verify=v)
    print('解算+验证:', ok)
    if not ok:
        print('解算失败')
        sys.exit(1)

    r2 = s.get(url, timeout=45, verify=v)
    low2 = r2.text.lower()
    print('解算后重试文章页:', r2.status_code, len(r2.text),
          '| altcha:', 'altcha-widget' in low2, '| pdf:', 'citation_pdf_url' in low2)
    if 'citation_pdf_url' in low2:
        print('SUCCESS: 挑战已绕过，文章页正常返回')
    else:
        print('注意: 验证通过但文章页仍未返回 PDF 链接')
        sys.exit(2)


if __name__ == '__main__':
    main()
