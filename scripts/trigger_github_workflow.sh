#!/usr/bin/env bash
# 用 GitHub API 触发 Actions（不依赖 schedule）
# export GITHUB_TOKEN=ghp_xxxx
# ./scripts/trigger_github_workflow.sh [workflow_file] [ref]

set -euo pipefail
REPO="${GITHUB_REPO:-Anjour-shao/xueqiu_portfolio}"
WORKFLOW="${1:-daily_digest.yml}"
REF="${2:-main}"

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "请设置 GITHUB_TOKEN（PAT，Actions 写权限）" >&2
  exit 1
fi

curl -fsS -X POST \
  -H "Authorization: Bearer ${GITHUB_TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches" \
  -d "{\"ref\":\"${REF}\"}"

echo "已请求触发: ${WORKFLOW} @ ${REF}"
echo "Actions: https://github.com/${REPO}/actions"
