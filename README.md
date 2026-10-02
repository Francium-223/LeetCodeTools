# LeetCodeTools for Sublime Text

LeetCodeTools is A [Sublime Text](www.sublimetext.com) plugin for practicing [LeetCode](leetcode.cn) without leaving your editor. It lets you search problems, fetch problems and official solutions, run your code offline against the official examples, submit directly to LeetCode, and pick problems from study plans like [Hot 100](https://leetcode.cn/studyplan/top-100-liked/) and [Top Interview 150](https://leetcode.cn/studyplan/top-interview-150/). Problems and solutions are saved locally as Markdown files; log in once and you're good to go.

LeetCodeTools 是一个 [Sublime Text](https://www.sublimetext.com/) 插件，让你不用离开编辑器就能刷[力扣](leetcode.cn)（LeetCode）。它支持按题号或关键字搜索题目、拉取题目和官方题解、在本地离线运行代码并与官方示例对比、一键提交到力扣判题，还能从「[热题 100](https://leetcode.cn/studyplan/top-100-liked/)」「[面试经典 150 题](https://leetcode.cn/studyplan/top-interview-150/)」等题集里选题。题目和题解都会以 Markdown 文件保存到本地，登录一次即可长期使用。
一个让你不用离开编辑器、直接在 [Sublime Text](https://www.sublimetext.com/) 里刷[力扣（LeetCode.cn）](https://leetcode.cn)的插件：搜索题目、拉取题目与官方题解、离线运行、直接提交。

## Features / 功能

| Command 命令 | Description 说明 |
|------|------|
| `LeetCode Tools: Login` | Open the browser to log in and paste the `LEETCODE_SESSION` cookie. 打开浏览器登录并粘贴 `LEETCODE_SESSION` Cookie |
| `LeetCode Tools: Search` | Open a filterable list of every problem (type an id or a title keyword in the panel, press Enter to fetch). 打开可过滤的题目列表（在面板里输入题号或标题关键字，回车拉题） |
| `LeetCode Tools: Update` | Rebuild the local problem-list and study-plan caches. 重建本地题目列表和题集缓存 |
| `LeetCode Tools: Run` | Run your code offline and compare with the official examples. 离线运行代码，与官方示例对比 |
| `LeetCode Tools: Run Online` | Same output as Run, but executed by LeetCode (like `Ctrl+'` in the web editor); works for every language, needs the login cookie. 输出格式和 Run 一致，但代码发到力扣跑（等价网页端 `Ctrl+'`）；支持所有语言，需要登录 Cookie |
| `LeetCode Tools: Submit` | Submit the current code to LeetCode for judging. 把当前代码提交到力扣判题 |
| `LeetCode Tools: Open in Browser` | Open the current problem's page in the browser. 在浏览器打开当前题目的网页 |
| `LeetCode Tools: Fetch Official Explanations` | Fetch the official solution as `xxx_explanation.md`. 拉取官方题解为 `xxx_explanation.md` |
| `LeetCode Tools: Select from Problem Set` | Pick a problem from a study plan (Hot 100, Top Interview 150, …). 从题集（热题100 / 面试经典150题等学习计划）里选题 |
| `LeetCode Tools: Daily Question` | Fetch today's daily question. 拉取今日的每日一题 |

## Installation / 安装

1. Install [Package Control](https://packagecontrol.io/installation) first if you don't have it yet.
   先安装 [Package Control](https://packagecontrol.io/installation)（如果还没有）。

2. Press `Ctrl+Shift+P` (`Cmd+Shift+P` on macOS), run `Package Control: Install Package`, search for `LeetCodeTools`, and press Enter to install.
   按 `Ctrl+Shift+P`（macOS 为 `Cmd+Shift+P`），运行 `Package Control: Install Package`，搜索 `LeetCodeTools` 并回车安装。

3. Install a system **Python 3** and add it to PATH. It is required by the offline Run (not by Login).
   安装一个系统 **Python 3** 并加入 PATH。离线 Run 需要它（登录不需要）。

4. Press `Ctrl+Shift+P`, run `LeetCode Tools: Login`; it opens the site in your browser. Log in, then press F12 → **Network** → reload → click any request → **Request Headers → `Cookie`** → select the whole value (`Ctrl+A`) → copy. Paste it into the input panel. Copy it from **Request Headers**, never from a `Set-Cookie` response header: only the request header carries the `cf_clearance` / `__cf_bm` cookies that Cloudflare issues — leetcode.com needs them for `Submit` / `Run Online`.
   按 `Ctrl+Shift+P`，运行 `LeetCode Tools: Login`；它会在浏览器打开站点。登录后按 F12 → **Network（网络）** → 刷新 → 点任意一个请求 → **Request Headers（请求标头）→ `Cookie`** → 双击值后 `Ctrl+A` 全选复制，粘进输入框。注意要从**请求标头**复制，不要从响应头的 `Set-Cookie` 复制：只有请求头那一份才带 Cloudflare 发的 `cf_clearance` / `__cf_bm`，leetcode.com 的 `Submit` / `Run Online` 需要它。

> Note: Search / Update / Submit / Run Online need the login cookie; Open in Browser, Run, and Fetch Official Explanations do not (the official solution uses the public GraphQL API).
> 说明：Search / Update / Submit / Run Online 需要登录 Cookie；Open in Browser、Run、Fetch Official Explanations 不需要登录（其中官方题解走的是公开 GraphQL 接口）。

## Configuration / 配置

Settings are edited via `Preferences: LeetCodeTools Settings` in the command palette, or `Preferences → Package Settings → LeetCodeTools → Settings` in the menu:
设置通过命令面板的 `Preferences: LeetCodeTools Settings`，或菜单 `Preferences → Package Settings → LeetCodeTools → Settings` 编辑：

| Key 配置项 | Default 默认值 | Description 说明 |
|--------|--------|------|
| `working_dir` | `~/leetcode` | Where problems are saved. 题目保存目录 |
| `default_lang` | `python3` | Default language used when fetching a problem. 拉题时的默认语言 |
| `language` | `zh` | Problem language (`zh` / `en`). 题目语言（`zh` / `en`） |
| `site` | `cn` | `cn` = leetcode.cn; `com` = leetcode.com. Cookies and caches are stored per site, so each site needs its own `Login` and switching never mixes them. `cn` = leetcode.cn；`com` = leetcode.com。Cookie 和缓存按站点分开存，两个站各要 `Login` 一次，切站不会互相覆盖 |
| `browser_ua` | `""` | User-Agent used for requests. Leave empty for a built-in Chrome UA; set it to your own browser's exact UA when you want a pasted `cf_clearance` to be accepted (see the leetcode.com note below). 请求使用的 User-Agent。留空用内置 Chrome UA；想让粘贴的 `cf_clearance` 生效就填自己浏览器的 UA（见下方 leetcode.com 说明） |
| `cache_age_days` | `7` | Cache age in days for the problem list and study plans; auto-refreshes when expired. 题目列表与题集的缓存天数，过期自动刷新 |
| `run_timeout` | `1` | Time limit in seconds for the offline Run (0 = no limit). 离线 Run 的超时秒数（0 表示不限时） |

## Supported languages / 支持的语言

These languages are available for fetched code templates and for Submit; the offline Run is Python-only (see limitations below).
以下语言可用于拉取的代码模板和 Submit；离线 Run 目前仅支持 Python（见下方限制）。

| Language slug 语言 | Extension 扩展名 | Run offline 离线运行 |
|------|------|------|
| `python3` / `python` | `.py` | ✔️ |
| `java` | `.java` | ❌️ |
| `cpp` | `.cpp` | ❌️ |
| `c` | `.c` | ❌️ |
| `csharp` | `.cs` | ❌️ |
| `javascript` | `.js` | ❌️ |
| `typescript` | `.ts` | ❌️ |
| `golang` | `.go` | ❌️ |
| `rust` | `.rs` | ❌️ |
| `kotlin` | `.kt` | ❌️ |
| `swift` | `.swift` | ❌️ |
| `scala` | `.scala` | ❌️ |
| `ruby` | `.rb` | ❌️ |
| `php` | `.php` | ❌️ |

## Usage / 使用流程

1. **Log in** (once): `LeetCode Tools: Login` → log in in the browser → copy `LEETCODE_SESSION` → paste it.
   **登录**（只需一次）：`LeetCode Tools: Login` → 浏览器登录 → 复制 `LEETCODE_SESSION` → 粘贴。

2. **Pick a problem**: `LeetCode Tools: Search` → the panel lists every problem; type an id or a title keyword to filter, press Enter to fetch and open the `.md` and code file.
   **选题**：`LeetCode Tools: Search` → 面板里列出全部题目，直接输入题号或标题关键字过滤，回车自动拉取并打开 `.md` 和代码文件。

3. **Solve it**: write your solution in the code file.
   **做题**：在代码文件里写解法。

4. **Run**: `LeetCode Tools: Run` executes locally (Python only) and compares with the official examples; `LeetCode Tools: Run Online` has the same output layout but is executed by LeetCode (like `Ctrl+'` in the web editor), so it works for any language.
   **运行**：`LeetCode Tools: Run` 在本地跑（仅 Python）并与官方示例对比；`LeetCode Tools: Run Online` 输出格式完全一致，但代码是发到力扣跑的（等价网页端 `Ctrl+'`），所以任何语言都能用。

5. **Submit**: `LeetCode Tools: Submit`; failed cases are appended to the local `_in.json` / `_out.json` for offline replay.
   **提交**：`LeetCode Tools: Submit`，失败用例会自动追加到本地 `_in.json` / `_out.json`，方便离线复现。

6. **Read the solution**: `LeetCode Tools: Fetch Official Explanations` generates and opens `xxx_explanation.md`.
   **看题解**：`LeetCode Tools: Fetch Official Explanations`，生成并打开 `xxx_explanation.md`。

## Generated files / 生成的文件

User-facing files go under `working_dir`; the plugin's internal files go into the cache.
用户可见的文件存在 `working_dir`，插件内部文件存在缓存目录。

**Under `working_dir`（在 working_dir 下）:**

| File 文件 | Content 内容 |
|------|------|
| `{slug}.md` | Problem description (HTML → Markdown). 题目描述（HTML 转成 Markdown） |
| `{slug}.py` | Official code template. 官方代码模板 |
| `{slug}_explanation.md` | Official solution (from Fetch Official Explanations). 官方题解（由 Fetch Official Explanations 生成） |

**In the cache `{working_dir}/.cache/`（在缓存目录下）:**

Everything cache-side is **per site**: `leetcode.cn` keeps the plain names below, other sites get a
`_com` suffix (`cookie_com.json`, `problems_com/`, …), so switching `site` never mixes the two.
缓存文件都是**按站点分开**的：`leetcode.cn` 用下面这些名字，其它站点带 `_com` 后缀（`cookie_com.json`、`problems_com/` …），切站不会互相覆盖。

| File 文件 | Content 内容 |
|------|------|
| `cookie.json` (cn) / `cookie_com.json` (com) | Login cookie for that site. 该站点的登录 Cookie |
| `problem_list.json` / `problem_list_com.json` | Cached problem list. 题目列表缓存 |
| `problems[_com]/{slug}.json` | Metadata (id / metaData / examples). 元数据（题号 / metaData / 示例用例） |
| `problems[_com]/{slug}_in.json` | Parsed input cases. 解析后的输入用例 |
| `problems[_com]/{slug}_out.json` | Expected outputs. 预期输出 |
| `images/{slug}/` | Problem-description images (shared). 题目描述的图片（共用） |
| `images/{slug}_explanation/` | Official-solution images (shared). 官方题解的图片（共用） |

## Current limitations / 当前限制

- Offline **Run currently supports only Python**: the local judge runs your code with Python `exec` and includes built-in parsing for `ListNode` / `TreeNode` / `Node`. Other languages (java / cpp / javascript / golang, …) can be run with `LeetCode Tools: Run Online` (LeetCode executes them) or submitted with `Submit` — they just cannot run locally.
- **离线 Run 目前仅支持 Python**：本地判题用 Python `exec` 运行你的代码，并内置了 `ListNode` / `TreeNode` / `Node` 的用例解析。其它语言（java / cpp / javascript / golang 等）可以用 `LeetCode Tools: Run Online`（交给力扣执行）或 `Submit` 提交，只是不能本地跑。
- On **leetcode.com**, everything except `Submit` and `Run Online` works with a normal login: search, fetch, study plans, daily question and official explanations all use the public API (the plugin's queries are site-aware). The two write endpoints — `/problems/<slug>/submit/` and `/problems/<slug>/interpret_solution/` — sit behind a **Cloudflare managed challenge** (`cf-mitigated: challenge`, HTTP 403) and only a browser's `cf_clearance` cookie gets through it. If Cloudflare challenges a request the error message says exactly that. To give it the best chance: pass the challenge in your browser, run `Login` and paste the **whole `Cookie` request header** (so `cf_clearance` / `__cf_bm` come along), and set `browser_ua` to that same browser's User-Agent — Cloudflare binds `cf_clearance` to the UA (and IP) that obtained it. Without it, `Submit` / `Run Online` will keep failing, and the offline `Run` can only show your own outputs (no `EXPECT` lines, and the panel says `no expected outputs, nothing compared` instead of faking failures).
- **leetcode.com** 上除了 `Submit` 和 `Run Online`，其余都能用正常登录跑通：搜题、拉题、题集、每日一题、官方题解走的都是公开接口（插件里的查询已按站点区分）。只有两个写接口 —— `/problems/<slug>/submit/` 和 `/problems/<slug>/interpret_solution/` —— 后面是 **Cloudflare 托管挑战**（`cf-mitigated: challenge` / HTTP 403），只有浏览器的 `cf_clearance` 能过。真被挑战时，报错里会直接写明。想尽量过掉：先在浏览器里过掉挑战，再运行 `Login` 并粘**整条 `Cookie` 请求头**（这样 `cf_clearance` / `__cf_bm` 才带上），同时把 `browser_ua` 设成同一个浏览器的 User-Agent —— Cloudflare 的 `cf_clearance` 和拿到它的 UA（以及 IP）是绑定的。搞不定的话 `Submit` / `Run Online` 会一直失败，离线 `Run` 也只能看到你自己的输出（没有 `EXPECT` 行，面板会写 `no expected outputs, nothing compared`，而不是假装全错）。
- Only the China site ([leetcode.cn](https://leetcode.cn)) is fully supported; the global site ([leetcode.com](https://leetcode.com)) works for everything the public API allows, and for `Submit` / `Run Online` as far as Cloudflare lets it through.

## Roadmap / 待办

- [ ] Separate the parser (test-case parsing + linked-list/tree/graph building + offline judge) out of `leetcodetools.py` into a standalone module/script, so it can run from the command line outside Sublime (e.g. `python -m leetcodetools_offline solution.py`).
  把 parser（用例解析 + 链表/树/图构建 + 离线判题）从 `leetcodetools.py` 里分离成独立模块 / 脚本，使其可以脱离 Sublime、在命令行本地执行（例如 `python -m leetcodetools_offline solution.py`）。
- [ ] Class / design problems (e.g. **Min Stack**, where you implement a class with multiple methods) are not supported by the offline Run yet.
  「最小栈」这类 class 设计题（需要实现一个类、多个方法）暂不支持本地 Run。

## Directory structure / 目录结构

```
LeetCodeTools/
├── leetcodetools.py                # Main plugin (commands + GraphQL client + offline judge). 主插件（命令 + GraphQL 客户端 + 离线判题）
├── Default.sublime-commands        # Command-palette menu. 命令面板菜单
├── LeetCodeTools.sublime-settings  # Default settings. 默认配置
└── .python-version                 # Dev Python version. 开发环境 Python 版本
```

## Notes / 其它

- The login cookie is cached at `{working_dir}/.cache/cookie.json`; the problem list at `{working_dir}/.cache/problem_list.json`; study plans at `{working_dir}/.cache/study_plans.json` and `{working_dir}/.cache/study_plan_problems.json`.
  登录 Cookie 缓存在 `{working_dir}/.cache/cookie.json`，题目列表缓存在 `{working_dir}/.cache/problem_list.json`，题集缓存在 `{working_dir}/.cache/study_plans.json` 和 `{working_dir}/.cache/study_plan_problems.json`。
- When you pick a problem from a study plan (`Select from Problem Set`), submitting it also marks it as done in that study plan.
  从题集（Select from Problem Set）选的题，提交时也会在对应题集里标记完成。
