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


# ── 找到系统 Python 3.14 exe，用于跑 cookie_grabber.py ──

def _find_system_python():
    """找到系统较高版本 Python。"""
    import glob
    candidates = [
        os.path.expandvars(r'%LOCALAPPDATA%\Python\bin\python3.exe'),
        os.path.expandvars(r'%LOCALAPPDATA%\Python\bin\python.exe'),
    ]
    for pat in [r'%LOCALAPPDATA%\Python\pythoncore-3.*-64\python.exe']:
        candidates.extend(glob.glob(os.path.expandvars(pat)))
    candidates.append('python3')
    candidates.append('python')
    for p in candidates:
        try:
            ver = subprocess.check_output([p, '--version'], stderr=subprocess.STDOUT, timeout=5).decode()
            if '3.' in ver:
                return p
        except Exception:
            continue
    return 'python'


LANG_EXT = {
    'python3': 'py', 'python': 'py', 'java': 'java',
    'cpp': 'cpp', 'c': 'c', 'csharp': 'cs',
    'javascript': 'js', 'typescript': 'ts', 'golang': 'go',
    'rust': 'rs', 'kotlin': 'kt', 'swift': 'swift',
    'scala': 'scala', 'ruby': 'rb', 'php': 'php',
}

EXT_LANG = {v: k for k, v in LANG_EXT.items() if v not in ('py',) or k == 'python3'}
EXT_LANG['py'] = 'python3'


# ==================== Cookie & API helpers ====================

def _grabber_path():
    return os.path.join(_cache_dir(), 'cookie_grabber.py')


def _cookie_login():
    """后台跑 cookie_grabber.py，等信号触发抓 Cookie。"""
    python_exe = _find_system_python()
    script = _grabber_path()
    err_file = os.path.join(_cache_dir(), '.grabber_err.txt')
    os.makedirs(_cache_dir(), exist_ok=True)
    sublime.status_message('LeetCode Tools: Opening browser...')
    subprocess.Popen(
        [python_exe, script, '--cache-dir', _cache_dir()],
        stderr=open(err_file, 'w')
    )

    # 等 2 秒检查进程是否还活着
    def check_alive():
        err_path = err_file
        if os.path.exists(err_path) and os.path.getsize(err_path) > 0:
            with open(err_path) as f:
                err_text = f.read().strip()
            if err_text:
                sublime.error_message('Cookie grabber error:\n' + err_text)
                return
        # 还没错误但也没 cookie → 可能是浏览器还没打开
        sublime.set_timeout(_check_cookie_or_error, 2000)

    sublime.set_timeout(check_alive, 2000)


def _signal_login_ready():
    """写信号文件，通知 cookie_grabber 抓 Cookie。"""
    signal_file = os.path.join(_cache_dir(), '.login_ready')
    with open(signal_file, 'w') as f:
        f.write('ok')


def _check_cookie_ready():
    """轮询检查 cookie.json 是否已写入。"""
    if os.path.exists(_cookie_cache_path()):
        sublime.status_message('LeetCodeTools: Login successful!')
    else:
        sublime.set_timeout(_check_cookie_ready, 500)


def _check_cookie_or_error():
    err_file = os.path.join(_cache_dir(), '.grabber_err.txt')
    if os.path.exists(err_file) and os.path.getsize(err_file) > 0:
        with open(err_file) as f:
            sublime.error_message('Cookie grabber error:\n' + f.read().strip())
    elif not os.path.exists(_cookie_cache_path()):
        sublime.set_timeout(_check_cookie_or_error, 1000)
    # 有 cookie 就静默成功


