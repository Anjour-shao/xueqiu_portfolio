# 用 GitHub API 手动/定时触发 Actions（不依赖仓库内 schedule）。
# 用法：
#   $env:GITHUB_TOKEN = "ghp_xxxx"   # Classic PAT：勾选 repo；或 Fine-grained：Actions Read/Write
#   .\daily_digest\trigger_github_workflow.ps1
#   .\daily_digest\trigger_github_workflow.ps1 -WorkflowFile schedule_smoke_test.yml
#
# Windows 计划任务：每天 21:00 执行本脚本，等效于「定时 Digest」。

param(
    [string]$Repo = "Anjour-shao/xueqiu_portfolio",
    [string]$WorkflowFile = "daily_digest.yml",
    [string]$Ref = "main"
)

$token = $env:GITHUB_TOKEN
if (-not $token) {
    Write-Error "请先设置环境变量 GITHUB_TOKEN（PAT，需 Actions 写权限）。"
    exit 1
}

$uri = "https://api.github.com/repos/$Repo/actions/workflows/$WorkflowFile/dispatches"
$body = @{ ref = $Ref } | ConvertTo-Json

try {
    Invoke-RestMethod -Method Post -Uri $uri -Headers @{
        Authorization = "Bearer $token"
        Accept        = "application/vnd.github+json"
        "X-GitHub-Api-Version" = "2022-11-28"
    } -Body $body -ContentType "application/json"
    Write-Host "已请求触发: $WorkflowFile @ $Ref"
    Write-Host "请到 Actions 查看: https://github.com/$Repo/actions"
} catch {
    Write-Error $_
    exit 1
}
