# 在 Windows 上注册「每天 21:00 触发 GitHub Digest」计划任务（不依赖 GHA schedule）。
# 需先设置环境变量 GITHUB_TOKEN（用户级或系统级均可）。
#
# 用法（管理员不必，普通用户即可）：
#   $env:GITHUB_TOKEN = "ghp_xxxx"
#   .\scripts\install_digest_scheduled_task.ps1
# 删除：Unregister-ScheduledTask -TaskName "XueqiuDailyDigest" -Confirm:$false

param(
    [string]$TaskName = "XueqiuDailyDigest",
    [string]$Time = "21:00"
)

$token = [Environment]::GetEnvironmentVariable("GITHUB_TOKEN", "User")
if (-not $token) { $token = [Environment]::GetEnvironmentVariable("GITHUB_TOKEN", "Machine") }
if (-not $token) {
    Write-Error "请先在「用户环境变量」中设置 GITHUB_TOKEN，或在本会话执行: `$env:GITHUB_TOKEN='ghp_...'"
    exit 1
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$triggerScript = Join-Path $repoRoot "scripts\trigger_github_workflow.ps1"
if (-not (Test-Path $triggerScript)) {
    Write-Error "找不到 $triggerScript"
    exit 1
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", "`"$triggerScript`""
) -WorkingDirectory $repoRoot

$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "已注册计划任务: $TaskName"
Write-Host "  每天 $Time 运行 Digest（workflow_dispatch）"
Write-Host "  脚本: $triggerScript"
Write-Host "测试立即运行: Start-ScheduledTask -TaskName $TaskName"
Write-Host "查看 Actions: https://github.com/Anjour-shao/xueqiu_portfolio/actions"
