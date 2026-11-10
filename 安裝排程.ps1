# 建立 Windows 工作排程：每個交易日（週一～五）06:10 / 07:30 / 08:30 / 15:30 自動更新 index.html
# 電腦當時若睡眠或關機，開機後會補跑一次（StartWhenAvailable）。
# 移除：Unregister-ScheduledTask -TaskName "台指夜盤決策更新" -Confirm:$false
$py  = "C:\Users\thoma\AppData\Local\Programs\Python\Python312\pythonw.exe"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$dir\taifex_night.py`"" -WorkingDirectory $dir
$days = 'Monday','Tuesday','Wednesday','Thursday','Friday'
$triggers = '06:10','07:30','08:30','15:30' | ForEach-Object { New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $_ }
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName "台指夜盤決策更新" -Action $action -Trigger $triggers -Settings $set `
  -Description "從期交所抓夜盤/外資資料，更新 $dir\index.html" -Force | Out-Null
Write-Host "排程已建立。下次執行：" (Get-ScheduledTaskInfo -TaskName "台指夜盤決策更新").NextRunTime
