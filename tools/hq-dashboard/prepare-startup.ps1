# Prepares a reviewable startup task; does not register it.
[CmdletBinding()]
param([string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
# Settings keys follow the shared loader contract.
$ownerSettings = @{}
foreach ($settingsFile in @('settings.example.json', 'settings.json')) {
    $settingsPath = Join-Path $repository $settingsFile
    if (Test-Path -LiteralPath $settingsPath) {
        $settingsObject = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($property in $settingsObject.PSObject.Properties) {
            if ($property.Name.StartsWith('_')) { continue }
            if ($settingsFile -eq 'settings.json' -and -not $ownerSettings.ContainsKey($property.Name)) { throw ('Unknown setting: ' + $property.Name) }
            $ownerSettings[$property.Name] = $property.Value
        }
    }
}
$taskAuthor = [Security.SecurityElement]::Escape($ownerSettings.task_author)
$taskPrincipal = [Security.SecurityElement]::Escape($ownerSettings.task_principal)
$stateDirectory = Join-Path $env:LOCALAPPDATA 'JobSearchHQ'
$tailscale = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
if (-not (Test-Path -LiteralPath $tailscale)) { throw 'Install and sign in to Tailscale first.' }
$network = & $tailscale status --json | ConvertFrom-Json
if ($network.BackendState -ne 'Running' -or -not $network.Self.DNSName) { throw 'Tailscale is not signed in.' }
$dnsName = $network.Self.DNSName.TrimEnd('.')
if ($dnsName -notmatch '^[a-zA-Z0-9.-]+\.ts\.net$') { throw 'Unexpected Tailscale hostname.' }
$python = Get-Command python -CommandType Application | Select-Object -First 1 -ExpandProperty Source
$settings = @{ repository = $repository; python = $python; origin = ('https://' + $dnsName) }
New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
$settings | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $stateDirectory 'server-settings.json') -Encoding UTF8
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repository 'work\pc-hosting' }
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$sid = [Security.SecurityElement]::Escape($identity.User.Value)
$workingDirectory = [Security.SecurityElement]::Escape($repository)
$scriptPath = Join-Path $PSScriptRoot 'run-server.py'
$arguments = [Security.SecurityElement]::Escape(('-I -B "' + $scriptPath + '"'))
$pythonCommand = [Security.SecurityElement]::Escape($python)
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<!-- No credential is stored in this file. -->
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
 <RegistrationInfo><Author>$taskAuthor</Author><Description>Job Search HQ on this PC. Password-protected loopback server behind Tailscale.</Description><URI>\JobSearchHQ-Server</URI></RegistrationInfo>
 <Triggers><BootTrigger><Enabled>true</Enabled><Delay>PT30S</Delay></BootTrigger></Triggers>
 <Principals><Principal id="$taskPrincipal"><UserId>$sid</UserId><LogonType>Password</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
 <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>true</StartWhenAvailable><AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled><Hidden>true</Hidden><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><RestartOnFailure><Interval>PT1M</Interval><Count>3</Count></RestartOnFailure></Settings>
 <Actions Context="$taskPrincipal"><Exec><Command>$pythonCommand</Command><Arguments>$arguments</Arguments><WorkingDirectory>$workingDirectory</WorkingDirectory></Exec></Actions>
</Task>
"@
$taskFile = Join-Path $OutputDirectory 'JobSearchHQ-Server.xml'
[IO.File]::WriteAllText($taskFile, $xml, [Text.Encoding]::Unicode)
Write-Output ('Prepared startup task: ' + $taskFile)
Write-Output ('Expected HTTPS origin: ' + $settings.origin)
Write-Output 'Task is not registered. Import it in Task Scheduler and enter your Windows password there when prompted.'
