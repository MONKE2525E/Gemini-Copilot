import io

code = """
#[derive(Clone, serde::Serialize, serde::Deserialize)]
pub struct DependencyStatus {
    pub node_installed: bool,
    pub node_version: String,
    pub gemini_installed: bool,
    pub gemini_version: String,
    pub gemini_logged_in: bool,
}

#[tauri::command]
async fn check_dependencies() -> Result<DependencyStatus, String> {
    let node_result = Command::new("powershell.exe")
        .args(["-NoProfile", "-Command", "node --version 2>&1"])
        .creation_flags(std::os::windows::process::CommandExt::CREATE_NO_WINDOW)
        .output();
    let (node_installed, node_version) = match node_result {
        Ok(out) if out.status.success() => {
            let v = String::from_utf8_lossy(&out.stdout).trim().to_string();
            let major_version_str = v.replace("v", "");
            let major_version = major_version_str.split('.').next().unwrap_or("0").parse::<i32>().unwrap_or(0);
            if major_version >= 20 { (true, v) } else { (false, format!("{} (Needs >= v20)", v)) }
        }
        _ => (false, String::new()),
    };

    let gemini_result = Command::new("powershell.exe")
        .args(["-NoProfile", "-Command", "npm ls -global @google/gemini-cli 2>&1"])
        .creation_flags(std::os::windows::process::CommandExt::CREATE_NO_WINDOW)
        .output();
    let (gemini_installed, gemini_version) = match gemini_result {
        Ok(out) if out.status.success() => {
            let out_str = String::from_utf8_lossy(&out.stdout);
            if out_str.contains("@google/gemini-cli@") { (true, "Installed via NPM".to_string()) } else { (false, String::new()) }
        }
        _ => (false, String::new()),
    };

    let mut gemini_logged_in = false;
    if gemini_installed {
        let auth_result = Command::new("powershell.exe")
            .args(["-NoProfile", "-Command", "npx -y @google/gemini-cli@latest 'What time is it?' 2>&1"])
            .creation_flags(std::os::windows::process::CommandExt::CREATE_NO_WINDOW)
            .output();
        if let Ok(out) = auth_result {
            let out_str = String::from_utf8_lossy(&out.stdout).to_lowercase();
            if out.status.success() && !out_str.contains("authentication") && !out_str.contains("login") && !out_str.contains("error") {
                gemini_logged_in = true;
            }
        }
    }
    Ok(DependencyStatus { node_installed, node_version, gemini_installed, gemini_version, gemini_logged_in })
}

#[tauri::command]
async fn install_gemini_cli(app_handle: tauri::AppHandle) -> Result<bool, String> {
    let window = app_handle.get_webview_window("main");
    if let Some(w) = &window { let _ = w.emit("install-progress", "Installing Gemini CLI via npm..."); }
    let output = Command::new("powershell.exe")
        .args(["-NoProfile", "-Command", "npm install -g @google/gemini-cli 2>&1"])
        .creation_flags(std::os::windows::process::CommandExt::CREATE_NO_WINDOW)
        .output().map_err(|e| e.to_string())?;
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    if let Some(w) = &window { let _ = w.emit("install-progress", format!("{}{}", stdout, stderr)); }
    if output.status.success() { Ok(true) } else { Err(format!("npm install failed:\\n{}{}", stdout, stderr)) }
}

#[tauri::command]
async fn open_url(url: String) -> Result<(), String> {
    open::that(url).map_err(|e| e.to_string())
}

#[tauri::command]
async fn login_gemini_cli() -> Result<(), String> {
    Command::new("cmd.exe")
        .args(["/c", "start", "cmd.exe", "/k", "echo Logging you into Gemini CLI... && npx -y @google/gemini-cli@latest \\\"test\\\""])
        .spawn().map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
async fn install_node() -> Result<(), String> {
    Command::new("cmd.exe")
        .args(["/c", "start", "powershell", "-NoExit", "-Command", "echo 'Installing Node.js LTS via winget...'; winget install -e --id OpenJS.NodeJS.LTS; echo ''; echo '------------------------------------------------'; echo 'INSTALLATION COMPLETE.'; echo 'Please CLOSE this window and RESTART Gemini Copilot.'; echo '------------------------------------------------'"])
        .spawn().map_err(|e| e.to_string())?;
    Ok(())
}
"""

with io.open("G:/Gemini Desktop/gemini-copilot/src-tauri/src/lib.rs", "a", encoding="utf-8") as f:
    f.write(code)
