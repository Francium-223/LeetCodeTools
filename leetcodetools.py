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


def _base_url():
    site = _settings().get('site', 'cn')
    return 'https://leetcode.cn' if site == 'cn' else 'https://leetcode.com'


def _working_dir():
    return os.path.expanduser(_settings().get('working_dir', '~/leetcode'))


def _default_lang():
    return _settings().get('default_lang', 'python3')


def _lang():
    return _settings().get('language', 'zh')


def _run_timeout():
    return _settings().get('run_timeout', 1)


def _cache_dir():
    return os.path.join(os.path.expanduser('~'), '.leetcode_tools_cache')


def _last_update_path():
    return os.path.join(_cache_dir(), 'last_update.json')


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
    return os.path.join(_cache_dir(), 'cookie.json')


def _problem_list_cache_path():
    return os.path.join(_cache_dir(), 'problem_list.json')


def _study_plans_cache_path():
    return os.path.join(_cache_dir(), 'study_plans.json')


def _study_plan_problems_cache_path():
    return os.path.join(_cache_dir(), 'study_plan_problems.json')


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
    json_path = meta_base + '.json'
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
            req = urllib.request.Request(src, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=15)
            data = resp.read()
            ext = _guess_image_ext(src, resp)
            fname = str(counter[0]) + ext
            os.makedirs(img_dir, exist_ok=True)
            with open(os.path.join(img_dir, fname), 'wb') as f:
                f.write(data)
            return '![](' + rel_prefix + '/' + fname + ')'
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
            req = urllib.request.Request(raw_src, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=15)
            data = resp.read()
            ext = _guess_image_ext(raw_src, resp)
            fname = str(counter[0]) + ext
            os.makedirs(img_dir, exist_ok=True)
            with open(os.path.join(img_dir, fname), 'wb') as f:
                f.write(data)
            return '![' + alt + '](' + rel_prefix + '/' + fname + ')'
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

def _save_cookie_from_text(text):
    """解析用户粘贴的 Cookie（完整 Cookie 头 / LEETCODE_SESSION=... / 单独的 session 值）。"""
    text = (text or '').strip().strip(';').strip()
    if not text:
        raise ValueError('Cookie is empty.')
    # 去掉可能带上的 "Cookie:" 前缀
    if text.lower().startswith('cookie:'):
        text = text[7:].strip()
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
    """用需要登录的查询验证会话：todayRecord.userStatus 匿名时为 null。"""
    try:
        all_cookies = cookie_dict.get('all', {})
        raw = '; '.join(k + '=' + v for k, v in all_cookies.items())
        if not raw:
            session = cookie_dict.get('LEETCODE_SESSION') or cookie_dict.get('sl-session') or ''
            raw = 'LEETCODE_SESSION=' + session
        req = urllib.request.Request(
            'https://leetcode.cn/graphql/',
            data=json.dumps({'query': 'query { todayRecord { userStatus } }'}).encode(),
            headers={'Content-Type': 'application/json', 'Cookie': raw, 'User-Agent': 'Mozilla/5.0'}
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        rec = (data.get('data', {}).get('todayRecord') or [{}])[0]
        return rec.get('userStatus') is not None
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
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0',
            'Origin': 'https://leetcode.cn',
            'Referer': 'https://leetcode.cn/',
        }
        if cookie_raw:
            headers['Cookie'] = cookie_raw
        req = urllib.request.Request(
            'https://leetcode.cn/graphql/',
            data=json.dumps({'query': 'query nojGlobalData { siteRegion }'}).encode(),
            headers=headers,
        )
        resp = urllib.request.urlopen(req, timeout=10)
        for h in (resp.headers.get_all('Set-Cookie') or []):
            if h.lower().startswith('csrftoken='):
                return h.split('=', 1)[1].split(';', 1)[0]
    except Exception:
        pass
    return ''


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
    # 只粘了 sl-session、缺 csrftoken 时，自动补一个（带会话去拉）
    if not client.csrf_token:
        csrf = _fetch_csrftoken(client.cookie_raw)
        if csrf:
            client.csrf_token = csrf
            client.cookie_raw = client.cookie_raw + '; csrftoken=' + csrf
    return client


