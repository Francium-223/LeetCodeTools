import sublime
import sublime_plugin
import os
import json
import re
import ast
import time
import threading
import subprocess
import sys
import io
import contextlib
import traceback
import urllib.request
import urllib.error
import webbrowser


# ==================== 配置 ====================

def _settings():
    return sublime.load_settings('LeetCodeTools.sublime-settings')


def _site():
    return _settings().get('site', 'cn')


def _base_url():
    return 'https://leetcode.cn' if _site() == 'cn' else 'https://leetcode.com'


def _site_key(site=None):
    """缓存 / Cookie 按站点分家的后缀。

    cn 沿用原来的文件名（`cookie.json` / `problems/`…），这样已有用户的 CN 缓存和登录态
    不用迁移；别的站加后缀（`cookie_com.json` / `problems_com/`…），免得切站互相覆盖。
    """
    site = site or _site()
    return '' if site == 'cn' else '_' + site


def _other_site():
    return 'cn' if _site() != 'cn' else 'com'


def _working_dir():
    return os.path.expanduser(_settings().get('working_dir', '~/leetcode'))


def _default_lang():
    return _settings().get('default_lang', 'python3')


# ─── HTTP：统一的浏览器化请求头 + 重试 ───
#
# LeetCode 前面挂着 Cloudflare，裸的 'Mozilla/5.0'、缺 Host/Accept 的请求很容易被判成
# 机器人：轻则 403，重则直接给一段挑战页。这里照 leetcode.nvim 的做法，每个请求都带上
# 一整套浏览器头，并对 5xx / 429 退避重试（比赛期间 LeetCode 会临时 503 / 429）。

_BROWSER_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
               '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')


def _browser_ua():
    """User-Agent：设置里的 browser_ua 优先。

    Cloudflare 的 cf_clearance 是跟拿到它的 UA（和 IP）绑定的，UA 对不上就会被当成
    无效 clearance 再挑战一次。所以想让粘贴进来的 cf_clearance 生效，就填自己浏览器的 UA。
    """
    return (_settings().get('browser_ua', '') or '').strip() or _BROWSER_UA


def _browser_headers(path='/', cookie_raw='', csrf_token='',
                     accept='application/json, text/plain, */*', json_body=True, host=True, extra=None):
    """构造一份像浏览器的请求头；path 是接口/题目页路径，用来拼 Referer。"""
    base = _base_url()
    if not path.startswith('/'):
        path = '/' + path
    headers = {
        'User-Agent': _browser_ua(),
        'Accept': accept,
        'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
        'Origin': base,
        'Referer': base + path,
    }
    if host:
        headers['Host'] = base.split('//', 1)[1].rstrip('/')
    if json_body:
        headers['Content-Type'] = 'application/json'
    if cookie_raw:
        headers['Cookie'] = cookie_raw
    if csrf_token:
        headers['X-CSRFToken'] = csrf_token
    if extra:
        headers.update(extra)
    return headers


def _http_error_text(e, what='Request'):
    """把 HTTPError 翻成人话。"""
    body = ''
    try:
        body = e.read().decode('utf-8', 'replace').strip()[:400]
    except Exception:
        pass
    mitigated = ''
    try:
        mitigated = (e.headers.get('cf-mitigated') or '') if e.headers else ''
    except Exception:
        pass
    if mitigated or 'Just a moment' in body or 'cf-chl' in body:
        return ('%s HTTP %d: 被 Cloudflare 的人机挑战拦住了（cf-mitigated: %s）。\n'
                'leetcode.com 的提交 / Run Code 恰好就在这道门后面：先在浏览器里过完挑战，'
                '再把「请求头里的完整 Cookie」（要含 cf_clearance / __cf_bm）粘给 Login，'
                '并把设置里的 browser_ua 填成同一个浏览器的 User-Agent（clearance 和 UA 绑定）。'
                % (what, e.code, mitigated or 'challenge'))
    if e.code == 429:
        return ('%s HTTP 429: LeetCode 限流了（Run Code / Submit 点太密），几秒后再试。\n'
                '%s HTTP 429: rate limited by LeetCode — wait a few seconds and retry.'
                % (what, what))
    if e.code in (401, 403):
        return ('%s HTTP %d: cookie 可能已过期，或者 LeetCode 临时限制了 API 访问'
                '（比赛期间常见；也可以试试关掉 VPN）。%s' % (what, e.code, body))
    return '%s HTTP %d: %s' % (what, e.code, body)


def _urlopen_retry(req, timeout=30, tries=5, wait=1.0):
    """发请求；5xx / 429 退避重试，其它 HTTP 错误直接抛（让调用方翻译成人话）。"""
    attempt = 0
    while True:
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            attempt += 1
            if e.code < 500 and e.code != 429:
                raise
            if attempt >= tries:
                raise
            time.sleep(wait * attempt)


def _lang():
    return _settings().get('language', 'zh')


def _run_timeout():
    return _settings().get('run_timeout', 1)


def _cache_dir():
    return os.path.join(_working_dir(), '.cache')


def _list_lang(lang=None):
    """题目列表（标题）缓存按**语言**分家，和 site 无关：zh / en 各一份。"""
    lang = (lang or _lang() or 'zh').strip().lower()
    return lang if lang in ('zh', 'en') else 'zh'


def _problem_title(p):
    """按当前 language 取标题：zh 先中文，en 先英文，缺了就回滚另一种。"""
    if _list_lang() == 'zh':
        return p.get('titleCn') or p.get('title') or '?'
    return p.get('title') or p.get('titleCn') or '?'


def _last_update_path(lang=None):
    return os.path.join(_cache_dir(), 'last_update_' + _list_lang(lang) + '.json')


def _maybe_auto_update():
    """如果缓存过期则自动更新。"""
    age_days = _settings().get('cache_age_days', 7)
    if not os.path.exists(_problem_list_cache_path()):
        return
    if os.path.exists(_last_update_path()):
        with open(_last_update_path()) as f:
            ts = json.load(f).get('timestamp', 0)
        if time.time() - ts < age_days * 86400:
            return
    # 过期了，删缓存触发重建
    os.remove(_problem_list_cache_path())
    sublime.status_message('LeetCode Tools: Cache expired, auto-updating...')


def _cookie_cache_path():
    # cn -> cookie.json（保持原样，不用迁移）；com -> cookie_com.json
    return os.path.join(_cache_dir(), 'cookie' + _site_key() + '.json')


def _problem_list_cache_path(lang=None):
    # 标题缓存按语言分：problem_list_zh.json / problem_list_en.json
    return os.path.join(_cache_dir(), 'problem_list_' + _list_lang(lang) + '.json')


def _write_problem_list(lang, problems):
    """写一份题目列表缓存（按语言），并记下这份的更新时间。"""
    os.makedirs(_cache_dir(), exist_ok=True)
    with open(_problem_list_cache_path(lang), 'w', encoding='utf-8') as f:
        json.dump(problems, f, ensure_ascii=False, indent=2)
    with open(_last_update_path(lang), 'w') as f:
        json.dump({'timestamp': time.time()}, f)


def _legacy_problem_list_paths():
    """迁移前那两份按站点存的旧列表（cn 那份同时带中英标题）。"""
    return [os.path.join(_cache_dir(), 'problem_list.json'),
            os.path.join(_cache_dir(), 'problem_list_com.json')]


def _study_plans_cache_path():
    return os.path.join(_cache_dir(), 'study_plans' + _site_key() + '.json')


def _study_plan_problems_cache_path():
    return os.path.join(_cache_dir(), 'study_plan_problems' + _site_key() + '.json')


def _problem_cache_dir(site=None):
    return os.path.join(_cache_dir(), 'problems' + _site_key(site))


def _problem_json_path(slug, site=None):
    return os.path.join(_problem_cache_dir(site), slug + '.json')


def _problem_in_path(slug, site=None):
    return os.path.join(_problem_cache_dir(site), slug + '_in.json')


def _problem_out_path(slug, site=None):
    return os.path.join(_problem_cache_dir(site), slug + '_out.json')


def _read_expected_outputs(slug):
    """读离线判题用的期望值（_out.json）。

    当前站点没抓到期望值时（.com 的 Run Code 被 Cloudflare 挡着就是这种情况），
    退回另一个站点的同名文件 —— 两个站的官方示例是一样的，cn 上抓过的值在 com 上照样能用。
    返回 None 表示一个真值都没有，面板就不显示 EXPECT。
    """
    current = _read_case_list(_problem_out_path(slug))
    if any(v is not None and v != '' for v in current):
        return current
    other = _read_case_list(_problem_out_path(slug, _other_site()))
    if any(v is not None and v != '' for v in other):
        return other
    return current or None


def _no_expected_hint():
    '''没有期望值时，给一句能照做的下一步（顺便区分"没登录"和"没生成用例"）。'''
    try:
        get_leetcode_cookie()
    except RuntimeError:
        return ('Not logged in to ' + _base_url() + ' — run "LeetCodeTools: Login", '
                'then "LeetCodeTools: Reload Problem".')
    except Exception as e:
        return 'Could not check the login state: ' + str(e)[:80]
    return ('No testcases stored for this problem yet — run "LeetCodeTools: Reload Problem" '
            'to generate them with LeetCode Run Code.')


def _problem_images_dir(slug):
    return os.path.join(_cache_dir(), 'images', slug)


def _explanation_images_dir(slug):
    return os.path.join(_cache_dir(), 'images', slug + '_explanation')


def _cache_is_fresh(cache_path):
    """判断缓存文件是否在 cache_age_days 天内。"""
    if not os.path.exists(cache_path):
        return False
    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        ts = data.get('timestamp', 0) if isinstance(data, dict) else 0
        age_days = _settings().get('cache_age_days', 7)
        return time.time() - ts < age_days * 86400
    except Exception:
        return False


# ── 找到系统 Python，用于跑 offline_runner ──

_SYSTEM_PYTHON = None


def _find_system_python():
    """找到系统较高版本 Python（带缓存，且隐藏控制台窗口）。"""
    global _SYSTEM_PYTHON
    if _SYSTEM_PYTHON:
        return _SYSTEM_PYTHON
    import glob
    candidates = [
        os.path.expandvars(r'%LOCALAPPDATA%\Python\bin\python3.exe'),
        os.path.expandvars(r'%LOCALAPPDATA%\Python\bin\python.exe'),
    ]
    for pat in [r'%LOCALAPPDATA%\Python\pythoncore-3.*-64\python.exe']:
        candidates.extend(glob.glob(os.path.expandvars(pat)))
    candidates.append('python3')
    candidates.append('python')
    kwargs = {}
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    for p in candidates:
        try:
            ver = subprocess.check_output([p, '--version'], stderr=subprocess.STDOUT, timeout=5, **kwargs).decode()
            if '3.' in ver:
                _SYSTEM_PYTHON = p
                return p
        except Exception:
            continue
    raise RuntimeError(
        'No system Python 3 found. Please install Python 3 and add it to PATH '
        '(required by Login and the offline Run).'
    )


LANG_EXT = {
    'python3': 'py', 'python': 'py', 'java': 'java',
    'cpp': 'cpp', 'c': 'c', 'csharp': 'cs',
    'javascript': 'js', 'typescript': 'ts', 'golang': 'go',
    'rust': 'rs', 'kotlin': 'kt', 'swift': 'swift',
    'scala': 'scala', 'ruby': 'rb', 'php': 'php',
}

EXT_LANG = {v: k for k, v in LANG_EXT.items() if v not in ('py',) or k == 'python3'}
EXT_LANG['py'] = 'python3'


def _detect_slug(fp):
    """从文件路径推断题目 slug（优先读元数据 JSON 的 titleSlug）。"""
    ext = os.path.splitext(fp)[1].lstrip('.')
    base = fp[:-(len(ext) + 1)] if ext else fp
    meta_base = base
    for suffix in ('_in', '_out'):
        if meta_base.endswith(suffix):
            meta_base = meta_base[:-len(suffix)]
            break
    slug = None
    json_path = _problem_json_path(os.path.basename(meta_base))
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                slug = json.load(f).get('titleSlug')
        except Exception:
            slug = None
    return slug or os.path.basename(meta_base)


def _guess_image_ext(url, resp):
    """根据 URL 路径或 Content-Type 推断图片扩展名。"""
    path = url.split('?')[0]
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp'):
        return '.jpg' if ext == '.jpeg' else ext
    ctype = ''
    try:
        ctype = (resp.headers.get('Content-Type', '') or '').split(';')[0].strip().lower()
    except Exception:
        ctype = ''
    mapping = {
        'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif',
        'image/webp': '.webp', 'image/svg+xml': '.svg', 'image/bmp': '.bmp',
    }
    return mapping.get(ctype, '.png')


def _download_images(html, img_dir, rel_prefix):
    """下载 HTML 里的 <img> 到本地 img_dir，替换成 ![](rel_prefix/fname)。"""
    counter = [0]

    def _replace(m):
        src = m.group(1)
        counter[0] += 1
        try:
            req = urllib.request.Request(src, headers=_browser_headers(
                '/problemset/', accept='image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
                json_body=False, host=False))
            resp = _urlopen_retry(req, timeout=15, tries=2)
            data = resp.read()
            ext = _guess_image_ext(src, resp)
            fname = str(counter[0]) + ext
            os.makedirs(img_dir, exist_ok=True)
            with open(os.path.join(img_dir, fname), 'wb') as f:
                f.write(data)
            return '![](' + rel_prefix + os.sep + fname + ')'
        except Exception:
            return '![](' + src + ')'

    return re.sub(r'<img[^>]*src="([^"]+)"[^>]*/?>', _replace, html)


