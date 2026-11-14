#!/usr/bin/env node
// 把某个提交里的被跟踪文本文件按依赖顺序导出成单个纯文本文件，并自校验往返一致。
// 用法: node tools/export-codebase.mjs <输出文件> [提交]
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, isAbsolute, resolve } from 'node:path';

// 不进入产物的文件：依赖锁定、许可证、归档实现、历史设计稿、机器日志。
const EXCLUDED_PREFIX = ['legacy/', 'docs/validation/'];
const EXCLUDED_FILE = new Set([
  'uv.lock',
  'LICENSE',
  'docs/tech-plan.md',
  'docs/agent-gavel-discussion.md',
  'docs/wechat-perf-optimization.md',
  'docs/release-readiness-2026-10-02.md',
]);
function excluded(path) {
  return EXCLUDED_FILE.has(path) ||
    EXCLUDED_PREFIX.some(p => path.startsWith(p) && (p !== 'docs/validation/' || path.endsWith('.json')));
}

// 阅读顺序：被依赖者在前；同一目录内先给出口清单，再按名字排序。
const SEGMENTS = [
  { label: 'S1 项目定位', files: ['README.md', 'AGENTS.md', 'CONTRIBUTING.md', 'CHANGELOG.md'] },
  {
    label: 'S2 构建与运行',
    files: [
      'pyproject.toml', '.python-version', 'run-mcp.sh', 'opencode.json',
      'Dockerfile', '.dockerignore', '.gitignore',
      '.github/workflows/ci.yml', '.github/PULL_REQUEST_TEMPLATE.md',
      'tools/export-codebase.mjs',
    ],
  },
  {
    label: 'S3 入口与守护',
    files: [
      'agent_gavel/__init__.py', 'agent_gavel/main.py',
      'agent_gavel/daemon.py', 'agent_gavel/remote.py',
    ],
  },
  {
    label: 'S4 核心概念',
    dir: 'agent_gavel/core',
    first: ['__init__.py', 'actions.py', 'checks.py', 'config.py', 'errors.py', 'targets.py', 'results.py', 'logging.py'],
  },
  {
    label: 'S5 工具层',
    dir: 'agent_gavel/tools',
    first: ['__init__.py', 'catalog.py', 'common.py', 'schemas.py', 'results.py', 'describe.py', 'migration.py'],
  },
  {
    label: 'S6 运行时',
    dir: 'agent_gavel/runtime',
    first: ['__init__.py', 'browser_process.py', 'browser_runtime.py', 'sessions.py', 'contexts.py', 'pages.py', 'queue.py'],
  },
  {
    label: 'S7 Playwright 后端',
    dir: 'agent_gavel/backends',
    first: ['__init__.py', 'playwright/__init__.py', 'playwright/actions.py', 'playwright/locator.py', 'playwright/reader.py', 'playwright/observation.py', 'playwright/assertion.py'],
  },
  {
    label: 'S8 工作流',
    dir: 'agent_gavel/workflows',
    first: ['__init__.py', 'schema.py', 'loader.py', 'compiler.py', 'runner.py'],
  },
  { label: 'S9 测试站点与夹具', dir: 'tests/webapp', first: [] },
  { label: 'S10 夹具文件', dir: 'tests/fixtures', first: [] },
  {
    label: 'S11 测试',
    dir: 'tests',
    skip: ['webapp/', 'fixtures/'],
    first: ['conftest.py', 'mcp_client.py', 'mcp_scenarios.py', 'tool_fixture.py', 'benchlib.py', 'e2e_mcp.py', 'mcp_live_smoke.py'],
  },
  {
    label: 'S12 当前文档',
    files: [
      'docs/architecture.md', 'docs/tools.md', 'docs/roadmap.md',
      'docs/internal-beta.md', 'docs/detection.md',
      'docs/validation/README.md', 'docs/validation/opencode-install.md',
      'constraints/constraints.yaml',
    ],
  },
  {
    label: 'S13 第三方说明',
    files: [
      'third_party/computer-use-linux/README.md',
      'third_party/computer-use-linux/fast_app_filter.patch',
    ],
  },
  { label: 'S14 阶段记录', dir: 'stages' },
];

const git = (...args) => execFileSync('git', args, { encoding: 'buffer', maxBuffer: 64 * 1024 * 1024 });
const root = git('rev-parse', '--show-toplevel').toString().trim();
const commit = process.argv[3] ?? 'HEAD';
const sha = git('rev-parse', commit).toString().trim();
const target = resolve(root, process.argv[2] ?? `exports/agent-gavel-code-${sha.slice(0, 7)}.txt`);

const tracked = git('ls-files', '-z').toString().split('\0').filter(Boolean);
const wanted = tracked.filter(p => !excluded(p));