def _validate_cookie(cookie_dict):
    try:
        raw = 'sl-session="' + cookie_dict['sl-session'] + '"'
        if cookie_dict.get('csrftoken'):
            raw += '; csrftoken=' + cookie_dict['csrftoken']
        req = urllib.request.Request(
            'https://leetcode.cn/graphql/',
            data=json.dumps({
                'query': 'query { question(titleSlug:"two-sum") { questionId } }'
            }).encode(),
            headers={'Content-Type': 'application/json', 'Cookie': raw, 'User-Agent': 'Mozilla/5.0'}
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        return data.get('data', {}).get('question', {}).get('questionId') is not None
    except Exception:
        return False


def get_leetcode_cookie():
    cache_path = _cookie_cache_path()
    if os.path.exists(cache_path):
        with open(cache_path, 'r', encoding='utf-8') as f:
            cached = json.load(f)
        if _validate_cookie(cached):
            return cached
    raise RuntimeError('No valid cookie. Run "LeetCode: Login" first.')


def _build_client():
    cookie_dict = get_leetcode_cookie()
    all_cookies = cookie_dict.get('all', {})
    parts = []
    for k, v in all_cookies.items():
        parts.append(k + '=' + v)
    raw = '; '.join(parts)
    if not raw:
        raw = 'sl-session="' + cookie_dict['sl-session'] + '"'
        if cookie_dict.get('csrftoken'):
            raw += '; csrftoken=' + cookie_dict['csrftoken']
    return LeetCodeToolsClient(raw)


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
            'Cookie': self.cookie_raw,
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Origin': 'https://leetcode.cn',
            'Referer': 'https://leetcode.cn/problemset/',
        }
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

    def fetch_problem(self, question_id, lang='python3', working_dir=None, force=False):
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

    def submit_code(self, problem_slug, question_id, lang_slug, typed_code):
        base_url = _base_url()
        url = base_url + '/problems/' + problem_slug + '/submit/'
        payload = json.dumps({
            'lang': lang_slug,
            'question_id': str(question_id),
            'typed_code': typed_code
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


def _run_offline(code_str, testcases, func_name, filename='<string>'):
    namespace = {
        'ListNode': ListNode, 'TreeNode': TreeNode, 'Node': Node,
        '_build_list': _build_list, '_build_tree': _build_tree, '_build_graph': _build_graph,
    }
    typed_code = code_str
    try:
        exec('from typing import *', namespace)
        exec(compile(typed_code, filename, 'exec'), namespace)
    except Exception as e:
        return [('', '', '', 'Compile/Exec Error:\n' + traceback.format_exc())]

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
        return [('', '', '', 'Function "' + func_name + '" not found in code.')]

    results = []
    for args in testcases:
        input_repr = ', '.join(json.dumps(_to_json(a), default=str) for a in args)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                output = func(*args)
            results.append((input_repr, json.dumps(_to_json(output)), buf.getvalue().strip(), None))
        except Exception as e:
            results.append((input_repr, '', buf.getvalue().strip(), traceback.format_exc()))
    return results


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
            _cookie_login()
        except Exception as e:
            sublime.error_message('Failed to start grabber:\n' + str(e))
            return

        def ask():
            if sublime.ok_cancel_dialog(
                'LeetCode \n\nBrowser opened. Please log in, then click OK.',
                ok_title='Logged In'
            ):
                _signal_login_ready()
                sublime.status_message('LeetCodeTools: Reading cookies...')
                sublime.set_timeout(_check_cookie_ready, 1000)
            else:
                sublime.status_message('LeetCodeTools: Login cancelled')

        sublime.set_timeout(ask, 1500)


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
            # 优先用 _in.json（含手动追加的失败用例）
            in_path = base + '_in.json'
            if os.path.exists(in_path):
                with open(in_path, encoding='utf-8') as f:
                    raw_tc = json.load(f)
                params = meta_obj.get('params', [])
                testcases = []
                for tc in raw_tc:
                    args = []
                    for j, v in enumerate(tc):
                        if manual and j < len(sig_types):
                            ptype = sig_types[j]
                        else:
                            ptype = params[j].get('type', '') if j < len(params) else ''
                        args.append(_from_json(v, ptype))
                    testcases.append(tuple(args))
            else:
                example = test_data.get('exampleTestcases', '')
                testcases = _parse_testcases(example, json.dumps(meta_obj))
            results = _run_offline(code, testcases, func_name, fp)
            in_path = base + '_in.json'
            out_path = base + '_out.json'
            expected_outputs = None
            if os.path.exists(out_path):
                with open(out_path, 'r', encoding='utf-8') as f:
                    expected_outputs = json.load(f)
            _, fname = os.path.split(fp)
            lines = ['=' * 50, '  LeetCode Offline Judge -- ' + fname, '=' * 50, '']
            passed = 0
            for i, (inp, out, stdout, err) in enumerate(results, 1):
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
                    if expected_outputs and i <= len(expected_outputs):
                        exp = expected_outputs[i - 1]
                        try:
                            out_val = json.loads(out) if isinstance(out, str) else out
                            exp_val = json.loads(exp) if isinstance(exp, str) else exp
                            match = out_val == exp_val
                        except Exception:
                            match = str(out).strip() == str(exp).strip()
                        lines.append('  EXPECT ' + str(exp) + ('  OK' if match else '  FAIL'))
                        if match: passed += 1
                lines.append('')
            lines.append(str(passed) + '/' + str(len(results)) + ' passed.')
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
            with open(fp, 'r', encoding='utf-8') as f:
                code = f.read()
            client = _build_client()
            sid = client.submit_code(title_slug, question_id, lang_slug, code)
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
                lines.append('Status:   Accepted \u2705')
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
                    # 追加到本地 _in/_out
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
            return client._fetch_problem_list()

        def done(window, result):
            sublime.status_message('LeetCodeTools: Cache updated (' + str(len(result)) + ' problems)')

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
                if result.get('json_path'):
                    window.open_file(result['json_path'])
                sublime.status_message('LeetCodeTools: #' + str(result['fid']) + ' force fetched')

        _run_in_thread(self.window, work, _on_done=done)