_MD_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(((?:[^)\s]|\\[()])+)\)')


def _download_markdown_images(md, img_dir, rel_prefix):
    """下载 Markdown 里 ![](http...) 图片到本地，替换成本地路径。"""
    counter = [0]

    def _replace(m):
        alt = m.group(1)
        src = m.group(2).strip()
        raw_src = src.replace('\\(', '(').replace('\\)', ')')
        if not raw_src.startswith(('http://', 'https://')):
            return m.group(0)
        counter[0] += 1
        try:
            req = urllib.request.Request(raw_src, headers=_browser_headers(
                '/problemset/', accept='image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
                json_body=False, host=False))
            resp = _urlopen_retry(req, timeout=15, tries=2)
            data = resp.read()
            ext = _guess_image_ext(raw_src, resp)
            fname = str(counter[0]) + ext
            os.makedirs(img_dir, exist_ok=True)
            with open(os.path.join(img_dir, fname), 'wb') as f:
                f.write(data)
            return '![' + alt + '](' + rel_prefix + os.sep + fname + ')'
        except Exception:
            return m.group(0)

    return _MD_IMAGE_RE.sub(_replace, md)


_VIDEO_PLACEHOLDER_RE = re.compile(
    r'!\[([^\]]*)\]\(([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\)'
)


def _clean_solution_markdown(md, videos=None):
    """规整 LeetCode 题解正文（本身已是 Markdown）：规整代码块语言标签、统一换行、把视频占位符换成封面链接。"""
    content = (md or '').replace('\r\n', '\n').replace('\r', '\n')

    def _fix_fence(m):
        lang = re.sub(r'\s*\[.*?\]\s*$', '', m.group(1)).strip().lower()
        return '```' + lang

    content = re.sub(r'```([^\n`]*)', _fix_fence, content)

    if videos:
        counter = [0]

        def _fix_video(m):
            alt = m.group(1)
            i = counter[0]
            counter[0] += 1
            cover = ''
            if i < len(videos):
                cover = (videos[i] or {}).get('coverUrl') or ''
            if cover:
                return '![' + alt + '](' + cover + ')'
            return m.group(0)

        content = _VIDEO_PLACEHOLDER_RE.sub(_fix_video, content)

    content = re.sub(r'\n{3,}', '\n\n', content)
    return content.strip()


# ==================== Cookie & API helpers ====================

# 从 DevTools 复制出来的各种形态里抠 cookie：表头一行 / Copy as cURL(bash|cmd) / Copy as fetch
#
# 按「行」找 `cookie:` 再取这一行剩下的部分，而不是拿引号配对：
# cmd 的 Copy as cURL 把整条写成 `-H ^"Cookie: …^"`，而且值里面本身就可能有
# `^\^"`（比如 ip_check=(true, ^\^"1.2.3.4^\^")）、`^|`、`^%^22` 这些转义，
# 用引号配对会在第一个 `"` 处截断，直接把 LEETCODE_SESSION 丢掉。
_COOKIE_LINE_RE = re.compile(r'''(?i)\bcookie\b\s*["']?\s*:\s*''')
_COOKIE_FLAG_RE = re.compile(r'''(?is)(?:-b|--cookie)\s+(?P<rest>[^\n]*)''')
_LEAD_JUNK_RE = re.compile(r'''^[\s"']+''')
_TAIL_JUNK_RE = re.compile(r'''[\s"',;\\^]+$''')


def _clean_cookie_value(value, cmd_escaped=False):
    """洗掉外层引号 / 续行符 / 结尾逗号，并把 cmd 的 ^ 转义还原（^" -> "，^| -> |，^% -> %）。"""
    value = _LEAD_JUNK_RE.sub('', value or '')
    value = _TAIL_JUNK_RE.sub('', value)
    if cmd_escaped:
        value = re.sub(r'\^(.)', r'\1', value)
        value = _TAIL_JUNK_RE.sub('', value)
    return re.sub(r'[\r\n\t]+', '', value).strip()


def _extract_cookie_text(text):
    """从粘贴的内容里抠出 Cookie 值本身。

    支持的粘法（都是 DevTools 里点两下就能拿到的，省得手动全选一长串）：
      * 裸的 Cookie 值：`LEETCODE_SESSION=…; csrftoken=…`
      * 表头一行：`Cookie: …`（Request Headers 里那一行）
      * **Copy as cURL**：bash `-H 'cookie: …'`、Windows cmd `-H ^"Cookie: …^"`、`-b '…'` / `--cookie '…'`
      * **Copy as fetch**：`"cookie": "…"`
    抠不出来（比如只贴了一个 session 值）就原样返回，交给下面按老逻辑处理。
    """
    raw = (text or '').replace('\r\n', '\n').replace('\r', '\n')
    cmd_escaped = '^"' in raw          # Windows cmd 的 Copy as cURL 把引号写成 ^"
    for line in raw.split('\n'):
        m = _COOKIE_LINE_RE.search(line)
        if m:
            value = _clean_cookie_value(line[m.end():], cmd_escaped)
            if value:
                return value
    m = _COOKIE_FLAG_RE.search(raw)    # curl -b '…' / --cookie '…'
    if m:
        value = _clean_cookie_value(m.group('rest'), cmd_escaped)
        if value:
            return value
    return raw


