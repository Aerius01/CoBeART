#!/usr/bin/env node
// Architecture fitness checks. Run from anywhere: node scripts/fitness.mjs
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const HUB_FILE = 'cobeart-app/electron/hub.js';

/** @typedef {{file: string, line: number, rule: string, message: string}} Violation */

/** @returns {Violation[]} one violation per line of `text` matching `pattern`, unless `allowed(line)` */
function scanLines(relPath, text, rule, pattern, message, allowed = () => false) {
  return text.split('\n').flatMap((line, i) =>
    pattern.test(line) && !allowed(line) ? [{ file: relPath, line: i + 1, rule, message }] : [],
  );
}

export function hubOnlyServer(relPath, text) {
  if (relPath === HUB_FILE) return [];
  return scanLines(relPath, text, 'hub-only-server', /new Server\(/,
    `only ${HUB_FILE} may construct a Socket.IO Server`);
}

// Matched on line content so the allowlist survives edits that shift line numbers.
const PORT_ALLOWLIST = [
  /const headDepth = .* : 3000;/,
  /e\.g\. http:\/\/127\.0\.0\.1:3000\b/,
];

export function noArenaPortLiterals(relPath, text) {
  return scanLines(relPath, text, 'no-arena-port-literals', /3000/,
    'arena or port literal; read it from config/cobeart.yaml instead',
    (line) => PORT_ALLOWLIST.some((re) => re.test(line)));
}

const HOT_PATH_FILES = [
  'cobeart/input/optitrack/sender.py',
  'cobeart/input/optitrack/metrics.py',
  'cobeart/input/optitrack/transform.py',
];

export function noPrintHotPath(relPath, text) {
  const inScope = HOT_PATH_FILES.includes(relPath);
  if (!inScope) return [];
  return scanLines(relPath, text, 'no-print-hot-path', /(^|[^\w.])print\(/, 'use logging, not print(), on the hot path');
}

// Each module may not import the modules it must stay independent of (D11). Matches `import cobeart.x` and `from cobeart.x import`.
const FORBIDDEN_IMPORTS = [
  { dir: 'cobeart/audio/', modules: ['input'] },
  { dir: 'cobeart/input/', modules: ['audio'] },
  { dir: 'cobeart/simulator/', modules: ['input', 'audio'] },
];

export function moduleBoundaries(relPath, text) {
  const forbidden = FORBIDDEN_IMPORTS.find((f) => relPath.startsWith(f.dir))?.modules ?? [];
  if (forbidden.length === 0) return [];
  const pattern = new RegExp(`^\\s*(?:from|import)\\s+cobeart\\.(?:${forbidden.join('|')})\\b`);
  return scanLines(relPath, text, 'module-boundaries', pattern,
    `${relPath.split('/')[1]} must not import cobeart.${forbidden.join(' or cobeart.')}`);
}

const isTestPath = (rel) => rel.split('/').some((p) => p === 'tests' || p === 'test');

/** Each rule: which files it applies to, and its check. */
export const RULES = [
  { check: hubOnlyServer, applies: (rel) => rel.startsWith('cobeart-app/') && rel.endsWith('.js') },
  {
    check: noArenaPortLiterals,
    applies: (rel) =>
      !isTestPath(rel) && /\.(js|py|html)$/.test(rel) &&
      (rel.startsWith('cobeart-app/public/') || rel.startsWith('cobeart-app/electron/') ||
        rel === 'cobeart-app/server.js' || rel.startsWith('cobeart/')),
  },
  {
    check: noPrintHotPath,
    applies: (rel) => !isTestPath(rel) && rel.endsWith('.py'),
  },
  { check: moduleBoundaries, applies: (rel) => rel.endsWith('.py') },
];

/** @returns {string[]} repo-relative posix paths of files under `dir`, skipping node_modules */
function walk(dir) {
  return readdirSync(path.join(REPO_ROOT, dir), { withFileTypes: true }).flatMap((e) => {
    const rel = `${dir}/${e.name}`;
    if (e.isDirectory()) return e.name === 'node_modules' || e.name.startsWith('.') ? [] : walk(rel);
    return e.isFile() ? [rel] : [];
  });
}

export function runAll() {
  const files = [...walk('cobeart-app'), ...walk('cobeart')];
  return files.flatMap((rel) => {
    const rules = RULES.filter((r) => r.applies(rel));
    if (rules.length === 0) return [];
    const text = readFileSync(path.join(REPO_ROOT, rel), 'utf8');
    return rules.flatMap((r) => r.check(rel, text));
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const violations = runAll();
  if (violations.length > 0) {
    for (const v of violations) console.error(`${v.file}:${v.line} ${v.rule} ${v.message}`);
    process.exit(1);
  }
  console.log(`fitness: ${RULES.length} rules, 0 violations`);
}
