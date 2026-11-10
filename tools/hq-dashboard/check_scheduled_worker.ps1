# Fictional-only worker check under Task Scheduler.
# Uses the signed-in user's Interactive logon; it does not prove Password/S4U logon readiness.
# Never changes an existing task and never asks for or handles a credential.
param([string]$ResultPath)
$ErrorActionPreference = 'Stop'
$taskLabel = 'JobSearchHQ-WorkerCheck-' + [guid]::NewGuid().ToString('N')
$pythonPath = (Get-Command python.exe -ErrorAction Stop).Source
$probePath = Join-Path $PSScriptRoot 'check_codex_worker.py'
if (-not $ResultPath) {
    $ResultPath = Join-Path $env:TEMP ($taskLabel + '.json')
}
$ResultPath = [IO.Path]::GetFullPath($ResultPath)
if ($ResultPath.Contains('"') -or (Test-Path -LiteralPath $ResultPath)) {
    throw 'Choose a new result filename without double quotes. Existing files are never overwritten.'
}
$taskArguments = '-I -B "' + $probePath + '" --result "' + $ResultPath + '"'
$taskAction = New-ScheduledTaskAction -Execute $pythonPath -Argument $taskArguments -WorkingDirectory $PSScriptRoot
$taskUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Limited
$taskSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 12)
$registered = $false
try {
    Register-ScheduledTask -TaskName $taskLabel -Action $taskAction -Principal $taskPrincipal -Settings $taskSettings | Out-Null
    $registered = $true
    Start-ScheduledTask -TaskName $taskLabel
    $deadline = (Get-Date).AddMinutes(11)
    while (-not (Test-Path -LiteralPath $ResultPath)) {
        if ((Get-Date) -gt $deadline) { throw 'Scheduled connectivity check timed out.' }
        Start-Sleep -Seconds 1
    }
    $check = Get-Content -LiteralPath $ResultPath -Raw -Encoding utf8 | ConvertFrom-Json
    $check | Add-Member -NotePropertyName schedulerLogonType -NotePropertyValue 'Interactive'
    $check | Add-Member -NotePropertyName provesPasswordLogon -NotePropertyValue $false
    $check | ConvertTo-Json | Set-Content -LiteralPath $ResultPath -Encoding utf8
    Write-Output ($check | ConvertTo-Json -Compress)
    if (-not $check.passed) { throw 'Scheduled worker check failed; see the redacted result.' }
} finally {
    if ($registered) {
        $taskState = (Get-ScheduledTask -TaskName $taskLabel).State
        if ($taskState -eq 'Running') { Stop-ScheduledTask -TaskName $taskLabel }
        Unregister-ScheduledTask -TaskName $taskLabel -Confirm:$false
    }
}