def _save_cookie_from_text(text):
    """解析用户粘贴的内容并保存登录态。

    接受：整条 Cookie / `Cookie: …` 一行 / Copy as cURL 整段 / Copy as fetch 整段 /
    `LEETCODE_SESSION=…` / 单独的 session 值。
    """
    text = _extract_cookie_text(text)
    text = (text or '').strip().strip(';').strip()
    if not text:
        raise ValueError('Cookie is empty.')
    # 换行 / 多余空白统一成单个空格（避免换行把值弄坏）
    text = ' '.join(text.split())
    pairs = {}
    lower = text.lower()
    if ';' in text or 'sl-session=' in lower or 'csrftoken=' in lower or 'leetcode_session=' in lower:
        for part in text.split(';'):
            part = part.strip()
            if '=' not in part:
                continue
            k, v = part.split('=', 1)
            k = k.strip()
            v = v.strip().strip('"').strip()
            if k:
                pairs[k] = v
    session = pairs.get('LEETCODE_SESSION') or pairs.get('sl-session')
    if not session:
        # 只贴了值（不带 key），当作 LEETCODE_SESSION
        session = text.strip('"').strip()
        pairs['LEETCODE_SESSION'] = session
    if not session:
        raise ValueError('No session cookie found. Paste the whole Cookie header.')
    data = {
        'LEETCODE_SESSION': pairs.get('LEETCODE_SESSION', ''),
        'sl-session': pairs.get('sl-session', ''),
        'csrftoken': pairs.get('csrftoken', ''),
        'all': pairs,
    }
    os.makedirs(_cache_dir(), exist_ok=True)
    with open(_cookie_cache_path(), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    return data


def _validate_cookie(cookie_dict):
    """用需要登录的查询验证会话。

    userStatus { isSignedIn username } 在 cn 和 com 上都是有效字段（com 上没有 globalData，
    cn 的 todayRecord.userStatus 未登录时直接返回 null），所以两边统一用它。
    """
    try:
        all_cookies = cookie_dict.get('all', {})
        raw = '; '.join(k + '=' + v for k, v in all_cookies.items())
        if not raw:
            session = cookie_dict.get('LEETCODE_SESSION') or cookie_dict.get('sl-session') or ''
            raw = 'LEETCODE_SESSION=' + session
        req = urllib.request.Request(
            _base_url() + '/graphql/',
            data=json.dumps({'query': 'query { userStatus { isSignedIn username } }'}).encode(),
            headers=_browser_headers('/problemset/', raw),
        )
        resp = _urlopen_retry(req, timeout=10, tries=2)
        status = (json.loads(resp.read()).get('data') or {}).get('userStatus') or {}
        return bool(status.get('isSignedIn'))
    except Exception:
        return False


def get_leetcode_cookie():
    cache_path = _cookie_cache_path()
    if os.path.exists(cache_path):
        with open(cache_path, 'r', encoding='utf-8') as f:
            cached = json.load(f)
        if _validate_cookie(cached):
            return cached
    raise RuntimeError('No valid cookie. Run "LeetCode Tools: Login" first — it opens the browser and asks you to paste the LEETCODE_SESSION cookie.')


def _fetch_csrftoken(cookie_raw=''):
    """通过 nojGlobalData 获取 csrftoken（带登录会话，尽量和会话匹配）。"""
    try:
        base = _base_url()
        headers = _browser_headers('/', cookie_raw)
        req = urllib.request.Request(
            base + '/graphql/',
            data=json.dumps({'query': 'query nojGlobalData { siteRegion }'}).encode(),
            headers=headers,
        )
        resp = _urlopen_retry(req, timeout=10, tries=2)
        for h in (resp.headers.get_all('Set-Cookie') or []):
            if h.lower().startswith('csrftoken='):
                return h.split('=', 1)[1].split(';', 1)[0]
    except Exception:
        pass
    return ''


def _set_cookie_value(raw, name, value):
    """把 raw cookie 串里的 name=… 换成新值；原本没有就追加。"""
    parts = [p.strip() for p in (raw or '').split(';') if p.strip()]
    out, found = [], False
    for p in parts:
        if p.split('=', 1)[0].strip().lower() == name.lower():
            out.append(name + '=' + value)
            found = True
        else:
            out.append(p)
    if not found:
        out.append(name + '=' + value)
    return '; '.join(out)


def _persist_csrftoken(token):
    """把刷新到的 csrftoken 写回 cookie 文件，下次启动直接是新值（写不了就算了）。"""
    try:
        path = _cookie_cache_path()
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        data['csrftoken'] = token
        if isinstance(data.get('all'), dict):
            data['all']['csrftoken'] = token
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def _build_client():
    cookie_dict = get_leetcode_cookie()
    all_cookies = cookie_dict.get('all', {})
    parts = []
    for k, v in all_cookies.items():
        parts.append(k + '=' + v)
    raw = '; '.join(parts)
    if not raw:
        session = cookie_dict.get('LEETCODE_SESSION') or cookie_dict.get('sl-session') or ''
        raw = 'LEETCODE_SESSION=' + session
        if cookie_dict.get('csrftoken'):
            raw += '; csrftoken=' + cookie_dict['csrftoken']
    client = LeetCodeToolsClient(raw)
    # csrftoken 会随浏览器重新登录轮换；拿着旧的去提交 / Run Code 会被拒（实测是 403/404，
    # 很容易误判成"接口挂了"）。所以每次构建都拿当前会话换一个新的，变了就写回文件。
    csrf = _fetch_csrftoken(client.cookie_raw)
    if csrf and csrf != client.csrf_token:
        client.csrf_token = csrf
        client.cookie_raw = _set_cookie_value(client.cookie_raw, 'csrftoken', csrf)
        _persist_csrftoken(csrf)
    return client


def _build_public_client():
    """无需登录的公开客户端（题解等公开接口用）。"""
    return LeetCodeToolsClient('')


# ==================== LeetCode CN API 客户端 ====================

# 同一时间只让一个线程去拉全量题目列表（4000+ 题要 40 个请求，别重复拉）
_problem_list_lock = threading.Lock()


class LeetCodeToolsClient:
    def __init__(self, raw_cookie):
        self.cookie_raw = raw_cookie
        self.csrf_token = self._extract_csrf(raw_cookie)

    def _extract_csrf(self, raw_cookie):
        for item in raw_cookie.split(';'):
            item = item.strip()
            if item.startswith('csrftoken='):
                return item.split('=')[1]
        return ''

    def _graphql(self, query, variables=None, operation_name=None):
        """发 GraphQL 请求，返回 data dict。"""
        payload = {'query': query}
        if variables:
            payload['variables'] = variables
        if operation_name:
            payload['operationName'] = operation_name
        body = json.dumps(payload).encode()
        base = _base_url()
        headers = _browser_headers('/problemset/', self.cookie_raw, self.csrf_token)
        req = urllib.request.Request(base + '/graphql/', data=body, headers=headers)
        try:
            resp = _urlopen_retry(req, timeout=30)
        except urllib.error.HTTPError as e:
            raise Exception(_http_error_text(e, 'GraphQL'))
        data = json.loads(resp.read())
        if 'errors' in data:
            raise Exception('GraphQL error: ' + str(data['errors']))
        return data['data']

    def get_problem_detail(self, title_slug):
        query = '''
        query questionData($titleSlug: String!) {
          question(titleSlug: $titleSlug) {
            questionId questionFrontendId title translatedTitle
            titleSlug content translatedContent difficulty
            exampleTestcases metaData
            topicTags { name translatedName slug }
            codeSnippets { lang langSlug code }
          }
        }
        '''
        data = self._graphql(query, {'titleSlug': title_slug})
        return data['question']

    def _fetch_problem_list(self):
        """从 GraphQL 一次拉全量题目列表（CN 有 titleCn，US 没有）。"""
        all_questions = []
        skip = 0
        limit = 100
        cn = _site() == 'cn'
        while True:
            if cn:
                query = 'query{problemsetQuestionList(skip:' + str(skip) + ' limit:' + str(limit) + '){total questions{frontendQuestionId title titleCn titleSlug difficulty}}}'
                data = self._graphql(query)['problemsetQuestionList']
                items, total, fid_key = data['questions'], data['total'], 'frontendQuestionId'
            else:
                query = 'query{questionList(categorySlug:"" skip:' + str(skip) + ' limit:' + str(limit) + ' filters:{}){totalNum data{questionFrontendId title titleSlug difficulty}}}'
                data = self._graphql(query)['questionList']
                items, total, fid_key = data['data'], data['totalNum'], 'questionFrontendId'
            for q in items:
                all_questions.append({
                    'frontendQuestionId': str(q.get(fid_key, '')),
                    'titleCn': q.get('titleCn', '') if cn else '',
                    'title': q.get('title', ''),
                    'titleSlug': q.get('titleSlug', ''),
                    'difficulty': q.get('difficulty', ''),
                })
            skip += limit
            if skip >= total:
                break
        # 英文那份谁都能产出；中文那份只有 cn 有（cn 一次同时给 title 和 titleCn）。
        # 在 .com 上就只写英文那份，别把已有的中文列表覆盖掉。
        _write_problem_list('en', [{
            'frontendQuestionId': p['frontendQuestionId'],
            'titleCn': '',
            'title': p.get('title', ''),
            'titleSlug': p.get('titleSlug', ''),
            'difficulty': p.get('difficulty', ''),
        } for p in all_questions])
        if cn:
            _write_problem_list('zh', all_questions)
        return all_questions

    def _load_cache(self):
        """题目列表：先取当前语言的缓存；没有再退另一种语言 / 迁移前的旧文件；都没有才现拉。"""
        _maybe_auto_update()
        lang = _list_lang()
        data = _read_case_list(_problem_list_cache_path(lang))
        if data:
            return data
        other = 'en' if lang == 'zh' else 'zh'
        for path in [_problem_list_cache_path(other)] + _legacy_problem_list_paths():
            data = _read_case_list(path)
            if data:
                # 手上是另一种语言（或迁移前的旧文件）。当前站点要是能产出当前语言那份，
                # 就后台补上（cn 能出 zh 和 en；com 只能出 en，所以 com+zh 不重拉）。
                if _site() == 'cn' or _list_lang() == 'en':
                    self._refresh_problem_list_async()
                return data
        return self._fetch_problem_list()

    def _refresh_problem_list_async(self):
        """后台补一次题目列表缓存；已经有一个在拉就直接跳过。"""
        if not _problem_list_lock.acquire(blocking=False):
            return

        def run():
            try:
                self._fetch_problem_list()
            except Exception:
                pass
            finally:
                _problem_list_lock.release()

        threading.Thread(target=run, daemon=True).start()

    def fetch_problem(self, question_id, lang='python3', working_dir=None, force=False, study_plan_slug=None):
        if working_dir is None:
            working_dir = _working_dir()
        problems = self._load_cache()
        qid_str = str(question_id)
        title_slug = None
        fid = None
        for p in problems:
            if str(p.get('frontendQuestionId', '')) == qid_str:
                title_slug = p['titleSlug']
                fid = p['frontendQuestionId']
                break
        if not title_slug:
            for p in problems:
                if (p.get('titleSlug', '') or '').lower() == qid_str.lower():
                    title_slug = p['titleSlug']
                    fid = p['frontendQuestionId']
                    break
        if not title_slug:
            raise ValueError('Problem not found: ' + str(question_id))

        detail = self.get_problem_detail(title_slug)
        if not detail:
            # 列表缓存可能是另一个站的（比如 com + zh 用的是 cn 那份中文列表），
            # 里面可能有本站没有的题，这里直接给个人话报错，别炸 AttributeError
            raise Exception('Problem not found on ' + _base_url() + ': ' + str(question_id))
        os.makedirs(working_dir, exist_ok=True)

        # MD
        md_path = os.path.join(working_dir, title_slug + '.md')
        img_dir = _problem_images_dir(title_slug)
        img_ref = os.path.relpath(img_dir, working_dir).replace('\\', '/')
        difficulty = detail.get('difficulty') or 'Unknown'
        tags = ', '.join((t.get('translatedName') or t.get('name') or '')
                         for t in detail.get('topicTags', []))
        use_zh = (_lang() == 'zh')
        content = detail.get('translatedContent' if use_zh else 'content')
        content = content or detail.get('content' if use_zh else 'translatedContent') or ''
        title = detail.get('translatedTitle' if use_zh else 'title')
        title = title or detail.get('title' if use_zh else 'translatedTitle') or ''
        content = re.sub(r'<sup>(.*?)</sup>', r'^\1', content)
        content = re.sub(r'<sub>(.*?)</sub>', r'_\1', content)
        content = re.sub(r'<pre>(.*?)</pre>', r'\n```\n\1\n```\n', content, flags=re.DOTALL)
        content = re.sub(r'<code>(.*?)</code>', r'`\1`', content)
        content = re.sub(r'<em>(.*?)</em>', r'*\1*', content)
        content = re.sub(r'<strong>(.*?)</strong>', r'**\1**', content)
        content = _download_images(content, img_dir, img_ref)
        content = re.sub(r'<[^>]+>', '', content)
        content = re.sub(r'&nbsp;', ' ', content)
        content = re.sub(r'&lt;', '<', content)
        content = re.sub(r'&gt;', '>', content)
        content = re.sub(r'&amp;', '&', content)
        content = re.sub(r'\n{3,}', '\n\n', content)
        if force or not os.path.exists(md_path):
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write('# ' + str(fid) + '. ' + title + '\n\n')
                f.write('**Difficulty**: ' + difficulty + '\n\n')
                if tags:
                    f.write('**Tags**: ' + tags + '\n\n')
                f.write('---\n\n')
                f.write(content)

        # Code
        ext = LANG_EXT.get(lang, 'txt')
        code_path = os.path.join(working_dir, title_slug + '.' + ext)
        snippets = detail.get('codeSnippets', [])
        code = ''
        for s in snippets:
            if s.get('langSlug') == lang:
                code = s.get('code', '')
                break
        if not code and snippets:
            code = snippets[0].get('code', '')
            lang = snippets[0].get('langSlug', lang)
        if not code:
            code = '# No code template'
        if force or not os.path.exists(code_path):
            with open(code_path, 'w', encoding='utf-8') as f:
                f.write(code)

        # JSON
        json_path = _problem_json_path(title_slug)
        os.makedirs(_problem_cache_dir(), exist_ok=True)
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump({
                'titleSlug': title_slug,
                'frontendQuestionId': fid,
                'questionId': detail.get('questionId', ''),
                'difficulty': difficulty,
                'exampleTestcases': detail.get('exampleTestcases', ''),
                'metaData': detail.get('metaData', ''),
                'study_plan_slug': study_plan_slug or '',
            }, f, ensure_ascii=False, indent=2)

        # Interpret: 插 return 桩 → Run Code → 抓预期输出
        in_path = _problem_in_path(title_slug)
        out_path = _problem_out_path(title_slug)
        example = detail.get('exampleTestcases', '')
        meta_str = detail.get('metaData', '')
        testcases = _parse_testcases(example, meta_str)
        question_id = detail.get('questionId', '')

        # 默认要抓一次（没有 / 无效的缓存都要抓）；只有"缓存已经有效"才跳过。
        # force = 不管缓存有没有效都重抓（Reload Problem 用）。
        # 注意别写成 need_interpret = bool(force)：那样普通 fetch 会直接跳过生成用例。
        need_interpret = True
        if not force and os.path.exists(out_path) and os.path.exists(in_path):
            try:
                with open(out_path) as f:
                    cached = json.load(f)
                if cached and all(v is not None and v != '' for v in cached):
                    need_interpret = False
            except Exception:
                pass

        if need_interpret:
            stub_code = _insert_return_stubs(code, meta_str)
            outputs = []
            run_err = ''
            try:
                sid = self.interpret_solution(title_slug, question_id, lang, stub_code, example)
                result_data = self._check_interpret(sid)
                expected = result_data.get('expected_code_answer', [])
                # 去尾哨兵
                while expected and expected[-1] == '':
                    expected.pop()
                for v in expected:
                    try:
                        outputs.append(json.loads(v))
                    except Exception:
                        outputs.append(v)
            except Exception as e:
                # 先记下错误，等用例落盘之后再报（见下面）—— 顺序反了的话，
                # 框没被点掉 / 进程被杀，这两个文件就永远写不出来。
                run_err = str(e)
                # None = "这条没有期望值"：面板里就不显示 EXPECT，也不会算出假的 FAIL
                outputs = [None] * len(testcases)
            # 这次没抓到就别拿空值覆盖原来抓好的（force 重抓时尤其重要）
            if all(v is None or v == '' for v in outputs):
                cached = _read_case_list(out_path)
                if any(v is not None and v != '' for v in cached):
                    outputs = cached
            with open(in_path, 'w', encoding='utf-8') as f:
                serializable = []
                for tc in testcases:
                    serializable.append([_to_json(v) for v in tc])
                json.dump(serializable, f, ensure_ascii=False, indent=2)
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(outputs, f, ensure_ascii=False, indent=2)
            # 报错必须在写文件之后：以前是先弹模态框（工作线程里同步阻塞），
            # 框没被点掉就永远写不出用例 —— 表现就是"题面有了、用例压根没生成"。
            if run_err:
                if _site() == 'cn':
                    sublime.error_message('LeetCodeTools: interpret failed\n\n' + run_err)
                else:
                    sublime.status_message(
                        'LeetCodeTools: no expected outputs (Run Code blocked) — ' + run_err[:80])

        return {
            'titleSlug': title_slug, 'fid': fid, 'lang': lang, 'ext': ext,
            'md_path': md_path, 'code_path': code_path, 'json_path': json_path,
            'in_path': in_path, 'out_path': out_path,
        }

    def submit_code(self, problem_slug, question_id, lang_slug, typed_code, study_plan_slug=None):
        base_url = _base_url()
        url = base_url + '/problems/' + problem_slug + '/submit/'
        body = {
            'lang': lang_slug,
            'question_id': str(question_id),
            'typed_code': typed_code,
        }
        if study_plan_slug:
            body['study_plan_slug'] = study_plan_slug
        payload = json.dumps(body).encode()
        headers = _browser_headers('/problems/' + problem_slug + '/', self.cookie_raw, self.csrf_token)
        req = urllib.request.Request(url, data=payload, headers=headers)
        try:
            resp = _urlopen_retry(req, timeout=30)
        except urllib.error.HTTPError as e:
            raise Exception(_http_error_text(e, 'Submit'))
        res_json = json.loads(resp.read())
        if 'submission_id' not in res_json:
            raise Exception('Submission failed: ' + str(res_json))
        return res_json['submission_id']

    def check_submission(self, submission_id):
        url = _base_url() + '/submissions/detail/' + str(int(submission_id)) + '/check/'
        headers = _browser_headers('/submissions/detail/' + str(int(submission_id)) + '/check/',
                                   self.cookie_raw)
        for _ in range(20):
            time.sleep(1)
            req = urllib.request.Request(url, headers=headers)
            try:
                resp = _urlopen_retry(req, timeout=10, tries=3)
            except urllib.error.HTTPError as e:
                raise Exception(_http_error_text(e, 'Check submission'))
            data = json.loads(resp.read())
            state = data.get('state', '')
            if state == 'SUCCESS':
                return data
        raise Exception('Check submission timed out')

    def interpret_solution(self, problem_slug, question_id, lang_slug, typed_code, test_input):
        """Run Code（不占提交历史），返回 interpret_id。"""
        url = _base_url() + '/problems/' + problem_slug + '/interpret_solution/'
        payload = json.dumps({
            'lang': lang_slug,
            'question_id': str(question_id),
            'typed_code': typed_code,
            'data_input': test_input,
        }).encode()
        headers = _browser_headers('/problems/' + problem_slug + '/', self.cookie_raw, self.csrf_token)
        req = urllib.request.Request(url, data=payload, headers=headers)
        try:
            resp = _urlopen_retry(req, timeout=30)
        except urllib.error.HTTPError as e:
            raise Exception(_http_error_text(e, 'Run Code'))
        res = json.loads(resp.read())
        if 'interpret_id' not in res:
            raise Exception('Interpret failed: ' + str(res))
        return res['interpret_id']

    def _check_interpret(self, interpret_id):
        url = _base_url() + '/submissions/detail/' + str(interpret_id) + '/check/'
        headers = _browser_headers('/submissions/detail/' + str(interpret_id) + '/check/',
                                   self.cookie_raw)
        for _ in range(20):
            time.sleep(1)
            req = urllib.request.Request(url, headers=headers)
            try:
                resp = _urlopen_retry(req, timeout=10, tries=3)
            except urllib.error.HTTPError as e:
                raise Exception(_http_error_text(e, 'Run Code check'))
            data = json.loads(resp.read())
            state = data.get('state', '')
            if state == 'SUCCESS':
                return data
        raise Exception('Interpret timed out')

    # ── 官方题解 ──

    def _find_official_solution(self, title_slug):
        """找官方题解。

        cn：questionSolutionArticles 里挑 byLeetcode 的那篇，再按 slug 取正文。
        com：没有 questionSolutionArticles（会 400: Cannot query field），
             但 question.solution 直接就是官方 editorial，而且一次就带回 content。
        """
        if _site() != 'cn':
            query = '''
            query questionSolution($titleSlug: String!) {
              question(titleSlug: $titleSlug) {
                solution {
                  id
                  title
                  slug
                  content
                }
              }
            }
            '''
            data = self._graphql(query, {'titleSlug': title_slug})
            sol = ((data.get('question') or {}).get('solution')) or {}
            if not (sol.get('slug') or sol.get('title') or sol.get('content')):
                return None
            return {
                'title': sol.get('title') or title_slug,
                'slug': sol.get('slug') or '',
                'byLeetcode': True,
                'topic': None,
                'content': sol.get('content') or '',
            }

        query = '''
        query questionSolutionArticles($questionSlug: String!, $skip: Int, $first: Int, $orderBy: SolutionArticleOrderBy) {
          questionSolutionArticles(questionSlug: $questionSlug, skip: $skip, first: $first, orderBy: $orderBy) {
            totalNum
            edges {
              node {
                title
                slug
                byLeetcode
                topic { id }
              }
            }
          }
        }
        '''
        first = 20
        skip = 0
        while skip < 200:
            data = self._graphql(query, {
                'questionSlug': title_slug,
                'skip': skip,
                'first': first,
                'orderBy': 'DEFAULT',
            })
            ps = data.get('questionSolutionArticles') or {}
            edges = ps.get('edges') or []
            for e in edges:
                node = e.get('node') or {}
                slug = node.get('slug') or ''
                if node.get('byLeetcode') or 'by-leetcode-solution' in slug:
                    return node
            total = ps.get('totalNum') or 0
            if skip + first >= total or not edges:
                break
            skip += first
        return None

    def _get_solution_detail(self, solution_slug):
        query = '''
        query solutionArticle($slug: String!) {
          solutionArticle(slug: $slug) {
            title
            content
            videosInfo {
              videoId
              coverUrl
              duration
            }
          }
        }
        '''
        data = self._graphql(query, {'slug': solution_slug})
        return data.get('solutionArticle') or {}

    def fetch_official_solution(self, title_slug, working_dir=None):
        if working_dir is None:
            working_dir = _working_dir()
        article = self._find_official_solution(title_slug)
        if not article:
            raise ValueError('No official solution found for: ' + title_slug)
        if article.get('content'):
            # com：正文跟着 question.solution 一起回来了，不用再查一次
            detail = {}
            raw = article['content']
            # com 的 editorial 混着 [TOC] 和 <iframe> 视频块，先规整成人能读的样子
            raw = re.sub(r'\[TOC\]\s*', '', raw)
            raw = re.sub(r'^\s*</?div[^>]*>\s*$', '', raw, flags=re.M)
            raw = re.sub(r'<iframe[^>]*src="([^"]+)"[^>]*>\s*</iframe>', r'[视频 / Video](\1)', raw)
        else:
            detail = self._get_solution_detail(article.get('slug') or '')
            raw = detail.get('content')
        content = _clean_solution_markdown(raw, detail.get('videosInfo'))
        if not content.strip():
            content = '_（题解内容为空）_'
        img_dir = _explanation_images_dir(title_slug)
        img_ref = os.path.relpath(img_dir, working_dir).replace('\\', '/')
        content = _download_markdown_images(content, img_dir, img_ref)
        # 原文链接
        url = _base_url() + '/problems/' + title_slug + '/solutions/'
        topic = article.get('topic')
        topic_id = topic.get('id') if isinstance(topic, dict) else None
        slug = article.get('slug') or ''
        # com 的 solution.slug 就等于题目 slug，拼出来会 404，所以 com 只给到题解列表页
        if _site() == 'cn':
            if topic_id:
                url += str(topic_id) + '/'
            if slug:
                url += slug + '/'
        os.makedirs(working_dir, exist_ok=True)
        md_path = os.path.join(working_dir, title_slug + '_explanation.md')
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write('# ' + (article.get('title') or title_slug) + '（官方题解）\n\n')
            f.write('> 原文：' + url + '\n\n')
            f.write(content)
        return md_path

    # ── 题集（学习计划）──

    def list_study_plans(self):
        """列出全部学习计划（题集），返回 [{slug, name, questionNum, premiumOnly}]。带缓存。"""
        cache_path = _study_plans_cache_path()
        if _cache_is_fresh(cache_path):
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    return json.load(f).get('plans', [])
            except Exception:
                pass
        plans = self._fetch_study_plans()
        os.makedirs(_cache_dir(), exist_ok=True)
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump({'timestamp': time.time(), 'plans': plans}, f, ensure_ascii=False)
        return plans

    def _fetch_study_plans(self):
        catalogs_data = self._graphql(
            'query { studyPlanV2Catalogs { slug } }')
        catalogs = catalogs_data.get('studyPlanV2Catalogs') or []
        plans = []
        for cat in catalogs:
            cat_slug = cat.get('slug') or ''
            if not cat_slug:
                continue
            offset = 0
            limit = 100
            while True:
                data = self._graphql('''
                    query studyPlansV2ByCatalog($catalogSlug: String!, $offset: Int!, $limit: Int!) {
                      studyPlansV2ByCatalog(catalogSlug: $catalogSlug, offset: $offset, limit: $limit) {
                        hasMore
                        studyPlans {
                          slug
                          questionNum
                          premiumOnly
                          name
                        }
                      }
                    }
                ''', {'catalogSlug': cat_slug, 'offset': offset, 'limit': limit})
                ps = data.get('studyPlansV2ByCatalog') or {}
                for p in (ps.get('studyPlans') or []):
                    plans.append({
                        'slug': p.get('slug') or '',
                        'name': p.get('name') or '',
                        'questionNum': p.get('questionNum') or 0,
                        'premiumOnly': bool(p.get('premiumOnly')),
                    })
                if not ps.get('hasMore'):
                    break
                offset += limit
        return plans

    def get_study_plan_problems(self, plan_slug):
        """列出某个学习计划里的题目，返回 [{frontendQuestionId, title, titleSlug, difficulty}]。带缓存。"""
        cache_path = _study_plan_problems_cache_path()
        by_slug = {}
        if _cache_is_fresh(cache_path):
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    by_slug = json.load(f).get('by_slug', {}) or {}
            except Exception:
                by_slug = {}
        if plan_slug in by_slug:
            return by_slug[plan_slug]
        problems = self._fetch_study_plan_problems(plan_slug)
        by_slug[plan_slug] = problems
        os.makedirs(_cache_dir(), exist_ok=True)
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump({'timestamp': time.time(), 'by_slug': by_slug}, f, ensure_ascii=False)
        return problems

    def _fetch_study_plan_problems(self, plan_slug):
        query = '''
        query studyPlanDetail($slug: String!) {
          studyPlanV2Detail(planSlug: $slug) {
            name
            planSubGroups {
              questions {
                translatedTitle
                titleSlug
                title
                questionFrontendId
                difficulty
              }
            }
          }
        }
        '''
        data = self._graphql(query, {'slug': plan_slug})
        detail = data.get('studyPlanV2Detail') or {}
        problems = []
        for group in (detail.get('planSubGroups') or []):
            for q in (group.get('questions') or []):
                problems.append({
                    'frontendQuestionId': str(q.get('questionFrontendId', '')),
                    'title': q.get('translatedTitle') or q.get('title') or '',
                    'titleSlug': q.get('titleSlug') or '',
                    'difficulty': q.get('difficulty') or '',
                })
        return problems

    def refresh_study_plans_cache(self):
        """强制刷新题集列表缓存，并清空题集题目缓存。"""
        for p in (_study_plans_cache_path(), _study_plan_problems_cache_path()):
            if os.path.exists(p):
                os.remove(p)
        return self.list_study_plans()

    # ── 每日一题 ──

    def get_daily_question(self):
        """获取今日的每日一题，返回 {frontendQuestionId, titleSlug, title, difficulty}。

        两个站的字段名不一样：cn 是 todayRecord，com 是 activeDailyCodingChallengeQuestion
        （com 上查 todayRecord 会 400: Cannot query field）。
        """
        if _site() == 'cn':
            query = '''
            query questionOfToday {
              todayRecord {
                date
                question {
                  questionId
                  questionFrontendId
                  difficulty
                  title
                  translatedTitle
                  titleSlug
                  isPaidOnly
                }
              }
            }
            '''
            data = self._graphql(query)
            records = data.get('todayRecord') or []
            if not records:
                raise ValueError('No daily question found.')
            q = records[0].get('question') or {}
        else:
            query = '''
            query questionOfToday {
              activeDailyCodingChallengeQuestion {
                date
                question {
                  questionId
                  questionFrontendId
                  difficulty
                  title
                  titleSlug
                }
              }
            }
            '''
            data = self._graphql(query)
            q = (data.get('activeDailyCodingChallengeQuestion') or {}).get('question') or {}
            if not q:
                raise ValueError('No daily question found.')
        return {
            'frontendQuestionId': str(q.get('questionFrontendId', '')),
            'titleSlug': q.get('titleSlug') or '',
            'title': q.get('translatedTitle') or q.get('title') or '',
            'difficulty': q.get('difficulty') or '',
            'isPaidOnly': bool(q.get('isPaidOnly')),
        }


def _split_testcase_strings(example_testcases, meta_data_str):
    """将 exampleTestcases 按参数数量拆成多个测试用例字符串。"""
    if not example_testcases or not example_testcases.strip():
        return []
    lines = example_testcases.strip().split('\n')
    lines = [l.strip() for l in lines]
    meta = json.loads(meta_data_str) if meta_data_str else {}
    param_count = len(meta.get('params', [])) or 1
    tc_strings = []
    i = 0
    while i < len(lines):
        chunk = lines[i:i + param_count]
        tc_strings.append('\n'.join(chunk))
        i += param_count
    return tc_strings


# ── 链表 / 二叉树转换（LeetCode 格式）──

class ListNode:
    def __init__(self, val=0, next=None):
        self.val = val
        self.next = next

class TreeNode:
    def __init__(self, val=0, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right
class Node:
    def __init__(self, val=0, neighbors=None):
        self.val = val
        self.neighbors = neighbors if neighbors else []

def _build_graph(adj_list):
    if not adj_list: return None
    nodes = [Node(i + 1) for i in range(len(adj_list))]
    for i, nbrs in enumerate(adj_list):
        nodes[i].neighbors = [nodes[n - 1] for n in nbrs]
    return nodes[0] if nodes else None

def _annotation_base_type(ann):
    """把一个类型注解归一成 _from_json 认识的名字。

    Optional / Union 剥掉，List[X] 之类的容器转成 X[]，其余取类型名本身：
      List[int]        -> int[]
      List[ListNode]   -> ListNode[]
      Optional[TreeNode] -> TreeNode
      Union[int, None] -> int
      "Node"           -> Node
    """
    ann = (ann or '').strip().strip("'\"").strip()
    if not ann:
        return ''
    # 去掉最外层括号，如 (List[int])
    while ann.startswith('(') and ann.endswith(')'):
        ann = ann[1:-1].strip()
    wrapper = re.match(r'(\w+)\s*\[(.*)\]$', ann, re.S)
    if wrapper:
        head, inner = wrapper.group(1).lower(), wrapper.group(2).strip()
        if head == 'optional':
            return _annotation_base_type(inner)
        if head == 'union':
            parts = [p for p in inner.split(',') if p.strip().lower() not in ('none', 'nonetype')]
            return _annotation_base_type(parts[0]) if parts else ''
        if head in ('list', 'sequence', 'iterable'):
            base = _annotation_base_type(inner)
            return base + '[]' if base else ''
    m = re.search(r'[A-Za-z_]\w*', ann)
    return m.group(0) if m else ''


def _parse_signature_types(code):
    """从 Python 函数签名提取参数类型名列表，交给 _from_json 解释。"""
    types = []
    for line in code.split('\n'):
        stripped = line.strip()
        if stripped.startswith('def '):
            m = re.match(r'def\s+\w+\s*\((.*)\)', stripped)
            if m and ':' in m.group(1):
                params = m.group(1)
                for p in params.split(','):
                    p = p.strip()
                    if ':' in p:
                        name = _annotation_base_type(p.split(':', 1)[1])
                        if name:
                            types.append(name)
                break
    return types

def _from_json(val, ptype):
    """JSON 原始值 → ListNode/TreeNode/Node 对象。支持数组类型（如 ListNode[]）。"""
    if val is None:
        return None
    ptype = (ptype or '').lower()
    is_arr = ptype.endswith('[]')
    base = ptype[:-2] if is_arr else ptype
    if 'listnode' in base:
        return [_build_list(v) for v in val] if is_arr else _build_list(val)
    if 'treenode' in base:
        return [_build_tree(v) for v in val] if is_arr else _build_tree(val)
    if base == 'node' or 'graph' in base:
        return [_build_graph(v) for v in val] if is_arr else _build_graph(val)
    return val

def _to_json(val):
    """将 ListNode/TreeNode 转回 JSON 可序列化格式。空节点序列化为 []。"""
    if val is None:
        return []
    if isinstance(val, ListNode):
        result = []
        cur = val
        while cur:
            result.append(cur.val)
            cur = cur.next
        return result
    if isinstance(val, TreeNode):
        if val is None:
            return None
        result = []
        q = [val]
        while q:
            node = q.pop(0)
            if node:
                result.append(node.val)
                q.append(node.left)
                q.append(node.right)
            else:
                result.append(None)
        while result and result[-1] is None:
            result.pop()
        return result
    if isinstance(val, Node):
        return _node_to_adj(val)
    if isinstance(val, list):
        return [_to_json(v) for v in val]
    return val

def _node_to_adj(node):
    if node is None: return []
    adj = {}
    visited = set()
    stack = [node]
    while stack:
        cur = stack.pop()
        if id(cur) in visited: continue
        visited.add(id(cur))
        adj[cur.val] = [n.val for n in cur.neighbors]
        for n in cur.neighbors:
            if id(n) not in visited:
                stack.append(n)
    return [adj[i] for i in sorted(adj)]

def _build_list(arr):
    if not arr: return None
    nodes = [ListNode(v) for v in arr]
    for i in range(len(nodes) - 1):
        nodes[i].next = nodes[i + 1]
    return nodes[0]

def _build_tree(arr):
    if not arr or arr[0] is None: return None
    root = TreeNode(arr[0])
    q = [root]
    i = 1
    while q and i < len(arr):
        node = q.pop(0)
        if i < len(arr) and arr[i] is not None:
            node.left = TreeNode(arr[i])
            q.append(node.left)
        i += 1
        if i < len(arr) and arr[i] is not None:
            node.right = TreeNode(arr[i])
            q.append(node.right)
        i += 1
    return root


# ==================== 离线评测 ====================

def _insert_return_stubs(code, meta_data_str):
    """给空函数体插 return 桩，按 return 类型精确返回。"""
    if not meta_data_str:
        return code
    meta = json.loads(meta_data_str)
    ret_type = (meta.get('return', {}) or {}).get('type', '').lower()

    default = '0'
    if ret_type.endswith('[]') or 'list' in ret_type or 'array' in ret_type:
        if 'double' in ret_type or 'float' in ret_type:
            default = '[0.0]'
        elif 'string' in ret_type:
            default = '[""]'
        else:
            default = '[0]'
    elif 'double' in ret_type or 'float' in ret_type:
        default = '0.0'
    elif 'string' in ret_type:
        default = '""'
    elif 'boolean' in ret_type or 'bool' in ret_type:
        default = 'False'
    elif 'listnode' in ret_type or 'treenode' in ret_type or ret_type == 'node':
        default = 'None'

    lines = code.split('\n')
    result = []
    in_class = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('class '):
            in_class = True
        result.append(line)
        if stripped.startswith('def ') and stripped.endswith(':'):
            indent = len(line) - len(line.lstrip())
            # 只看有返回类型注解的函数（跳过 __init__ 等）
            if '->' not in stripped:
                continue
            next_line = lines[i + 1].strip() if i + 1 < len(lines) else ''
            if not next_line or next_line.startswith('def ') or next_line.startswith('class ') or next_line.startswith('#'):
                func_indent = ' ' * (indent + 4)
                result.append(func_indent + 'return ' + default)
    return '\n'.join(result)
def _parse_testcases(example_testcases, meta_data_str):
    if not example_testcases or not example_testcases.strip():
        return []

    meta = json.loads(meta_data_str) if meta_data_str else {}
    params = meta.get('params', [])
    param_count = len(params) or 1

    lines = example_testcases.strip().split('\n')
    lines = [l.strip() for l in lines if l.strip()]

    def _parse_value(raw, param_info):
        val = json.loads(raw)
        ptype = (param_info or {}).get('type', '')
        return _from_json(val, ptype)

    testcases = []
    i = 0
    while i < len(lines):
        args = []
        for j in range(param_count):
            if i + j < len(lines):
                try:
                    args.append(_parse_value(lines[i + j], params[j] if j < len(params) else {}))
                except Exception:
                    try:
                        args.append(ast.literal_eval(lines[i + j]))
                    except Exception:
                        args.append(lines[i + j])
            else:
                args.append(None)
        testcases.append(tuple(args))
        i += param_count
    return testcases


def _fmt_time(sec):
    """把秒数格式化成易读的运行时间。"""
    if sec < 0.001:
        return '%.2f µs' % (sec * 1e6)
    if sec < 1:
        return '%.2f ms' % (sec * 1e3)
    return '%.2f s' % sec


def _run_offline(code_str, testcases, func_name, filename='<string>', timeout=None):
    namespace = {
        'ListNode': ListNode, 'TreeNode': TreeNode, 'Node': Node,
        '_build_list': _build_list, '_build_tree': _build_tree, '_build_graph': _build_graph,
    }
    typed_code = code_str
    try:
        exec('from typing import *', namespace)
        exec(compile(typed_code, filename, 'exec'), namespace)
    except Exception as e:
        return [('', '', '', 'Compile/Exec Error:\n' + traceback.format_exc(), 0.0)]

    func = namespace.get(func_name)
    if func is None:
        # 找 Solution 类的实例方法
        for k, v in namespace.items():
            if isinstance(v, type) and hasattr(v, func_name):
                obj = v()
                func = getattr(obj, func_name)
                break
    if func is None:
        for v in namespace.values():
            if callable(v) and not getattr(v, '__name__', '').startswith('_'):
                name = getattr(v, '__name__', '')
                if name in ('_build_list', '_build_tree', func_name):
                    continue
                if isinstance(v, type):
                    continue
                func = v
                break
    if func is None:
        return [('', '', '', 'Function "' + func_name + '" not found in code.', 0.0)]

    results = []
    for args in testcases:
        input_repr = ', '.join(json.dumps(_to_json(a), default=str) for a in args)
        buf = io.StringIO()
        box = {}

        def run_one():
            try:
                with contextlib.redirect_stdout(buf):
                    box['output'] = func(*args)
            except BaseException:
                box['error'] = traceback.format_exc()

        th = threading.Thread(target=run_one, daemon=True)
        t0 = time.time()
        th.start()
        if timeout and timeout > 0:
            th.join(timeout)
        else:
            th.join()
        elapsed = time.time() - t0

        if th.is_alive():
            results.append((input_repr, '', buf.getvalue().strip(),
                            'Time Limit Exceeded (' + _fmt_time(timeout) + ')', elapsed))
            break
        elif 'error' in box:
            results.append((input_repr, '', buf.getvalue().strip(), box['error'], elapsed))
            break
        else:
            results.append((input_repr, json.dumps(_to_json(box['output'])), buf.getvalue().strip(), None, elapsed))
    return results


_OFFLINE_RUNNER = r'''"""LeetCode Tools offline judge runner. 由系统 Python 运行，超时会被 kill。"""
import sys, os, json, io, contextlib, traceback, time, threading, re, ast


class ListNode:
    def __init__(self, val=0, next=None):
        self.val = val
        self.next = next


class TreeNode:
    def __init__(self, val=0, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right


class Node:
    def __init__(self, val=0, neighbors=None):
        self.val = val
        self.neighbors = neighbors if neighbors else []


def _build_list(arr):
    if not arr:
        return None
    nodes = [ListNode(v) for v in arr]
    for i in range(len(nodes) - 1):
        nodes[i].next = nodes[i + 1]
    return nodes[0]


def _build_tree(arr):
    if not arr or arr[0] is None:
        return None
    root = TreeNode(arr[0])
    q = [root]
    i = 1
    while q and i < len(arr):
        node = q.pop(0)
        if i < len(arr) and arr[i] is not None:
            node.left = TreeNode(arr[i])
            q.append(node.left)
        i += 1
        if i < len(arr) and arr[i] is not None:
            node.right = TreeNode(arr[i])
            q.append(node.right)
        i += 1
    return root


def _build_graph(adj_list):
    if not adj_list:
        return None
    nodes = [Node(i + 1) for i in range(len(adj_list))]
    for i, nbrs in enumerate(adj_list):
        nodes[i].neighbors = [nodes[n - 1] for n in nbrs]
    return nodes[0] if nodes else None


def _from_json(val, ptype):
    if val is None:
        return None
    ptype = (ptype or '').lower()
    is_arr = ptype.endswith('[]')
    base = ptype[:-2] if is_arr else ptype
    if 'listnode' in base:
        return [_build_list(v) for v in val] if is_arr else _build_list(val)
    if 'treenode' in base:
        return [_build_tree(v) for v in val] if is_arr else _build_tree(val)
    if base == 'node' or 'graph' in base:
        return [_build_graph(v) for v in val] if is_arr else _build_graph(val)
    return val


def _to_json(val):
    if val is None:
        return []
    if isinstance(val, ListNode):
        result = []
        cur = val
        while cur:
            result.append(cur.val)
            cur = cur.next
        return result
    if isinstance(val, TreeNode):
        if val is None:
            return None
        result = []
        q = [val]
        while q:
            node = q.pop(0)
            if node:
                result.append(node.val)
                q.append(node.left)
                q.append(node.right)
            else:
                result.append(None)
        while result and result[-1] is None:
            result.pop()
        return result
    if isinstance(val, Node):
        adj = {}
        visited = set()
        stack = [val]
        while stack:
            cur = stack.pop()
            if id(cur) in visited:
                continue
            visited.add(id(cur))
            adj[cur.val] = [n.val for n in cur.neighbors]
            for n in cur.neighbors:
                if id(n) not in visited:
                    stack.append(n)
        return [adj[i] for i in sorted(adj)]
    if isinstance(val, list):
        return [_to_json(v) for v in val]
    return val


def _parse_testcases(example_testcases, meta_data_str):
    if not example_testcases or not example_testcases.strip():
        return []
    meta = json.loads(meta_data_str) if meta_data_str else {}
    params = meta.get('params', [])
    param_count = len(params) or 1
    lines = example_testcases.strip().split('\n')
    lines = [l.strip() for l in lines if l.strip()]

    def _parse_value(raw, param_info):
        val = json.loads(raw)
        ptype = (param_info or {}).get('type', '')
        return _from_json(val, ptype)

    testcases = []
    i = 0
    while i < len(lines):
        args = []
        for j in range(param_count):
            if i + j < len(lines):
                try:
                    args.append(_parse_value(lines[i + j], params[j] if j < len(params) else {}))
                except Exception:
                    try:
                        args.append(ast.literal_eval(lines[i + j]))
                    except Exception:
                        args.append(lines[i + j])
            else:
                args.append(None)
        testcases.append(tuple(args))
        i += param_count
    return testcases


def _returns_none(code, func_name, ret_type=''):
    """判断目标函数是否声明了 `-> None`（即原地修改、无返回值的函数）。"""
    if func_name:
        m = re.search(r'def\s+' + re.escape(func_name) + r'\s*\([\s\S]*?\)\s*->\s*([^\s:]+)', code)
        if m:
            return m.group(1).strip().strip('\'"').lower() in ('none', 'nonetype')
    return ret_type.strip().lower() in ('none', 'nonetype')


def _find_func(namespace, func_name, injected=None):
    """在用户代码里找目标函数。

    injected 是 runner 预置的 name -> 对象（ListNode / typing 导出等），必须跳过它们：
    typing 里的 Text 就是 str，而 str 有 .partition / .count 等方法，否则
    _find_func(ns, 'partition') 会返回空字符串的 str.partition，
    用户自己写的 Solution.partition 永远轮不到，结果就是 ["", "", ""]。
    判断用「值是不是注入的那个对象」，这样用户在代码里重新定义同名顶层函数也算用户的。
    """
    injected = injected or {}

    def _from_user(name, obj):
        return name not in injected or injected[name] is not obj

    func = namespace.get(func_name)
    if func is not None and _from_user(func_name, func):
        return func
    for name, v in namespace.items():
        if not _from_user(name, v):
            continue
        if isinstance(v, type) and hasattr(v, func_name):
            return getattr(v(), func_name)
    for name, v in namespace.items():
        if not _from_user(name, v):
            continue
        if callable(v) and not getattr(v, '__name__', '').startswith('_'):
            fname = getattr(v, '__name__', '')
            if fname in ('_build_list', '_build_tree', '_build_graph', func_name):
                continue
            if isinstance(v, type):
                continue
            return v
    return None


def _emit(obj):
    sys.__stdout__.write(json.dumps(obj, ensure_ascii=False) + '\n')
    sys.__stdout__.flush()


def _user_error_text():
    # 只保留用户代码自己的堆栈帧，过滤掉 runner 自身（offline_runner.py）的帧，
    # 这样报错会直接指向用户文件的行号，而不是 <solution> / offline_runner.py。
    exc_type, exc_value, tb = sys.exc_info()
    te = traceback.TracebackException(exc_type, exc_value, tb)
    runner_file = os.path.realpath(__file__)
    kept = [f for f in te.stack if os.path.realpath(f.filename) != runner_file]
    te.stack = traceback.StackSummary.from_list(kept)
    return ''.join(te.format())


def main():
    payload = json.load(sys.stdin)
    code = payload.get('code', '')
    func_name = payload.get('func_name', '')
    timeout = payload.get('timeout') or 0
    mode = payload.get('mode', 'raw')
    try:
        meta_obj = json.loads(payload.get('meta_str') or '{}')
    except Exception:
        meta_obj = {}
    ret_type = ((meta_obj.get('return') or {}).get('type') or '').lower()
    node_ret = 'listnode' in ret_type or 'treenode' in ret_type
    # `-> None` 说明函数是原地修改的，返回值为 None；此时改对比第一个入参。
    in_place = _returns_none(code, func_name, ret_type)

    namespace = {
        'ListNode': ListNode, 'TreeNode': TreeNode, 'Node': Node,
        '_build_list': _build_list, '_build_tree': _build_tree, '_build_graph': _build_graph,
    }
    filename = payload.get('filename') or '<solution>'
    injected = dict(namespace)
    try:
        exec('from typing import *', namespace)
        injected.update(namespace)
        exec(compile(code, filename, 'exec'), namespace)
    except Exception:
        _emit({'error': 'Compile/Exec Error:\n' + _user_error_text()})
        return

    func = _find_func(namespace, func_name, injected)
    if func is None:
        _emit({'error': 'Function "' + func_name + '" not found in code.'})
        return

    if mode == 'example':
        testcases = _parse_testcases(payload.get('example', ''), payload.get('meta_str', '{}'))
    else:
        raw_tc = payload.get('raw_tc', [])
        types = payload.get('types', [])
        testcases = []
        for tc in raw_tc:
            args = []
            for j, v in enumerate(tc):
                ptype = types[j] if j < len(types) else ''
                args.append(_from_json(v, ptype))
            testcases.append(tuple(args))

    results = []
    for args in testcases:
        input_repr = ', '.join(json.dumps(_to_json(a), default=str) for a in args)
        buf = io.StringIO()
        box = {}

        def run_one():
            try:
                with contextlib.redirect_stdout(buf):
                    box['output'] = func(*args)
            except BaseException:
                box['error'] = _user_error_text()

        th = threading.Thread(target=run_one, daemon=True)
        t0 = time.time()
        th.start()
        if timeout and timeout > 0:
            th.join(timeout)
        else:
            th.join()
        elapsed = time.time() - t0

        if th.is_alive():
            results.append({'input': input_repr, 'output': '', 'stdout': buf.getvalue(),
                            'error': 'Time Limit Exceeded', 'elapsed': elapsed})
            break
        elif 'error' in box:
            results.append({'input': input_repr, 'output': '', 'stdout': buf.getvalue(),
                            'error': box['error'], 'elapsed': elapsed})
            break
        else:
            out = box['output']
            if in_place and args:
                # 原地函数：返回值是 None，真正的结果是被改写的第一个入参。
                out = args[0]
            elif out is None and node_ret:
                # 返回节点类型的函数返回了 None（空链表 / 空树 / 空图），序列化成 []
                out = []
            # 直接放真正的值，让外层 envelope 一次性 JSON 序列化。
            # 不能在这里 json.dumps：那样字符串会变成 '"abc"'、布尔会变成 'true'，
            # 落进 JSON 里就成了字符串，跟 _out.json 里的裸值类型对不上。
            results.append({'input': input_repr, 'output': _to_json(out),
                            'stdout': buf.getvalue(), 'error': None, 'elapsed': elapsed})

    _emit({'results': results})


if __name__ == '__main__':
    main()
'''


def _offline_runner_path():
    return os.path.join(_cache_dir(), 'offline_runner.py')


def _ensure_offline_runner():
    path = _offline_runner_path()
    os.makedirs(_cache_dir(), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(_OFFLINE_RUNNER)
    return path


def _run_offline_subprocess(code, raw_tc, types, func_name, timeout, example='', meta_str='{}', mode='raw', filename=None):
    """在子进程里跑离线判题（可 kill 死循环）。返回 runner 的逐条结果 dict 列表。"""
    python_exe = _find_system_python()
    runner = _ensure_offline_runner()
    payload = {
        'code': code,
        'func_name': func_name,
        'timeout': timeout,
        'mode': mode,
        'raw_tc': raw_tc,
        'types': types,
        'example': example,
        'meta_str': meta_str,
        'filename': filename,
    }
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')

    safety = None
    if timeout and timeout > 0:
        safety = timeout * 50 + 15

    proc = None
    popen_kwargs = {}
    if os.name == 'nt':
        popen_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(
            [python_exe, runner],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            **popen_kwargs
        )
        out, err = proc.communicate(body, timeout=safety)
    except subprocess.TimeoutExpired:
        if proc is not None:
            proc.kill()
            proc.communicate()
        raise Exception('Offline judge timed out (killed).')
    except Exception as e:
        raise Exception('Failed to run offline judge:\n' + str(e))

    if proc.returncode != 0:
        raise Exception('Offline judge failed:\n' + (err.decode('utf-8', 'replace') if err else ''))

    try:
        data = json.loads(out.decode('utf-8', 'replace'))
    except Exception:
        raise Exception('Offline judge returned invalid output.')

    if data.get('error'):
        raise Exception(data['error'])

    # runner 吐的本来就是 dict（input / output / stdout / error / elapsed），直接透传；
    # 离线 / 在线的排版统一由 _format_judge_panel 负责。
    return data.get('results', [])


# ==================== Sublime 命令 ====================

def _show_output(window, name, text):
    panel = window.create_output_panel(name)
    panel.settings().set('auto_indent', False)
    panel.settings().set('word_wrap', True)
    panel.run_command('select_all')
    panel.run_command('right_delete')
    panel.run_command('insert', {'characters': text})
    window.run_command('show_panel', {'panel': 'output.' + name})


def _run_in_thread(window, target, **kwargs):
    result = [None]
    error = [None]

    def worker():
        try:
            result[0] = target(window)
        except Exception as e:
            error[0] = e

    def check():
        if thread.is_alive():
            sublime.set_timeout(check, 200)
        else:
            if error[0]:
                sublime.error_message('LeetCodeTools Error:\n' + str(error[0]))
            elif '_on_done' in kwargs:
                kwargs['_on_done'](window, result[0])

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    sublime.set_timeout(check, 200)


# ─── Login ───

class LeetcodeLoginCommand(sublime_plugin.WindowCommand):
    def run(self):
        base = _base_url()
        us_note = ''
        if _site() != 'cn':
            us_note = (
                '⚠ leetcode.com 建议粘「整条 Cookie」：Cloudflare 的人机挑战凭证 cf_clearance（还有 __cf_bm）\n'
                '   只存在于这一整条里。只贴 LEETCODE_SESSION 的话，搜题 / 拉题 / 离线 Run 都能用，\n'
                '   但 Submit 和 Run Online 会被 403 挑战挡下。\n'
                '   如果还填了 browser_ua，记得和复制 Cookie 的那个浏览器保持一致。\n\n'
                'On leetcode.com paste the WHOLE cookie: the Cloudflare cf_clearance token only\n'
                'travels with the full header. Session-only cookies still search/fetch/run offline,\n'
                'but Submit and Run Online will hit a 403 challenge.\n\n')
        # 先弹提示、点确认之后再开浏览器：message_dialog 是非阻塞的，
        # 先开浏览器的话说明会被浏览器盖住，用户根本没机会看。
        if not sublime.ok_cancel_dialog(
            'LeetCodeTools 登录 / Login —— ' + base + '\n\n'
            '中文：\n'
            '1. 在浏览器里登录 ' + base + '（页面能正常打开，说明已经过掉 Cloudflare）\n'
            '2. 按 F12 → 选「网络 / Network」标签 → 刷新一次页面\n'
            '3. 点左侧任意一个 ' + base + ' 的请求\n'
            '4. 右侧找「请求标头 / Request Headers」→ 找到 Cookie 这一行\n'
            '5. 复制整条值，随便用哪种办法：\n'
            '   · 右键那个值 → Copy value（最省事）\n'
            '   · 在值上连点三下选中整段 → Ctrl+C（别按 Ctrl+A，那会选中整个面板）\n'
            '   · 或者右键左侧请求 → Copy → Copy as cURL，整段粘过来也行（插件会自己抠）\n'
            '   （一定要从「请求标头」复制，不要从响应头的 Set-Cookie 复制）\n'
            '6. 回到 Sublime，粘进输入框，回车\n\n'
            '（leetcode.cn 只贴 LEETCODE_SESSION 的值也能用；整条粘更好。）\n\n'
            + us_note +
            'English:\n'
            '1. Log in to ' + base + ' in your browser.\n'
            '2. F12 → Network tab → reload the page.\n'
            '3. Click any request to ' + base + '.\n'
            '4. Request Headers → find the Cookie line.\n'
            '5. Copy the whole value: right-click it → Copy value, or triple-click it → Ctrl+C,\n'
            '   or right-click the request → Copy → Copy as cURL and paste that whole block\n'
            '   (the plugin extracts the cookie from it).\n'
            '   Copy from Request Headers, never from a Set-Cookie response header.\n'
            '6. Paste it into the input box and press Enter.',
            '打开浏览器 / Open browser'):
            return
        try:
            webbrowser.open(base + '/')
        except Exception as e:
            sublime.error_message('Failed to open browser:\n' + str(e))
            return
        self.window.show_input_panel(
            '粘贴 Cookie（整条 Cookie / Copy as cURL 整段都可以）:',
            '', self._on_cookie, None,
            lambda: sublime.status_message('LeetCodeTools: Login cancelled'))

    def _on_cookie(self, text):
        try:
            data = _save_cookie_from_text(text)
        except Exception:
            sublime.error_message(
                'LeetCodeTools: 没识别出登录凭证。\n\n'
                '请从 ' + _base_url() + ' 的「F12 → Network → Request Headers → Cookie」复制整条值\n'
                '（右键值 → Copy value，或右键请求 → Copy as cURL 整段粘贴），\n'
                '里面要有 LEETCODE_SESSION=很长一串。然后再运行一次 Login。\n\n'
                'LeetCodeTools: could not find a login cookie.\n'
                'Copy the whole Cookie value from ' + _base_url() + ' (F12 → Network → Request Headers):\n'
                'right-click the value → Copy value, or right-click the request → Copy → Copy as cURL and\n'
                'paste that whole block. It must contain LEETCODE_SESSION=<a very long value>.\n'
                'Then run Login again.')
            return

        sublime.status_message('LeetCodeTools: Verifying cookie...')

        def work(window):
            return _validate_cookie(data)

        def done(window, ok):
            if not ok:
                sublime.error_message(
                    'LeetCodeTools: 没登录成功。\n\n'
                    '请确认：\n'
                    '1. 浏览器里已经登录 ' + _base_url() + '\n'
                    '2. 复制的是「Request Headers → Cookie」的整条值\n'
                    '   （不是 Cookie 列表里的单个值，也不是响应头的 Set-Cookie）\n'
                    '3. 里面确实有 LEETCODE_SESSION=\n\n'
                    '再运行一次 Login 重试。\n\n'
                    'LeetCodeTools: sign-in failed. Please check:\n'
                    '1. You are logged in to ' + _base_url() + ' in your browser.\n'
                    '2. You copied the whole "Request Headers → Cookie" value (not a single cookie from the\n'
                    '   Application panel, and not a Set-Cookie response header).\n'
                    '3. It really contains LEETCODE_SESSION=.\n\n'
                    'Run Login again to retry.')
                return
            warn = ''
            if _site() != 'cn':
                all_cookies = data.get('all') or {}
                # 只看 cf_clearance。__cf_bm 是 30 分钟一换的短期 bot cookie，
                # 请求里经常压根没有，缺了不代表提交会被拦，别误报。
                if not all_cookies.get('cf_clearance'):
                    warn = ('\n\n⚠ 这条 Cookie 里没有 cf_clearance，'
                            '而它正是 Cloudflare 认你已经过了人机挑战的凭证。\n'
                            '搜题、拉题、离线 Run 都正常；但 Submit / Run Online 很可能被 Cloudflare 挑战拦下。\n'
                            '想提交的话：在浏览器里打开一道题并提交或运行一次（过掉人机挑战），\n'
                            '再从同一个浏览器复制整条 Cookie 重新 Login（并让 browser_ua 与之一致）。\n\n'
                            '⚠ This cookie has no cf_clearance, which is the token Cloudflare issues once\n'
                            'you have passed its human check.\n'
                            'Search / fetch / offline Run still work, but Submit and Run Online will most\n'
                            'likely be blocked by a Cloudflare challenge.\n'
                            'To give it a chance: open a problem in the browser and submit or run once (to\n'
                            'pass the challenge), then copy the whole Cookie from that same browser and run\n'
                            'Login again (and set browser_ua to match that browser).')
            sublime.status_message('LeetCodeTools: Login successful!')
            if warn:
                sublime.message_dialog('LeetCodeTools: Login successful!' + warn)

        _run_in_thread(self.window, work, _on_done=done)


# ─── Search ───

class LeetcodeSearchCommand(sublime_plugin.WindowCommand):
    """直接开顶部的 quick panel；输入过滤和选择都在同一个面板里完成。

    旧版是先在窗口底部 show_input_panel 输入关键词、再开 quick panel，多一步。
    现在一次把全部题目（本地缓存）丢进 quick panel，用面板自带的过滤框按题号或标题筛，
    选中即拉题并打开。
    """

    def run(self):
        sublime.status_message('LeetCode Tools: Loading problem list...')

        def work(window):
            client = _build_client()
            return client._load_cache()

        def done(window, problems):
            if not problems:
                sublime.message_dialog(
                    'Problem list is empty. Run "LeetCodeTools: Update" first.')
                return
            items = []
            for p in problems:
                fid = p.get('frontendQuestionId', '?')
                items.append(['#' + str(fid) + '  ' + _problem_title(p),
                              str(p.get('difficulty') or '?')])

            def on_select(idx):
                if idx >= 0:
                    self._fetch_and_open(problems[idx].get('frontendQuestionId'))

            self.window.show_quick_panel(items, on_select)

        _run_in_thread(self.window, work, _on_done=done)

    def _fetch_and_open(self, fid):
        def work(window):
            sublime.status_message('LeetCode Tools: Fetching #' + str(fid) + '...')
            client = _build_client()
            return client.fetch_problem(fid)

        def done(window, result):
            if result:
                for k in ('md_path', 'code_path'):
                    if result.get(k):
                        window.open_file(result[k])
                sublime.status_message('LeetCodeTools: #' + str(fid) + ' fetched')

        _run_in_thread(self.window, work, _on_done=done)


# ─── Reload Problem / Edit Testcases ───

class LeetcodeReloadCommand(sublime_plugin.TextCommand):
    """强制把当前这道题重拉一遍：题面覆盖、测试用例重抓（含"空返回值骗期望值"那一步）。

    你自己写的那份代码不会被换掉 —— 只有题面、元数据和用例是强制覆盖的。
    """

    def run(self, edit):
        fp = self.view.file_name()
        if not fp:
            sublime.error_message('Save the file first.')
            return
        window = self.view.window()
        slug = _detect_slug(fp)
        ext = os.path.splitext(fp)[1].lstrip('.')
        lang = EXT_LANG.get(ext) or _default_lang()
        try:
            # 取缓冲区里的内容（含未保存的修改），等会儿原样放回去
            mine = self.view.substr(sublime.Region(0, self.view.size()))
        except Exception:
            mine = None

        def work(window):
            sublime.status_message('LeetCodeTools: Reloading ' + slug + '...')
            result = _build_client().fetch_problem(slug, lang=lang, force=True)
            if mine is not None:
                try:
                    with open(fp, 'w', encoding='utf-8') as f:
                        f.write(mine)
                except Exception:
                    pass
            return result

        def done(window, result):
            for k in ('md_path', 'code_path'):
                if result.get(k):
                    window.open_file(result[k])
            sublime.status_message('LeetCodeTools: ' + slug + ' reloaded')

        _run_in_thread(window, work, _on_done=done)


class LeetcodeEditTestcasesCommand(sublime_plugin.TextCommand):
    """打开当前题的 `_in.json` / `_out.json`（用例输入 / 期望输出）直接改。"""

    def run(self, edit):
        fp = self.view.file_name()
        if not fp:
            sublime.error_message('Save the file first.')
            return
        slug = _detect_slug(fp)
        paths = [p for p in (_problem_in_path(slug), _problem_out_path(slug)) if os.path.exists(p)]
        if not paths:
            sublime.error_message(
                'No testcases for ' + slug + ' yet.\n'
                'Run "LeetCodeTools: Reload Problem" first to generate them.')
            return
        window = self.view.window()
        for p in paths:
            window.open_file(p)
        sublime.status_message('LeetCodeTools: opened ' + str(len(paths)) + ' testcase file(s)')


# ─── Run (Offline Judge / LeetCode Run Code) ───

class LeetcodeRunCommand(sublime_plugin.TextCommand):
    """判题入口：默认在本地离线跑（online = False）。

    两个命令共用这一个 run()，LeetcodeRunOnlineCommand 只是把 online / banner 翻过来
    （皮套），结果都写进同一个 'leetcode_run' 面板，方便两条路对照着看。
    """

    online = False
    banner = 'LeetCode Offline Judge'
    panel = 'leetcode_run'

    def run(self, edit):
        fp = self.view.file_name()
        if not fp:
            sublime.error_message('Save the file first.')
            return
        ext = os.path.splitext(fp)[1].lstrip('.')
        if ext not in EXT_LANG:
            sublime.error_message('Unsupported file type: .' + ext)
            return
        base = fp[:-(len(ext) + 1)]
        json_path = _problem_json_path(os.path.basename(base))
        if not os.path.exists(json_path):
            sublime.error_message('Test file not found:\n' + json_path)
            return
        window = self.view.window()

        def work(window):
            base = fp[:-(len(ext) + 1)]
            slug = os.path.basename(base)
            json_path = _problem_json_path(slug)
            if not os.path.exists(json_path):
                raise FileNotFoundError('Test file not found: ' + json_path)
            with open(json_path, 'r', encoding='utf-8') as f:
                test_data = json.load(f)
            meta = test_data.get('metaData', '{}')
            if isinstance(meta, str):
                meta_obj = json.loads(meta)
            else:
                meta_obj = meta
            func_name = meta_obj.get('name', '')
            with open(fp, 'r', encoding='utf-8') as f:
                code = f.read()
            example = test_data.get('exampleTestcases', '')
            no_expect_hint = ''
            if self.online:
                # 网页端 Ctrl+'：代码发给 LeetCode，用官方示例跑一遍
                results = _run_online(code, slug, test_data, meta_obj, example, ext)
                expected_outputs = None
            else:
                # manual 题用函数签名类型，否则用 metaData 类型
                manual = meta_obj.get('manual', False)
                sig_types = _parse_signature_types(code)
                params = meta_obj.get('params', [])
                timeout = _run_timeout()
                # 优先用 _in.json（含手动追加的失败用例）
                in_path = _problem_in_path(slug)
                if os.path.exists(in_path):
                    with open(in_path, encoding='utf-8') as f:
                        raw_tc = json.load(f)
                    max_args = max((len(tc) for tc in raw_tc), default=0)
                    types = []
                    for j in range(max_args):
                        if manual and j < len(sig_types):
                            types.append(sig_types[j])
                        else:
                            types.append(params[j].get('type', '') if j < len(params) else '')
                    results = _run_offline_subprocess(
                        code, raw_tc, types, func_name, timeout, mode='raw', filename=fp)
                else:
                    results = _run_offline_subprocess(
                        code, [], [], func_name, timeout,
                        example=example, meta_str=json.dumps(meta_obj), mode='example', filename=fp)
                expected_outputs = _read_expected_outputs(slug)
                if not any(v is not None and v != '' for v in (expected_outputs or [])):
                    no_expect_hint = _no_expected_hint()
            _, fname = os.path.split(fp)
            return _format_judge_panel(self.banner, fname, results, expected_outputs, no_expect_hint)

        def done(window, text):
            _show_output(window, self.panel, text)

        _run_in_thread(window, work, _on_done=done)


class LeetcodeRunOnlineCommand(LeetcodeRunCommand):
    """LeetCodeTools: Run Online —— 皮套：只把旗翻过来，逻辑全在基类。"""

    online = True
    banner = 'LeetCode Online Judge'


def _values_match(a, b):
    """按值判等：两边本身的值相等才算过。

    runner 的 output 与 _out.json 里存的都是裸值（JSON 原生类型），所以直接比就行。
    不比 str() 渲染出来的样子，也不做任何跨类型折算——字符串 'true' 和布尔 True
    是两个不同的值，不能算相等。
    """
    return a == b


def _display_val(val):
    """输出面板里展示一个值：字符串带引号，布尔显示 true/false 而不是 True/False。"""
    try:
        return json.dumps(val, ensure_ascii=False)
    except Exception:
        return str(val)


# ─── 判题结果排版（离线 / 在线共用）───

_MISSING = object()


def _format_judge_panel(banner, fname, results, expected_outputs=None, hint=''):
    """把判题结果渲染成输出面板文本，离线和在线共用同一套排版。

    results 每条是一个 dict：input / output / stdout / error / elapsed；
    可选 expected（这一条的期望值）和 ok（服务端给的判定，给了就优先用它，不再本地按值比）。
    expected_outputs 是离线判题用的 _out.json（按下标对应 results），None 表示没有期望值文件。
    hint 是"没有期望值"时面板里显示的那句原因 / 下一步。
    """
    lines = ['=' * 50, '  ' + banner + ' -- ' + fname, '=' * 50, '']
    passed = 0
    judged = 0
    total_time = 0.0
    has_time = False
    for i, r in enumerate(results, 1):
        elapsed = r.get('elapsed')
        if elapsed is not None:
            total_time += elapsed
            has_time = True
        stdout = r.get('stdout') or ''
        err = r.get('error')
        lines.append('Test ' + str(i) + ':')
        if err:
            # 出错的行和离线判题一样：只给 INPUT + ERROR（没有输入就只给 ERROR，
            # 比如编译错误那种整段跑不起来的）
            if r.get('input'):
                lines.append('  INPUT  ' + str(r['input']))
            lines.append('  ERROR  ' + str(err))
            if stdout:
                lines.append('  STDOUT\n' + stdout)
            lines.append('')
            continue
        lines.append('  INPUT  ' + str(r.get('input', '')))
        if stdout:
            lines.append('  STDOUT\n' + stdout)
        lines.append('  OUTPUT ' + _display_val(r.get('output')))
        if elapsed is not None:
            lines.append('  TIME   ' + _fmt_time(elapsed))
        exp = r.get('expected', _MISSING)
        if exp is _MISSING and expected_outputs and i <= len(expected_outputs):
            exp = expected_outputs[i - 1]
        # 空串 / None 一律当成"没有期望值"：.com 上拿不到 Run Code 时 _out.json 就是一片空值，
        # 那种情况下显示 EXPECT ""  FAIL 是骗人的（其实什么都没比）。
        if exp is not _MISSING and exp is not None and exp != '':
            judged += 1
            ok = r.get('ok')
            match = ok if ok is not None else _values_match(r.get('output'), exp)
            lines.append('  EXPECT ' + _display_val(exp) + ('  OK' if match else '  FAIL'))
            if match:
                passed += 1
        lines.append('')
    missing = (len(results) - len(expected_outputs)) if expected_outputs is not None else 0
    if missing > 0:
        lines.append('NOTE: _in.json has ' + str(len(results)) + ' case(s) but _out.json only has '
                     + str(len(expected_outputs)) + ' expected value(s), so the last '
                     + str(missing) + ' case(s) cannot be judged. Submit a WA or re-fetch to fill them in.')
        lines.append('')
    if judged:
        lines.append(str(passed) + '/' + str(len(results)) + ' passed.')
    else:
        lines.append(str(len(results)) + ' case(s) run — no expected outputs, nothing compared.')
        if hint:
            lines.append('  ' + hint)
    if has_time:
        lines.append('Total time: ' + _fmt_time(total_time))
    lines.append('=' * 50)
    return '\n'.join(lines)


# ─── 在线跑（网页端 Ctrl+'）───

def _answer_value(raw):
    """LeetCode 的 answer 元素是 JSON 字符串（'[0,1]' / '"abc"'），解析成裸值；解析不了原样返回。"""
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return raw


def _answer_list(raw, count):
    """把 code_answer / expected_code_answer 解析成裸值列表。

    这两个数组末尾会多带一个 '' 哨兵（有时又不带），所以按 compare_result 的长度截断；
    没有 compare_result 时退化成去掉末尾的哨兵。
    """
    if not isinstance(raw, list):
        return []
    vals = [_answer_value(v) for v in raw]
    if count and len(vals) > count:
        return vals[:count]
    while vals and (vals[-1] == '' or vals[-1] is None):
        vals.pop()
    return vals


def _run_online(code, slug, test_data, meta_obj, example, ext):
    """把代码发给 LeetCode 跑官方示例（等价网页端 Ctrl+'），返回和离线判题同构的结果列表。

    判定只用服务端返回的 compare_result（逐条 '1' / '0'）：那是网页端的判定，比本地按值
    严格比较更权威（subsets 这种答案顺序不唯一的题，本地会误判 FAIL）。
    Run Code 的 status_msg 不可信 —— 答案是错的时候它照样返回 "Accepted"。
    """
    if not example or not example.strip():
        raise Exception('No example testcases in the local JSON; re-fetch this problem first.')
    lang = EXT_LANG.get(ext, ext)
    client = _build_client()
    sid = client.interpret_solution(slug, test_data.get('questionId', ''), lang, code, example)
    data = client._check_interpret(sid)

    # 编译没过（C++ / Java 等）：用例输入没有意义，也不假装是某一条用例失败
    compile_err = data.get('full_compile_error') or data.get('compile_error')
    if compile_err:
        return [{'input': '', 'output': None, 'stdout': '',
                 'error': 'Compile Error:\n' + str(compile_err).strip(), 'elapsed': None}]

    testcases = _parse_testcases(example, json.dumps(meta_obj))
    compare = str(data.get('compare_result') or '')
    answers = _answer_list(data.get('code_answer'), len(compare))
    expected = _answer_list(data.get('expected_code_answer'), len(compare))
    stdout_list = data.get('std_output_list')
    if not isinstance(stdout_list, list):
        stdout_list = [data.get('std_output') or '']
    # Python 的语法错误会被服务端算成 runtime_error，所以两种都收
    err_text = data.get('full_runtime_error') or data.get('runtime_error') or ''

    def _stdout(i):
        return stdout_list[i] if i < len(stdout_list) else ''

    def _input_repr(args):
        return ', '.join(json.dumps(_to_json(a), default=str) for a in args)

    # 崩在第几条：第一条「没有答案」（缺条目或空串）的用例
    crash = None
    if err_text:
        for i in range(len(testcases)):
            if i >= len(answers) or answers[i] == '':
                crash = i
                break

    results = []
    for i in range(len(testcases) if crash is None else crash):
        row = {'input': _input_repr(testcases[i]),
               'output': answers[i] if i < len(answers) else None,
               'stdout': _stdout(i), 'error': None, 'elapsed': None}
        if i < len(compare):
            row['ok'] = compare[i] == '1'
        if i < len(expected):
            row['expected'] = expected[i]
        results.append(row)

    if err_text:
        if crash is None:
            # 每条都有答案却还报错（少见）：把错误挂在最后一条上，别把信息丢了
            if results:
                results[-1]['error'] = err_text
                results[-1]['output'] = None
                results[-1].pop('expected', None)
                results[-1].pop('ok', None)
            else:
                results.append({'input': '', 'output': None, 'stdout': '',
                                'error': err_text, 'elapsed': None})
        else:
            # 和离线判题一样：停在出错的那条
            results.append({'input': _input_repr(testcases[crash]), 'output': None,
                            'stdout': _stdout(crash), 'error': err_text, 'elapsed': None})

    if not results:
        results.append({'input': '', 'output': None, 'stdout': '',
                        'error': 'LeetCode returned no result.', 'elapsed': None})
    return results


def _read_case_list(path):
    """读一个用例/期望值数组文件；不存在或格式不对就返回空列表，不让 Run/Submit 崩掉。"""
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _case_key(tc):
    return json.dumps(tc, sort_keys=True, ensure_ascii=False)


def _append_failed_case(slug, last_testcase, expected_output):
    """把 Submit 失败的那条用例追加到本地 _in.json / _out.json。

    _in 和 _out 是按下标一一对应的两个数组，必须同时追加、且同一条用例只留一份。
    只有本地已经存在 _in.json（说明这题已经开了手动用例）才追加，
    免得把本来跑 exampleTestcases 的题切成 raw 模式。

    返回 (新追加了几条, 给 _out 补齐了几条期望值)。
    """
    in_path = _problem_in_path(slug)
    out_path = _problem_out_path(slug)
    if not os.path.exists(in_path):
        return (0, 0)

    meta = {}
    json_path = _problem_json_path(slug)
    if os.path.exists(json_path):
        try:
            with open(json_path, encoding='utf-8') as jf:
                meta = json.load(jf).get('metaData', '{}')
        except Exception:
            meta = {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}

    new_tc = _parse_testcases(last_testcase or '', json.dumps(meta))
    try:
        exp_val = json.loads(expected_output)
    except Exception:
        exp_val = expected_output

    tc_list = _read_case_list(in_path)
    exp_list = _read_case_list(out_path)
    index = {}
    for i, t in enumerate(tc_list):
        index.setdefault(_case_key(t), i)

    added = 0
    filled = 0
    for tc in new_tc:
        tc_json = [_to_json(v) for v in tc]
        key = _case_key(tc_json)
        i = index.get(key)
        if i is None:
            index[key] = len(tc_list)
            tc_list.append(tc_json)
            exp_list.append(exp_val)
            added += 1
        elif i >= len(exp_list):
            # 老版本 bug 只把用例写进了 _in、期望值没写进 _out。趁这次 Submit 把缺的补上，
            # 让两边下标重新对齐，否则 Run 里这些用例根本没有 EXPECT 行。
            exp_list.extend([None] * (i - len(exp_list)))
            exp_list.append(exp_val)
            filled += 1

    if added or filled:
        with open(in_path, 'w', encoding='utf-8') as f:
            json.dump(tc_list, f, ensure_ascii=False)
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(exp_list, f, ensure_ascii=False)
    return (added, filled)


# ─── Submit ───
class LeetcodeSubmitCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        fp = self.view.file_name()
        if not fp:
            sublime.error_message('Save the file first.')
            return
        ext = os.path.splitext(fp)[1].lstrip('.')
        if ext not in EXT_LANG:
            sublime.error_message('Unsupported file type: .' + ext)
            return
        window = self.view.window()

        def work(window):
            base = fp[:-(len(ext) + 1)]
            slug = os.path.basename(base)
            json_path = _problem_json_path(slug)
            if not os.path.exists(json_path):
                raise FileNotFoundError('Test file not found: ' + json_path)
            with open(json_path, 'r', encoding='utf-8') as f:
                test_data = json.load(f)
            title_slug = test_data.get('titleSlug', '')
            question_id = test_data.get('questionId', '')
            if not title_slug or not question_id:
                raise ValueError('Missing titleSlug or questionId in JSON.')
            lang_slug = EXT_LANG.get(ext, ext)
            study_plan_slug = test_data.get('study_plan_slug', '')
            with open(fp, 'r', encoding='utf-8') as f:
                code = f.read()
            client = _build_client()
            sid = client.submit_code(title_slug, question_id, lang_slug, code, study_plan_slug=study_plan_slug)
            result = None
            sublime.status_message('LeetCodeTools: Submitting...')
            result = client.check_submission(sid)
            sublime.status_message('LeetCodeTools: Done')
            return {'slug': title_slug, 'sid': sid, 'result': result}

        def done(window, data):
            r = data['result']
            lines = ['=' * 50, '  LeetCode Submit -- ' + data['slug'], '=' * 50, '']
            status = r.get('status_msg', 'Unknown')
            if status == 'Accepted':
                lines.append('Status:   Accepted \u2714\ufe0f')
                lines.append('Runtime:  ' + str(r.get('status_runtime', '?')) + '   Beat ' + str(round(r.get('runtime_percentile', 0), 1)) + '%')
                lines.append('Memory:   ' + str(r.get('status_memory', '?')) + '   Beat ' + str(round(r.get('memory_percentile', 0), 1)) + '%')
                lines.append('Passed:   ' + str(r.get('total_correct', '?')) + ' / ' + str(r.get('total_testcases', '?')))
                if r.get('std_output'):
                    lines.append('Stdout:')
                    lines.append(r['std_output'])
            elif r.get('compile_error') or r.get('full_compile_error'):
                lines.append('Status:   Compile Error \u274c')
                lines.append('Error:')
                lines.append(r.get('full_compile_error') or r.get('compile_error', ''))
            else:
                lines.append('Status:   ' + status + ' \u274c')
                lines.append('Passed:   ' + str(r.get('total_correct', '?')) + ' / ' + str(r.get('total_testcases', '?')))
                if r.get('last_testcase'):
                    lines.append('')
                    lines.append('Last Input:   ' + str(r['last_testcase']))
                if r.get('code_output'):
                    lines.append('Your Output:  ' + str(r['code_output']))
                if r.get('expected_output'):
                    lines.append('Expected:     ' + str(r['expected_output']))
                if r.get('std_output'):
                    lines.append('Stdout:')
                    lines.append(r['std_output'])
                # 追加失败的用例到本地 _in/_out（不依赖 std_output）
                if r.get('last_testcase') and r.get('expected_output'):
                    slug = data.get('slug') or os.path.basename(fp[:-(len(ext) + 1)])
                    try:
                        _append_failed_case(slug, r['last_testcase'], r['expected_output'])
                    except Exception as e:
                        lines.append('(append testcase failed: ' + str(e) + ')')
            lines.append('')
            lines.append('=' * 50)
            _show_output(window, 'leetcode_submit', '\n'.join(lines))

        _run_in_thread(window, work, _on_done=done)


# ─── Update Cache ───

class LeetcodeUpdateCommand(sublime_plugin.WindowCommand):
    def run(self):
        def work(window):
            client = _build_client()
            # 删旧缓存强制重建
            cache = _problem_list_cache_path()
            if os.path.exists(cache):
                os.remove(cache)
            problems = client._fetch_problem_list()
            plans = client.refresh_study_plans_cache()
            return {'problems': len(problems), 'plans': len(plans)}

        def done(window, result):
            sublime.status_message(
                'LeetCodeTools: Cache updated ('
                + str(result['problems']) + ' problems, '
                + str(result['plans']) + ' study plans)')

        sublime.status_message('LeetCodeTools: Updating cache...')
        _run_in_thread(self.window, work, _on_done=done)

# ─── Open in Browser ───

class LeetcodeOpenBrowserCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        fp = self.view.file_name()
        if not fp:
            sublime.error_message('Open a problem file first.')
            return
        slug = _detect_slug(fp)
        url = _base_url() + '/problems/' + slug + '/'
        try:
            webbrowser.open(url)
            sublime.status_message('LeetCodeTools: Opening ' + url)
        except Exception as e:
            sublime.error_message('LeetCodeTools: failed to open browser\n\n' + str(e))


# ─── Fetch Official Explanations ───

class LeetcodeFetchExplanationCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        fp = self.view.file_name()
        if not fp:
            sublime.error_message('Open a problem file first.')
            return
        slug = _detect_slug(fp)
        window = self.view.window()
        if not window:
            sublime.error_message('No window available.')
            return

        def work(window):
            client = _build_public_client()
            return client.fetch_official_solution(slug)

        def done(window, result):
            if result:
                window.open_file(result)
                sublime.status_message('LeetCodeTools: Official explanation fetched (' + slug + ')')

        sublime.status_message('LeetCodeTools: Fetching official explanation...')
        _run_in_thread(window, work, _on_done=done)


# ─── Select from Problem Set ───

class LeetcodeProblemSetCommand(sublime_plugin.WindowCommand):
    def run(self):
        sublime.status_message('LeetCode Tools: Loading problem sets...')

        def work(window):
            client = _build_client()
            return client.list_study_plans()

        def done(window, plans):
            plans = [p for p in plans if p.get('slug')]
            if not plans:
                sublime.message_dialog('No problem sets found.')
                return
            items = []
            for p in plans:
                label = p['name']
                if p.get('questionNum'):
                    label += '  (' + str(p['questionNum']) + ' 题)'
                if p.get('premiumOnly'):
                    label += '  [会员]'
                items.append([label, str(p.get('questionNum') or '')])

            def on_select(idx):
                if idx >= 0:
                    self._pick_problem(plans[idx]['slug'])

            self.window.show_quick_panel(items, on_select)

        _run_in_thread(self.window, work, _on_done=done)

    def _pick_problem(self, plan_slug):
        sublime.status_message('LeetCode Tools: Loading problems...')

        def work(window):
            client = _build_client()
            return client.get_study_plan_problems(plan_slug)

        def done(window, problems):
            if not problems:
                sublime.message_dialog('No problems in this set.')
                return
            items = []
            for p in problems:
                fid = p.get('frontendQuestionId', '?')
                items.append(['#' + str(fid) + '  ' + (p.get('title') or '?'),
                              str(p.get('difficulty') or '?')])

            def on_select(idx):
                if idx >= 0:
                    self._fetch_and_open(problems[idx]['frontendQuestionId'], plan_slug)

            self.window.show_quick_panel(items, on_select)

        _run_in_thread(self.window, work, _on_done=done)

    def _fetch_and_open(self, fid, plan_slug=None):
        def fetch_and_open(window):
            sublime.status_message('LeetCode Tools: Fetching #' + str(fid) + '...')
            client = _build_client()
            return client.fetch_problem(fid, study_plan_slug=plan_slug)

        def done_fetch(window, result):
            if result:
                for k in ('md_path', 'code_path'):
                    if result.get(k):
                        window.open_file(result[k])
                sublime.status_message('LeetCodeTools: #' + str(fid) + ' fetched')

        _run_in_thread(self.window, fetch_and_open, _on_done=done_fetch)


# ─── Daily Question ───

class LeetcodeDailyCommand(sublime_plugin.WindowCommand):
    def run(self):
        sublime.status_message('LeetCode Tools: Loading daily question...')

        def work(window):
            client = _build_client()
            q = client.get_daily_question()
            fid = q.get('frontendQuestionId')
            if not fid:
                raise ValueError('No daily question found.')
            sublime.status_message('LeetCode Tools: Fetching daily question #' + str(fid) + '...')
            return client.fetch_problem(fid)

        def done(window, result):
            if result:
                for k in ('md_path', 'code_path'):
                    if result.get(k):
                        window.open_file(result[k])
                sublime.status_message('LeetCodeTools: daily question fetched')

        _run_in_thread(self.window, work, _on_done=done)