// 展开目录段：先放 first 指定的文件，再按名字排序放其余。
const plan = [];
for (const segment of SEGMENTS) {
  if (segment.files) {
    plan.push({ label: segment.label, files: [...segment.files] });
    continue;
  }
  const prefix = `${segment.dir}/`;
  const skip = segment.skip ?? [];
  const kept = wanted.filter(p => {
    if (!p.startsWith(prefix)) return false;
    const rest = p.slice(prefix.length);
    return !skip.some(s => rest.startsWith(s));
  });
  const members = kept.filter(p => !p.slice(prefix.length).includes('/')).map(p => p.slice(prefix.length));
  const nested = kept.filter(p => p.slice(prefix.length).includes('/'));
  const ordered = [];
  for (const name of segment.first) {
    const full = `${prefix}${name}`;
    if (!wanted.includes(full)) throw new Error(`${segment.dir} 缺少 ${name}`);
    ordered.push(full);
  }
  const taken = new Set(ordered);
  members.filter(n => !taken.has(`${prefix}${n}`)).sort().forEach(n => ordered.push(`${prefix}${n}`));
  nested.filter(p => !taken.has(p)).sort().forEach(p => ordered.push(p));
  plan.push({ label: segment.label, files: ordered });
}
const placed = plan.flatMap(s => s.files);

const duplicate = placed.filter((p, i) => placed.indexOf(p) !== i);
const missing = wanted.filter(p => !placed.includes(p));
const stale = placed.filter(p => !wanted.includes(p));
if (duplicate.length || missing.length || stale.length) {
  console.error(JSON.stringify({ duplicate, missing, stale }, null, 2));
  throw new Error('导出顺序表与被跟踪文件不一致；先更新 SEGMENTS');
}

const body = [];
const index = [];
for (const segment of plan) {
  body.push(`### ${segment.label}`);
  for (const path of segment.files) {
    const content = git('show', `${sha}:${path}`).toString('utf8');
    if (content.includes('\r')) throw new Error(`${path} 含 CR，已中止`);
    if (!content.endsWith('\n')) throw new Error(`${path} 末尾缺少换行，已中止`);
    const start = body.length + 1;
    body.push(path);
    for (const line of content.slice(0, -1).split('\n')) body.push(line === '' ? '  ' : `  ${line}`);
    body.push('');
    index.push({ path, start, end: body.length - 1 });
  }
}

const meta = [
  '# agent-gavel 代码包',
  `# 仓库: ${root}`,
  `# 提交: ${sha}`,
  `# 生成时间: ${new Date().toISOString()}`,
  `# 文件数: ${placed.length}`,
  `# 正文字节: ${Buffer.byteLength(body.join('\n') + '\n')}`,
  '# 排序: 被依赖者在前；同一目录内先出口清单，再按名字',
  '# 排除: uv.lock、LICENSE、legacy/（归档通道，不打包不运行）、docs/tech-plan.md 与 docs/agent-gavel-discussion.md（历史设计稿）、docs/validation/*.json（机器日志）、未跟踪的 .venv/ 与 .runtime/',
  '# 读法: 路径行不缩进；代码行以两个空格开头，空行写成两个空格；### 开头是分段标记',
  '## 文件索引',
];
const offset = meta.length + index.length + 1;
const indexLines = index.map((e, i) =>
  `${String(i + 1).padStart(4, '0')}  行 ${offset + e.start}-${offset + e.end}  ${e.path}`);
const text = [...meta, ...indexLines, '## 正文', ...body].join('\n') + '\n';

const textLines = text.split('\n');
const bodyAt = textLines.indexOf('## 正文');
if (bodyAt < 0) throw new Error('产物缺少正文标记');
const rebuilt = new Map();
let current = null;
for (const line of textLines.slice(bodyAt + 1)) {
  if (line === '') continue;
  if (!line.startsWith(' ') && !line.startsWith('#')) {
    current = [];
    rebuilt.set(line, current);
    continue;
  }
  if (line.startsWith('  ')) {
    if (!current) throw new Error(`孤立代码行: ${line.slice(0, 40)}`);
    current.push(line.slice(2));
  }
}
const mismatches = [];
for (const path of placed) {
  const expected = createHash('sha256').update(git('show', `${sha}:${path}`)).digest('hex');
  const actual = createHash('sha256').update(Buffer.from(rebuilt.get(path).join('\n') + '\n')).digest('hex');
  if (expected !== actual) mismatches.push(path);
}
if (rebuilt.size !== placed.length) throw new Error(`还原文件数 ${rebuilt.size} ≠ ${placed.length}`);
if (mismatches.length) throw new Error(`往返不一致: ${mismatches.join(', ')}`);

mkdirSync(dirname(target), { recursive: true });
writeFileSync(target, text);

console.log(JSON.stringify({
  output: isAbsolute(target) ? target : resolve(root, target),
  commit: sha,
  files: placed.length,
  bodyLines: body.length,
  bytes: Buffer.byteLength(text),
  roundTrip: 'ok',
}, null, 2));