def _build_public_client():
    """无需登录的公开客户端（题解等公开接口用）。"""
    return LeetCodeToolsClient('')


# ==================== LeetCode CN API 客户端 ====================

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
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Origin': 'https://leetcode.cn',
            'Referer': 'https://leetcode.cn/problemset/',
        }
        if self.cookie_raw:
            headers['Cookie'] = self.cookie_raw
        if self.csrf_token:
            headers['X-CSRFToken'] = self.csrf_token
        req = urllib.request.Request('https://leetcode.cn/graphql/', data=body, headers=headers)
        resp = urllib.request.urlopen(req, timeout=30)
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
        """从 GraphQL 一次拉全量题目列表（titleCn 是中文）。"""
        all_questions = []
        skip = 0
        limit = 100
        while True:
            query = 'query{problemsetQuestionList(skip:' + str(skip) + ' limit:' + str(limit) + '){total questions{frontendQuestionId title titleCn titleSlug difficulty}}}'
            data = self._graphql(query)
            ps = data['problemsetQuestionList']
            for q in ps['questions']:
                all_questions.append({
                    'frontendQuestionId': str(q.get('frontendQuestionId', '')),
                    'titleCn': q.get('titleCn', ''),
                    'title': q.get('title', ''),
                    'titleSlug': q.get('titleSlug', ''),
                    'difficulty': q.get('difficulty', ''),
                })
            skip += limit
            if skip >= ps['total']:
                break
        os.makedirs(_cache_dir(), exist_ok=True)
        with open(_problem_list_cache_path(), 'w', encoding='utf-8') as f:
            json.dump(all_questions, f, ensure_ascii=False, indent=2)
        with open(_last_update_path(), 'w') as f:
            json.dump({'timestamp': time.time()}, f)
        return all_questions

    def _load_cache(self):
        _maybe_auto_update()
        cache_path = _problem_list_cache_path()
        if os.path.exists(cache_path):
            with open(cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return self._fetch_problem_list()

    def search_problems(self, keyword):
        problems = self._load_cache()
        kw = keyword.strip().lower()
        results = []
        for p in problems:
            fid = str(p.get('frontendQuestionId', ''))
            slug = (p.get('titleSlug', '') or '').lower()
            cn = (p.get('titleCn', '') or '').lower()
            en = (p.get('title', '') or '').lower()
            if kw == fid or kw in slug or kw in cn or kw in en:
                results.append(p)
        return results

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
        os.makedirs(working_dir, exist_ok=True)

        # MD
        md_path = os.path.join(working_dir, title_slug + '.md')
        img_folder = title_slug + '_images'
        img_dir = os.path.join(working_dir, img_folder)
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
        content = _download_images(content, img_dir, img_folder)
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
        json_path = os.path.join(working_dir, title_slug + '.json')
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
        in_path = os.path.join(working_dir, title_slug + '_in.json')
        out_path = os.path.join(working_dir, title_slug + '_out.json')
        example = detail.get('exampleTestcases', '')
        meta_str = detail.get('metaData', '')
        testcases = _parse_testcases(example, meta_str)
        question_id = detail.get('questionId', '')

        # 检查缓存是否有效
        need_interpret = True
        if os.path.exists(out_path) and os.path.exists(in_path):
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
                sublime.error_message('LeetCodeTools: interpret failed\n\n' + str(e))
                outputs = [''] * len(testcases)
            with open(in_path, 'w', encoding='utf-8') as f:
                serializable = []
                for tc in testcases:
                    serializable.append([_to_json(v) for v in tc])
                json.dump(serializable, f, ensure_ascii=False, indent=2)
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(outputs, f, ensure_ascii=False, indent=2)

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
        headers = {
            'Content-Type': 'application/json',
            'Cookie': self.cookie_raw,
            'Origin': 'https://leetcode.cn',
            'Referer': 'https://leetcode.cn/problems/' + problem_slug + '/',
            'User-Agent': 'Mozilla/5.0',
        }
        if self.csrf_token:
            headers['X-CSRFToken'] = self.csrf_token
        req = urllib.request.Request(url, data=payload, headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=30)
        except urllib.error.HTTPError as e:
            body = ''
            try:
                body = e.read().decode('utf-8', 'replace')
            except Exception:
                pass
            raise Exception('Submit HTTP %d: %s' % (e.code, body))
        res_json = json.loads(resp.read())
        if 'submission_id' not in res_json:
            raise Exception('Submission failed: ' + str(res_json))
        return res_json['submission_id']

    def check_submission(self, submission_id):
        url = 'https://leetcode.cn/submissions/detail/' + str(int(submission_id)) + '/check/'
        for _ in range(20):
            time.sleep(1)
            req = urllib.request.Request(url, headers={
                'Cookie': self.cookie_raw,
                'User-Agent': 'Mozilla/5.0',
            })
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read())
            state = data.get('state', '')
            if state == 'SUCCESS':
                return data
        raise Exception('Check submission timed out')

    def interpret_solution(self, problem_slug, question_id, lang_slug, typed_code, test_input):
        """Run Code（不占提交历史），返回 interpret_id。"""
        url = 'https://leetcode.cn/problems/' + problem_slug + '/interpret_solution/'
        payload = json.dumps({
            'lang': lang_slug,
            'question_id': str(question_id),
            'typed_code': typed_code,
            'data_input': test_input,
        }).encode()
        headers = {
            'Content-Type': 'application/json',
            'Cookie': self.cookie_raw,
            'Origin': 'https://leetcode.cn',
            'Referer': 'https://leetcode.cn/problems/' + problem_slug + '/',
            'User-Agent': 'Mozilla/5.0',
        }
        if self.csrf_token:
            headers['X-CSRFToken'] = self.csrf_token
        req = urllib.request.Request(url, data=payload, headers=headers)
        resp = urllib.request.urlopen(req, timeout=30)
        res = json.loads(resp.read())
        if 'interpret_id' not in res:
            raise Exception('Interpret failed: ' + str(res))
        return res['interpret_id']

    def _check_interpret(self, interpret_id):
        url = 'https://leetcode.cn/submissions/detail/' + str(interpret_id) + '/check/'
        for _ in range(20):
            time.sleep(1)
            req = urllib.request.Request(url, headers={
                'Cookie': self.cookie_raw,
                'User-Agent': 'Mozilla/5.0',
            })
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read())
            state = data.get('state', '')
            if state == 'SUCCESS':
                return data
        raise Exception('Interpret timed out')

    # ── 官方题解 ──

    def _find_official_solution(self, title_slug):
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
        detail = self._get_solution_detail(article.get('slug') or '')
        content = _clean_solution_markdown(detail.get('content'), detail.get('videosInfo'))
        if not content.strip():
            content = '_（题解内容为空）_'
        img_folder = title_slug + '_explanation_images'
        img_dir = os.path.join(working_dir, img_folder)
        content = _download_markdown_images(content, img_dir, img_folder)
        # 原文链接
        topic_id = None
        topic = article.get('topic')
        if isinstance(topic, dict):
            topic_id = topic.get('id')
        slug = article.get('slug') or ''
        url = _base_url() + '/problems/' + title_slug + '/solutions/'
        if topic_id:
            url += str(topic_id) + '/'
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
        """获取今日的每日一题，返回 {frontendQuestionId, titleSlug, title, difficulty}。"""
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

