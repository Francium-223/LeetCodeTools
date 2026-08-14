"""LeetCode Tools cookie grabber. System Python only."""
import os, json, sys, time
from DrissionPage import ChromiumPage, ChromiumOptions

def main():
    cache_dir = os.path.join(os.path.expanduser('~'), '.leetcode_tools_cache')
    for i, a in enumerate(sys.argv):
        if a == '--cache-dir' and i + 1 < len(sys.argv):
            cache_dir = sys.argv[i + 1]
    signal_file = os.path.join(cache_dir, '.login_ready')
    os.makedirs(cache_dir, exist_ok=True)
    if os.path.exists(signal_file):
        os.remove(signal_file)

    candidates = [
        os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    ]
    browser_path = None
    for p in candidates:
        if os.path.isfile(p):
            browser_path = p
            break
    if not browser_path:
        raise FileNotFoundError('No Chrome/Edge found.')

    co = ChromiumOptions()
    co.set_browser_path(browser_path)
    co.set_user_data_path(os.path.join(cache_dir, 'chrome_profile'))
    co.auto_port()
    page = ChromiumPage(co)
    page.get('https://leetcode.cn/')

    while not os.path.exists(signal_file):
        time.sleep(1)

    all_cookies = {}
    sl = csrf = None
    for c in page.cookies():
        name = c.get('name', '')
        val = c.get('value', '')
        all_cookies[name] = val
        if name == 'sl-session': sl = val
        elif name == 'csrftoken': csrf = val
    if not sl:
        sys.exit(1)

    with open(os.path.join(cache_dir, 'cookie.json'), 'w') as f:
        json.dump({'sl-session': sl, 'csrftoken': csrf or '', 'all': all_cookies}, f)
    os.remove(signal_file)
    while True:
        time.sleep(60)

if __name__ == '__main__':
    main()
"""由系统 Python 3.14 运行。"""
import os, json, sys, time
from DrissionPage import ChromiumPage, ChromiumOptions

CACHE_DIR = os.path.join(os.path.expanduser('~'), '.leetcode_cn_cache')
SIGNAL_FILE = os.path.join(CACHE_DIR, '.login_ready')

def find_browser():
    candidates = [
        os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    raise FileNotFoundError('No Chrome/Edge found.')

def main():
    os.makedirs(CACHE_DIR, exist_ok=True)
    if os.path.exists(SIGNAL_FILE):
        os.remove(SIGNAL_FILE)
    browser_path = find_browser()
    co = ChromiumOptions()
    co.set_browser_path(browser_path)
    co.set_user_data_path(os.path.join(CACHE_DIR, 'chrome_profile'))
    co.auto_port()
    page = ChromiumPage(co)
    page.get('https://leetcode.cn/')
    while not os.path.exists(SIGNAL_FILE):
        time.sleep(1)
    sl = csrf = None
    all_cookies = {}
    for c in page.cookies():
        name = c.get('name', '')
        val = c.get('value', '')
        all_cookies[name] = val
        if name == 'sl-session': sl = val
        elif name == 'csrftoken': csrf = val
    if not sl:
        sys.exit(1)
    with open(os.path.join(CACHE_DIR, 'cookie.json'), 'w') as f:
        json.dump({'sl-session': sl, 'csrftoken': csrf or '', 'all': all_cookies}, f)
    os.remove(SIGNAL_FILE)
    # 不关浏览器，保持 session 存活
    while True:
        time.sleep(60)

if __name__ == '__main__':
    main()