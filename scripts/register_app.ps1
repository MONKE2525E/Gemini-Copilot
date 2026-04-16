$WshShell = New-Object -ComObject WScript.Shell
$StartMenuPath = [System.IO.Path]::Combine($env:APPDATA, "Microsoft\Windows\Start Menu\Programs\Gemini Copilot.lnk")
$Shortcut = $WshShell.CreateShortcut($StartMenuPath)
$Shortcut.TargetPath = "G:\Gemini Desktop\gemini-copilot\src-tauri\target\debug\gemini-copilot.exe"
$Shortcut.WorkingDirectory = "G:\Gemini Desktop\gemini-copilot"
$Shortcut.Description = "Gemini Copilot Desktop Utility"
$Shortcut.Save()

Write-Host "Shortcut created at: $StartMenuPath"
