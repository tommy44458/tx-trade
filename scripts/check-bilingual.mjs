import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
if (Number(process.versions.node.split('.')[0]) < 24) {
  throw new Error('Bilingual development checks require Node.js 24 or newer.');
}
const python = join(root, 'apps/api/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
if (!existsSync(python)) {
  throw new Error('Set up the API development environment first: cd apps/api && uv sync');
}
function check(command, args, cwd = root) {
  const result = spawnSync(command, args, {
    cwd, stdio: 'inherit', env: { ...process.env, PYTHONPATH: join(root, 'apps/api/src') },
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}

// These checks use temporary databases and fixed evidence. No model requests,
// source refreshes, application settings, or private credentials are accessed.
check(python, ['-m', 'trade_helper.prompts', 'check']);
check(python, ['-m', 'pytest', '-q',
  'tests/test_prompt_registry.py', 'tests/test_bilingual_jobs.py',
  'tests/test_bilingual_offline_evaluation.py', 'tests/test_macro_translation.py',
  'tests/test_report_macro_locale.py', 'tests/test_worker_error_locale.py',
], join(root, 'apps/api'));
check(process.execPath, ['--test',
  'apps/web/tests/i18n.test.mjs', 'apps/web/tests/python-reference-text.test.mjs',
  'apps/desktop/tests/locales.test.mjs',
]);
