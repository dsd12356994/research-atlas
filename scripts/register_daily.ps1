$ErrorActionPreference = 'Stop'
$atlasRoot = Split-Path -Parent $PSScriptRoot
$atlasName = 'ResearchAtlas-Daily'
if (Get-ScheduledTask -TaskName $atlasName -ErrorAction SilentlyContinue) { throw 'Task already exists; inspect it before replacing it.' }
$atlasAction = New-ScheduledTaskAction -Execute (Join-Path $atlasRoot '.venv/Scripts/python.exe') -Argument '-m hub.pipeline run --deep 0' -WorkingDirectory $atlasRoot
$atlasTrigger = New-ScheduledTaskTrigger -Daily -At '09:00'
$atlasPrincipal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$atlasSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 40)
Register-ScheduledTask -TaskName $atlasName -Action $atlasAction -Trigger $atlasTrigger -Principal $atlasPrincipal -Settings $atlasSettings -Description 'Research Atlas metadata collection; local timezone 09:00; no model calls.'