def _parse_signature_types(code):
    """从 Python 函数签名提取参数类型名列表。"""
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
                        ann = p.split(':', 1)[1].strip()
                        inner = re.search(r'\[(.*)\]', ann)
                        if inner:
                            ann = inner.group(1)
                        ann = ann.strip().strip("'\"").strip()
                        mm = re.search(r'(\w+)', ann)
                        if mm:
                            types.append(mm.group(1))
                break
    return types

def _from_json(val, ptype):
    """JSON 原始值 → ListNode/TreeNode/Node 对象。"""
    ptype = (ptype or '').lower()
    if 'listnode' in ptype:
        return _build_list(val)
    if 'treenode' in ptype:
        return _build_tree(val)
    if 'node' == ptype or 'graph' in ptype or ptype == 'node':
        return _build_graph(val)
    return val

def _to_json(val):
    """将 ListNode/TreeNode 转回 JSON 可序列化格式。"""
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
        if 'ListNode' in ptype or 'listnode' in ptype.lower():
            return _build_list(val)
        if 'TreeNode' in ptype or 'treenode' in ptype.lower():
            return _build_tree(val)
        if 'Node' in ptype or 'node' == ptype.lower() or 'graph' in ptype.lower():
            return _build_graph(val)
        return val

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
    ptype = (ptype or '').lower()
    if 'listnode' in ptype:
        return _build_list(val)
    if 'treenode' in ptype:
        return _build_tree(val)
    if ptype == 'node' or 'graph' in ptype:
        return _build_graph(val)
    return val


