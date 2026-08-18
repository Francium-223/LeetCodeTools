# LeetCodeTools for Sublime Text

LeetCodeTools is A [Sublime Text](www.sublimetext.com) plugin for practicing [LeetCode](leetcode.cn) without leaving your editor. It lets you search problems, fetch problems and official solutions, run your code offline against the official examples, submit directly to LeetCode, and pick problems from study plans like [Hot 100](https://leetcode.cn/studyplan/top-100-liked/) and [Top Interview 150](https://leetcode.cn/studyplan/top-interview-150/). Problems and solutions are saved locally as Markdown files; log in once and you're good to go.

LeetCodeTools 是一个 [Sublime Text](https://www.sublimetext.com/) 插件，让你不用离开编辑器就能刷[力扣](leetcode.cn)（LeetCode）。它支持按题号或关键字搜索题目、拉取题目和官方题解、在本地离线运行代码并与官方示例对比、一键提交到力扣判题，还能从「[热题 100](https://leetcode.cn/studyplan/top-100-liked/)」「[面试经典 150 题](https://leetcode.cn/studyplan/top-interview-150/)」等题集里选题。题目和题解都会以 Markdown 文件保存到本地，登录一次即可长期使用。
一个让你不用离开编辑器、直接在 [Sublime Text](https://www.sublimetext.com/) 里刷[力扣（LeetCode.cn）](https://leetcode.cn)的插件：搜索题目、拉取题目与官方题解、离线运行、直接提交。

## Features / 功能

| Command 命令 | Description 说明 |
|------|------|
| `LeetCode Tools: Login` | Open Chrome/Edge to log in and grab the session cookie. 打开 Chrome/Edge 登录并抓取登录 Cookie |
| `LeetCode Tools: Search` | Search problems by id or title keyword. 按题号 / 标题关键字搜索题目 |
| `LeetCode Tools: Fetch` | Fetch a problem by id (e.g. `1` or `1 python3`). 输入题号拉取题目（如 `1` 或 `1 python3`） |
| `LeetCode Tools: Fetch (Force)` | Force re-fetch and overwrite existing files. 强制重新拉取，覆盖已有文件 |
| `LeetCode Tools: Update` | Rebuild the local problem-list and study-plan caches. 重建本地题目列表和题集缓存 |
| `LeetCode Tools: Run` | Run your code offline and compare with the official examples. 离线运行代码，与官方示例对比 |
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

3. Install a system **Python 3** and add it to PATH. It is required by Login and the offline Run.
   安装一个系统 **Python 3** 并加入 PATH。登录和离线 Run 都需要它。

4. To use **Login**, install `DrissionPage` into that Python and make sure Chrome or Edge is installed:
   要使用**登录**功能，请在该 Python 里安装 `DrissionPage`，并确保装有 Chrome 或 Edge：

   ```bash
   pip install DrissionPage
   ```

5. Press `Ctrl+Shift+P`, run `LeetCode Tools: Login`, log in in the browser, then click OK.
   按 `Ctrl+Shift+P`，运行 `LeetCode Tools: Login`，在浏览器里登录后点确定即可。

> Note: Search / Fetch / Update / Submit need the login cookie; Open in Browser, Run, and Fetch Official Explanations do not (the official solution uses the public GraphQL API).
> 说明：Search / Fetch / Update / Submit 需要登录 Cookie；Open in Browser、Run、Fetch Official Explanations 不需要登录（其中官方题解走的是公开 GraphQL 接口）。

## Configuration / 配置

Settings are edited via `Preferences: LeetCodeTools Settings` in the command palette, or `Preferences → Package Settings → LeetCodeTools → Settings` in the menu:
设置通过命令面板的 `Preferences: LeetCodeTools Settings`，或菜单 `Preferences → Package Settings → LeetCodeTools → Settings` 编辑：

| Key 配置项 | Default 默认值 | Description 说明 |
|--------|--------|------|
| `working_dir` | `~/leetcode` | Where problems are saved. 题目保存目录 |
| `default_lang` | `python3` | Default language for Fetch. Fetch 默认语言 |
| `language` | `zh` | Problem language (`zh` / `en`). 题目语言（`zh` / `en`） |
| `site` | `cn` | `cn` = leetcode.cn, `com` = leetcode.com |
| `cache_age_days` | `7` | Cache age in days for the problem list and study plans; auto-refreshes when expired. 题目列表与题集的缓存天数，过期自动刷新 |
| `run_timeout` | `1` | Time limit in seconds for the offline Run (0 = no limit). 离线 Run 的超时秒数（0 表示不限时） |

## Supported languages / 支持的语言

These languages can be used with Fetch (code template) and Submit; the offline Run is Python-only (see limitations below).
以下语言可用于 Fetch（代码模板）和 Submit；离线 Run 目前仅支持 Python（见下方限制）。

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

1. **Log in** (once): `LeetCode Tools: Login` → log in in browser → click OK.
   **登录**（只需一次）：`LeetCode Tools: Login` → 浏览器登录 → 点确定。

2. **Fetch a problem**: `LeetCode Tools: Fetch` → enter an id (e.g. `1` or `1 python3`); it opens the `.md` and code file.
   **拉题**：`LeetCode Tools: Fetch` → 输入题号（如 `1` 或 `1 python3`），自动打开 `.md` 和代码文件。

3. **Solve it**: write your solution in the code file.
   **做题**：在代码文件里写解法。

4. **Run locally**: `LeetCode Tools: Run` shows a comparison with the official examples in the output panel.
   **本地跑**：`LeetCode Tools: Run`，在输出面板看到和官方示例的对比结果。

5. **Submit**: `LeetCode Tools: Submit`; failed cases are appended to the local `_in.json` / `_out.json` for offline replay.
   **提交**：`LeetCode Tools: Submit`，失败用例会自动追加到本地 `_in.json` / `_out.json`，方便离线复现。

6. **Read the solution**: `LeetCode Tools: Fetch Official Explanations` generates and opens `xxx_explanation.md`.
   **看题解**：`LeetCode Tools: Fetch Official Explanations`，生成并打开 `xxx_explanation.md`。

## Generated files / 生成的文件

Each problem generates these files under `working_dir`:
每题在 `working_dir` 下生成：

| File 文件 | Content 内容 |
|------|------|
| `{slug}.md` | Problem description (HTML → Markdown). 题目描述（HTML 转成 Markdown） |
| `{slug}.py` | Official code template. 官方代码模板 |
| `{slug}.json` | Metadata (id / metaData / examples). 元数据（题号 / metaData / 示例用例） |
| `{slug}_in.json` | Parsed input cases. 解析后的输入用例 |
| `{slug}_out.json` | Expected outputs. 预期输出 |
| `{slug}_explanation.md` | Official solution (from Fetch Official Explanations). 官方题解（由 Fetch Official Explanations 生成） |
| `{slug}_images/` | Images downloaded from the problem description. 题目描述里下载的本地图片 |
| `{slug}_explanation_images/` | Images downloaded from the official solution. 官方题解里下载的本地图片 |

## Current limitations / 当前限制

- Offline **Run currently supports only Python**: the local judge runs your code with Python `exec` and includes built-in parsing for `ListNode` / `TreeNode` / `Node`. Other languages (java / cpp / javascript / golang, …) can only Fetch templates and Submit — not run locally.
- **离线 Run 目前仅支持 Python**：本地判题用 Python `exec` 运行你的代码，并内置了 `ListNode` / `TreeNode` / `Node` 的用例解析。其它语言（java / cpp / javascript / golang 等）目前只能 Fetch 代码模板和 Submit 提交，不能本地 Run。
- Only the China site ([leetcode.cn](https://leetcode.cn)) is supported; the global site ([leetcode.com](https://leetcode.com)) is planned but not yet implemented.
- 目前只支持中国版（[leetcode.cn](https://leetcode.cn)）；美国版/国际版（[leetcode.com](https://leetcode.com)）待实现。

## Roadmap / 待办

- [ ] Separate the parser (test-case parsing + linked-list/tree/graph building + offline judge) out of `leetcodetools.py` into a standalone module/script, so it can run from the command line outside Sublime (e.g. `python -m leetcodetools_offline solution.py`).
  把 parser（用例解析 + 链表/树/图构建 + 离线判题）从 `leetcodetools.py` 里分离成独立模块 / 脚本，使其可以脱离 Sublime、在命令行本地执行（例如 `python -m leetcodetools_offline solution.py`）。

## Directory structure / 目录结构

```
LeetCodeTools/
├── leetcodetools.py                # Main plugin (commands + GraphQL client + offline judge). 主插件（命令 + GraphQL 客户端 + 离线判题）
├── cookie_grabber.py               # Login cookie grabber (system Python + DrissionPage). 登录抓 Cookie（系统 Python + DrissionPage）
├── Default.sublime-commands        # Command-palette menu. 命令面板菜单
├── LeetCodeTools.sublime-settings  # Default settings. 默认配置
└── .python-version                 # Dev Python version. 开发环境 Python 版本
```

## Notes / 其它

- The login cookie is cached at `~/.leetcode_tools_cache/cookie.json`; the problem list at `~/.leetcode_tools_cache/problem_list.json`; study plans at `~/.leetcode_tools_cache/study_plans.json` and `~/.leetcode_tools_cache/study_plan_problems.json`.
  登录 Cookie 缓存在 `~/.leetcode_tools_cache/cookie.json`，题目列表缓存在 `~/.leetcode_tools_cache/problem_list.json`，题集缓存在 `~/.leetcode_tools_cache/study_plans.json` 和 `~/.leetcode_tools_cache/study_plan_problems.json`。
- Images in problem descriptions and official solutions are downloaded locally (into `{slug}_images/` and `{slug}_explanation_images/`), so you can view them without opening a browser.
  题目描述和官方题解里的图片会下载到本地（`{slug}_images/` 和 `{slug}_explanation_images/`），看图不用开浏览器。
