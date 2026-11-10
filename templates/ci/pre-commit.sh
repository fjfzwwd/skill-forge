#!/usr/bin/env bash
# skill-forge pre-commit hook — copy to .git/hooks/pre-commit and chmod +x.
# 检出：已编译 skill 的源/产物漂移、回归基线不一致、未人审的机器草案。
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
FORGE="${FORGE:-$ROOT/skill-forge/scripts/forge.py}"

if [ ! -f "$FORGE" ]; then
  echo "skill-forge: 未找到 $FORGE，跳过门禁" >&2
  exit 0
fi

# --allow-empty：仓库里还没有已编译 skill 时不阻塞提交。
python "$FORGE" ci "$ROOT" --strict --allow-empty --format text
