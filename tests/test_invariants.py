"""架构不变量检查（docs/architecture.md 第 2 节）。

这些检查只约束结构，不替代端到端行为测试。
白名单只能减少：修好一处就从白名单删除一项，不允许为新代码新增豁免。
"""

import ast
import pathlib

PKG = pathlib.Path(__file__).resolve().parents[1] / "agent_gavel"


def _modules():
    for p in sorted(PKG.rglob("*.py")):
        yield p.relative_to(PKG).as_posix(), ast.parse(
            p.read_text(encoding="utf-8"), filename=str(p))


def _assigned_names(tree):
    """模块级与类级的赋值目标名。"""
    names = set()

    def scan(body):
        for node in body:
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
            if isinstance(node, ast.ClassDef):
                scan(node.body)

    scan(tree.body)
    return names


# ---------------- I1：只有 core/config.py 读环境变量 ----------------

# 阶段 1 结束时清零。
ENV_WHITELIST = {
    "runtime/policies.py",
    "runtime/artifacts.py",
    "runtime/sessions.py",
    "runtime/profiles.py",
    "runtime/browser_process.py",
    "tools/browser_tools.py",
    "workflows/loader.py",
}


def _uses_env(tree):
    for n in ast.walk(tree):
        if not isinstance(n, ast.Attribute) or not isinstance(n.value, ast.Name):
            continue
        if n.value.id == "os" and n.attr in ("environ", "getenv"):
            return True
    return False


def test_i1_env_only_in_config():
    offenders = {rel for rel, tree in _modules() if _uses_env(tree)}
    allowed = ENV_WHITELIST | {"core/config.py"}
    extra = offenders - allowed
    assert not extra, f"环境变量读取出现在非主人模块：{sorted(extra)}"


# ---------------- I2：backend 页面函数首参为 handle ----------------

# 阶段 3/4 结束时清零。
PAGE_FIRST_WHITELIST = {
    "retry_on_nav_race", "resolve", "act", "page_scroll", "read", "text",
    "snapshot", "explore", "find_text", "page_meta", "screenshot",
    "page_signature", "evaluate_checks",
}


def test_i2_backend_takes_handle():
    offenders = set()
    for rel, tree in _modules():
        if not rel.startswith("backends/"):
            continue
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            for fn in cls.body:
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if fn.name.startswith("_"):
                    continue
                if any(isinstance(d, ast.Name) and d.id == "staticmethod"
                       for d in fn.decorator_list):
                    continue
                args = [a.arg for a in fn.args.args]
                if len(args) >= 2 and args[1] == "page":
                    offenders.add(fn.name)
    extra = offenders - PAGE_FIRST_WHITELIST
    assert not extra, f"backend 页面函数未使用 handle 首参：{sorted(extra)}"


# ---------------- I3/I4：词表只有一个主人 ----------------

VOCAB_OWNERS = {
    "ACTIONS": "core/actions.py",
    "ACTION_TYPES": "core/actions.py",
    "READ_KINDS": "core/checks.py",
    "OPS": "core/checks.py",
    "_READ_PREFIX": "core/checks.py",
    "_OP_SUFFIXES": "core/checks.py",
    "_READ_KINDS": "core/checks.py",
}

# 阶段 3 结束时清零。
VOCAB_WHITELIST = {
    ("workflows/schema.py", "ACTION_TYPES"),
    ("backends/playwright_backend.py", "_READ_PREFIX"),
    ("backends/playwright_backend.py", "_OP_SUFFIXES"),
    ("backends/playwright_backend.py", "_READ_KINDS"),
}


def test_i3_i4_vocabulary_single_owner():
    found = set()
    for rel, tree in _modules():
        for name in _assigned_names(tree) & set(VOCAB_OWNERS):
            found.add((rel, name))
    bad = []
    for rel, name in found:
        if (rel, name) in VOCAB_WHITELIST:
            continue
        if rel != VOCAB_OWNERS[name]:
            bad.append((rel, name, VOCAB_OWNERS[name]))
    assert not bad, f"词表出现在非主人模块：{bad}"


# ---------------- I5：对外 status 取值受限 ----------------

STATUS_VALUES = {"ok", "pass", "fail", "ambiguous", "error", "blocked"}
# 阶段 4 归一，清零。
STATUS_WHITELIST = {("page_tools.py", "no_match")}


def test_i5_status_values():
    bad = []
    for rel, tree in _modules():
        if not rel.startswith("tools/"):
            continue
        for n in ast.walk(tree):
            if not isinstance(n, ast.Dict):
                continue
            for k, v in zip(n.keys, n.values):
                if not (isinstance(k, ast.Constant) and k.value == "status"):
                    continue
                if not (isinstance(v, ast.Constant) and isinstance(v.value, str)):
                    continue
                if v.value in STATUS_VALUES:
                    continue
                if (rel.rsplit("/", 1)[-1], v.value) in STATUS_WHITELIST:
                    continue
                bad.append((rel, v.value))
    assert not bad, f"未登记的 status 取值：{bad}"


# ---------------- I6：模板写路径只接受精确 id ----------------

# 阶段 1 结束时清零。
WRITE_PATH_WHITELIST = {("delete", "get"), ("record_run", "get")}


def test_i6_loader_write_paths_exact():
    tree = ast.parse((PKG / "workflows/loader.py").read_text(encoding="utf-8"))
    bad = []
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        if fn.name not in ("save", "delete", "record_run"):
            continue
        called = {c.func.id for c in ast.walk(fn)
                  if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        for name in called & {"get", "resolve_id"}:
            if (fn.name, name) not in WRITE_PATH_WHITELIST:
                bad.append((fn.name, name))
    assert not bad, f"写路径使用了宽松查找：{bad}"


# ---------------- I7：async 函数内不得直接阻塞 ----------------

def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


BLOCKING_CALLS = {
    "time.sleep", "subprocess.run", "subprocess.call",
    "urllib.request.urlopen", "os.walk", "shutil.copytree",
    "shutil.copy2", "shutil.move",
}

# 阶段 1 结束时清零。
BLOCKING_WHITELIST = {("runtime/browser_process.py", "_spawn_managed",
                       "urllib.request.urlopen")}


def test_i7_no_direct_blocking_in_async():
    bad = []
    for rel, tree in _modules():
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and _dotted(sub.func) in BLOCKING_CALLS:
                    key = (rel, node.name, _dotted(sub.func))
                    if key not in BLOCKING_WHITELIST:
                        bad.append(key)
    assert not bad, f"async 函数内直接调用阻塞 API：{bad}"


# ---------------- I8：包依赖单向 ----------------

# 阶段 2 结束时清零。
IMPORT_WHITELIST = {("runtime/browser_runtime.py", "..workflows.recorder")}


def test_i8_runtime_does_not_import_upper_layers():
    bad = []
    for rel, tree in _modules():
        if not rel.startswith("runtime/"):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            mod = "." * node.level + (node.module or "")
            if any(x in mod for x in ("workflows", "backends", "tools")):
                if (rel, mod) in IMPORT_WHITELIST:
                    continue
                bad.append((rel, mod))
    assert not bad, f"runtime 反向依赖上层：{bad}"


def test_i8_core_does_not_import_layers():
    bad = []
    for rel, tree in _modules():
        if not rel.startswith("core/"):
            continue
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.ImportFrom):
                mods.append("." * node.level + (node.module or ""))
            for mod in mods:
                if any(x in mod for x in ("runtime", "workflows", "backends", "tools")):
                    bad.append((rel, mod))
    assert not bad, f"core 依赖了上层：{bad}"
