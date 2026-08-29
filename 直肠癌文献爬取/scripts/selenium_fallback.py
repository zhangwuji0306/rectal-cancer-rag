# -*- coding: utf-8 -*-
"""Selenium 挑战兜底层：用本机 Chrome 自动通过 DDoS-Guard / Cloudflare JS 挑战，收割 cookies。

被调用: from selenium_fallback import harvest_cookies(mirror, timeout, headless) -> dict | None
独立测试: python selenium_fallback.py [--mirror https://sci-hub.st] [--headed] [--timeout 90]
"""

import argparse
import os
import sys
import time

CHROME_CANDIDATES = [
    os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
    os.path.join(os.environ.get('PROGRAMFILES', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
    os.path.join(os.environ.get('PROGRAMFILES(X86)', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
    os.path.join(os.environ.get('PROGRAMFILES(X86)', ''), 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    os.path.join(os.environ.get('PROGRAMFILES', ''), 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
]

CHALLENGE_TITLES = ('checking your browser', 'just a moment', 'attention required',
                    'recaptcha', 'ddos-guard', '你是机器人吗', 'are you a robot')


def find_browser():
    """定位本机 Chrome/Edge 可执行文件。"""
    for p in CHROME_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


def harvest_cookies(mirror, timeout=90, headless=True):
    """访问镜像首页并等待 JS 挑战自动通过，返回 {cookie名: 值}；失败返回 None。

    headless=False 时使用可见窗口（适合需要人工确认验证码的场景）。
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    # 优先使用本地匹配的 ChromeDriver；缺省时才走 Selenium Manager
    # （其下载端点 storage.googleapis.com 在本网络不可达，会自动挂起）
    service = None
    driver_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'drivers', 'chromedriver-win64', 'chromedriver.exe')
    if os.path.exists(driver_path):
        service = Service(executable_path=driver_path)

    opts = Options()
    binary = find_browser()
    if binary:
        opts.binary_location = binary
    if headless:
        opts.add_argument('--headless=new')
    opts.add_argument('--disable-blink-features=AutomationControlled')
    opts.add_argument('--no-first-run')
    opts.add_argument('--no-default-browser-check')
    opts.add_argument('--window-size=1366,768')
    opts.add_argument('--lang=en-US')
    opts.add_experimental_option('excludeSwitches', ['enable-automation'])
    opts.add_experimental_option('useAutomationExtension', False)

    driver = webdriver.Chrome(options=opts, service=service)
    driver.set_page_load_timeout(timeout)  # get() 也受收割时限约束，防页面挂死拖穿 deadline
    try:
        driver.get(mirror + '/')
        deadline = time.time() + timeout
        while time.time() < deadline:
            title = (driver.title or '').lower()
            passed = title and 'sci-hub' in title and not any(
                c in title for c in CHALLENGE_TITLES)
            if passed:
                cookies = {c['name']: c['value'] for c in driver.get_cookies()}
                if cookies:
                    return cookies
            time.sleep(2)
        return None
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description='Selenium 挑战 cookie 收割器')
    ap.add_argument('--mirror', default='https://sci-hub.st')
    ap.add_argument('--timeout', type=int, default=90)
    ap.add_argument('--headed', action='store_true', help='使用可见窗口')
    args = ap.parse_args()

    print('浏览器:', find_browser() or '未找到，将使用 Selenium Manager 下载')
    cookies = harvest_cookies(args.mirror, timeout=args.timeout, headless=not args.headed)
    if cookies:
        print(f'成功收割 {len(cookies)} 个 cookies: {list(cookies.keys())}')
    else:
        print('收割失败（挑战未通过或超时）')
        sys.exit(1)


if __name__ == '__main__':
    main()