def _to_json(val):
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
        if 'ListNode' in ptype or 'listnode' in ptype.lower():
            return _build_list(val)
        if 'TreeNode' in ptype or 'treenode' in ptype.lower():
            return _build_tree(val)
        if 'Node' in ptype or 'node' == ptype.lower() or 'graph' in ptype.lower():
            return _build_graph(val)
        return val

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


def _find_func(namespace, func_name):
    func = namespace.get(func_name)
    if func is not None:
        return func
    for v in namespace.values():
        if isinstance(v, type) and hasattr(v, func_name):
            return getattr(v(), func_name)
    for v in namespace.values():
        if callable(v) and not getattr(v, '__name__', '').startswith('_'):
            name = getattr(v, '__name__', '')
            if name in ('_build_list', '_build_tree', '_build_graph', func_name):
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

    namespace = {
        'ListNode': ListNode, 'TreeNode': TreeNode, 'Node': Node,
        '_build_list': _build_list, '_build_tree': _build_tree, '_build_graph': _build_graph,
    }
    filename = payload.get('filename') or '<solution>'
    try:
        exec('from typing import *', namespace)
        exec(compile(code, filename, 'exec'), namespace)
    except Exception:
        _emit({'error': 'Compile/Exec Error:\n' + _user_error_text()})
        return

    func = _find_func(namespace, func_name)
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
            results.append({'input': input_repr, 'output': json.dumps(_to_json(box['output'])),
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
    """在子进程里跑离线判题（可 kill 死循环）。返回 5 元组结果列表。"""
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

    results = data.get('results', [])
    return [(
        r.get('input', ''),
        r.get('output', ''),
        r.get('stdout', ''),
        r.get('error'),
        r.get('elapsed', 0.0),
    ) for r in results]


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
        try:
            webbrowser.open('https://leetcode.cn/')
        except Exception as e:
            sublime.error_message('Failed to open browser:\n' + str(e))
            return
        sublime.message_dialog(
            'LeetCode Tools 登录 / Login\n\n'
            '中文：\n'
            '1. 在浏览器里登录 https://leetcode.cn\n'
            '2. 按 F12 打开开发者工具\n'
            '3. 点顶部「应用 / Application」标签（Firefox 叫「存储 / Storage」）\n'
            '4. 左侧展开「Cookies」，点 https://leetcode.cn\n'
            '5. 找到 LEETCODE_SESSION 这一行\n'
            '6. 双击它的「值 / Value」格子 → 按 Ctrl+A 全选 → Ctrl+C 复制\n'
            '7. 回到 Sublime，粘贴到输入框，按回车\n\n'
            'English:\n'
            '1. Log in to https://leetcode.cn in your browser.\n'
            '2. Press F12 to open the developer tools.\n'
            '3. Click the "Application" tab (called "Storage" in Firefox).\n'
            '4. In the left panel, expand "Cookies" and click https://leetcode.cn.\n'
            '5. Find the LEETCODE_SESSION row.\n'
            '6. Double-click its "Value" cell, press Ctrl+A to select all, then Ctrl+C to copy.\n'
            '7. Back in Sublime, paste it into the input box and press Enter.\n\n'
            '注意 / Note:\n'
            'LEETCODE_SESSION 是登录凭证，值很长，一定要 Ctrl+A 全选，否则只复制到一半会登录失败。\n'
            'LEETCODE_SESSION is the login token; it is very long, so press Ctrl+A to select the whole value, or the login will fail.'
        )
        self.window.show_input_panel(
            '粘贴 LEETCODE_SESSION 的值（或整个 Cookie）:',
            '', self._on_cookie, None,
            lambda: sublime.status_message('LeetCodeTools: Login cancelled'))

    def _on_cookie(self, text):
        try:
            data = _save_cookie_from_text(text)
        except Exception as e:
            sublime.error_message(
                'LeetCodeTools: 没识别出 sl-session。\n\n'
                '请复制 leetcode.cn 的 sl-session「值 / Value」再试。')
            return

        sublime.status_message('LeetCodeTools: Verifying cookie...')

        def work(window):
            return _validate_cookie(data)

        def done(window, ok):
            if ok:
                sublime.status_message('LeetCodeTools: Login successful!')
            else:
                sublime.error_message(
                    'LeetCodeTools: 没登录成功。\n\n'
                    '请确认：\n'
                    '1. 已经登录 leetcode.cn\n'
                    '2. 复制的是 sl-session 的值（Value），不是名字（Name）\n'
                    '3. 别复制成 csrftoken 或别的\n\n'
                    '再运行一次 Login 重试。')

        _run_in_thread(self.window, work, _on_done=done)


# ─── Search ───

class LeetcodeSearchCommand(sublime_plugin.WindowCommand):
    def run(self):
        self.window.show_input_panel('LeetCode Search:', '', self._on_keyword, None, None)

    def _on_keyword(self, keyword):
        if not keyword.strip():
            return
        kw = keyword.strip()
        sublime.status_message('LeetCode Tools: Searching...')

        def work(window):
            client = _build_client()
            return client.search_problems(kw)

        def done(window, results):
            if not results:
                sublime.message_dialog('No problems matched "' + kw + '".')
                return
            items = []
            for p in results:
                fid = p.get('frontendQuestionId', '?')
                title = p.get('titleCn') or p.get('title', '?')
                diff = p.get('difficulty', '?')
                items.append(['#' + str(fid) + '  ' + title, str(diff)])

            def on_select(idx):
                if idx >= 0:
                    p = results[idx]
                    fid = p['frontendQuestionId']

                    def fetch_and_open(window):
                        sublime.status_message('LeetCode Tools: Fetching #' + str(fid) + '...')
                        client = _build_client()
                        return client.fetch_problem(fid)

                    def done_fetch(window, result):
                        if result:
                            for k in ('md_path', 'code_path'):
                                if result.get(k):
                                    window.open_file(result[k])
                            sublime.status_message('LeetCodeTools: #' + str(fid) + ' fetched')

                    _run_in_thread(window, fetch_and_open, _on_done=done_fetch)

            window.show_quick_panel(items, on_select)

        _run_in_thread(self.window, work, _on_done=done)


# ─── Fetch ───

class LeetcodeFetchCommand(sublime_plugin.WindowCommand):
    def run(self):
        self.window.show_input_panel(
            'Problem # (e.g. 1 or 1 python3):', '',
            self._on_input, None, None
        )

    def _on_input(self, text):
        parts = text.strip().split()
        if not parts:
            return
        qid = parts[0]
        lang = parts[1] if len(parts) > 1 else _default_lang()

        def work(window):
            client = _build_client()
            return client.fetch_problem(qid, lang)

        def done(window, result):
            if result:
                if result.get('md_path'):
                    window.open_file(result['md_path'])
                if result.get('code_path'):
                    window.open_file(result['code_path'])
                sublime.status_message('LeetCodeTools: #' + str(result['fid']) + ' fetched')

        _run_in_thread(self.window, work, _on_done=done)


# ─── Run (Offline Judge) ───

class LeetcodeRunCommand(sublime_plugin.TextCommand):
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
        json_path = base + '.json'
        if not os.path.exists(json_path):
            sublime.error_message('Test file not found:\n' + json_path)
            return
        window = self.view.window()

        def work(window):
            base = fp[:-(len(ext) + 1)]
            json_path = base + '.json'
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
            # manual 题用函数签名类型，否则用 metaData 类型
            manual = meta_obj.get('manual', False)
            sig_types = _parse_signature_types(code)
            params = meta_obj.get('params', [])
            timeout = _run_timeout()
            # 优先用 _in.json（含手动追加的失败用例）
            in_path = base + '_in.json'
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
                example = test_data.get('exampleTestcases', '')
                results = _run_offline_subprocess(
                    code, [], [], func_name, timeout,
                    example=example, meta_str=json.dumps(meta_obj), mode='example', filename=fp)
            in_path = base + '_in.json'
            out_path = base + '_out.json'
            expected_outputs = None
            if os.path.exists(out_path):
                with open(out_path, 'r', encoding='utf-8') as f:
                    expected_outputs = json.load(f)
            _, fname = os.path.split(fp)
            lines = ['=' * 50, '  LeetCode Offline Judge -- ' + fname, '=' * 50, '']
            passed = 0
            total_time = 0.0
            for i, (inp, out, stdout, err, elapsed) in enumerate(results, 1):
                total_time += elapsed
                lines.append('Test ' + str(i) + ':')
                if err:
                    lines.append('  INPUT  ' + str(inp))
                    lines.append('  ERROR  ' + str(err))
                    if stdout:
                        lines.append('  STDOUT\n' + stdout)
                else:
                    lines.append('  INPUT  ' + str(inp))
                    if stdout:
                        lines.append('  STDOUT\n' + stdout)
                    lines.append('  OUTPUT ' + str(out))
                    lines.append('  TIME   ' + _fmt_time(elapsed))
                    if expected_outputs and i <= len(expected_outputs):
                        exp = expected_outputs[i - 1]
                        try:
                            out_val = json.loads(out) if isinstance(out, str) else out
                            exp_val = json.loads(exp) if isinstance(exp, str) else exp
                            match = out_val == exp_val
                        except Exception:
                            match = str(out).strip() == str(exp).strip()
                        try:
                            exp_repr = json.dumps(exp, ensure_ascii=False)
                        except Exception:
                            exp_repr = str(exp)
                        lines.append('  EXPECT ' + exp_repr + ('  OK' if match else '  FAIL'))
                        if match: passed += 1
                lines.append('')
            lines.append(str(passed) + '/' + str(len(results)) + ' passed.')
            lines.append('Total time: ' + _fmt_time(total_time))
            lines.append('=' * 50)
            return '\n'.join(lines)

        def done(window, text):
            _show_output(window, 'leetcode_run', text)

        _run_in_thread(window, work, _on_done=done)


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
            json_path = base + '.json'
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
                    try:
                        base = fp[:-(len(ext) + 1)]
                        in_path = base + '_in.json'
                        out_path = base + '_out.json'
                        json_path = base + '.json'
                        meta_str = '{}'
                        if os.path.exists(json_path):
                            with open(json_path) as jf:
                                meta_str = json.load(jf).get('metaData', '{}')
                        meta = json.loads(meta_str) if isinstance(meta_str, str) else meta_str
                        new_tc = _parse_testcases(r['last_testcase'], json.dumps(meta))
                        exp_val = r['expected_output']
                        try:
                            exp_val = json.loads(exp_val)
                        except Exception:
                            pass
                        if os.path.exists(in_path):
                            with open(in_path) as f:
                                tc_list = json.load(f)
                            for tc in new_tc:
                                tc_list.append([_to_json(v) for v in tc])
                            with open(in_path, 'w') as f:
                                json.dump(tc_list, f)
                        if os.path.exists(out_path):
                            with open(out_path) as f:
                                exp_list = json.load(f)
                            exp_list.append(exp_val)
                            with open(out_path, 'w') as f:
                                json.dump(exp_list, f)
                    except Exception:
                        pass
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

# ─── Fetch (Force) ───

class LeetcodeFetchForceCommand(sublime_plugin.WindowCommand):
    def run(self):
        self.window.show_input_panel(
            'Force fetch (overwrites .py/.md):', '',
            self._on_input, None, None
        )

    def _on_input(self, text):
        parts = text.strip().split()
        if not parts:
            return
        qid = parts[0]
        lang = parts[1] if len(parts) > 1 else _default_lang()

        def work(window):
            client = _build_client()
            return client.fetch_problem(qid, lang, force=True)

        def done(window, result):
            if result:
                if result.get('md_path'):
                    window.open_file(result['md_path'])
                if result.get('code_path'):
                    window.open_file(result['code_path'])
                sublime.status_message('LeetCodeTools: #' + str(result['fid']) + ' force fetched')

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


