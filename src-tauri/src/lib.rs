use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder},
    Emitter, Manager, Runtime, WebviewWindow, State
};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt as AutostartManagerExt};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};
use std::process::{Command, Stdio};
use std::io::{BufRead, BufReader, Write};
use std::sync::{Arc, Mutex};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::sync::oneshot;
use std::collections::HashMap;
use std::os::windows::process::CommandExt;

#[derive(Clone, Serialize, Deserialize)]
struct SidecarResponse {
    success: bool,
    image: Option<String>,
    image_path: Option<String>,
    response: Option<String>,
    message: String,
}

#[allow(dead_code)]
#[derive(Clone, Serialize, Deserialize)]
struct ActivityPacket {
    request_id: String,
    #[serde(rename = "type")]
    packet_type: String,
    kind: String,
    text: String,
}

struct SidecarState {
    stdin: Arc<Mutex<std::process::ChildStdin>>,
    pending_requests: Arc<Mutex<HashMap<String, oneshot::Sender<SidecarResponse>>>>,
}

#[tauri::command]
async fn take_screenshot(state: State<'_, SidecarState>) -> Result<SidecarResponse, String> {
    let (tx, rx) = oneshot::channel();
    let request_id = uuid::Uuid::new_v4().to_string();
    {
        let mut pending = state.pending_requests.lock().unwrap();
        pending.insert(request_id.clone(), tx);
    }
    let command = json!({"type": "screenshot", "request_id": request_id});
    {
        let mut stdin = state.stdin.lock().unwrap();
        writeln!(stdin, "{}", command.to_string()).map_err(|e| e.to_string())?;
        stdin.flush().map_err(|e| e.to_string())?;
    }
    let mut res = tokio::time::timeout(std::time::Duration::from_secs(185), rx)
        .await
        .map_err(|_| "Screenshot command timed out".to_string())?
        .map_err(|e| e.to_string())?;

    if res.success {
        if let Some(path) = &res.image_path {
            if let Ok(bytes) = std::fs::read(path) {
                use base64::{Engine as _, engine::general_purpose::STANDARD};
                res.image = Some(STANDARD.encode(bytes));
            }
        }
    }

    Ok(res)
}

#[tauri::command]
async fn reset_session(state: State<'_, SidecarState>) -> Result<SidecarResponse, String> {
    let (tx, rx) = oneshot::channel();
    let request_id = uuid::Uuid::new_v4().to_string();
    {
        let mut pending = state.pending_requests.lock().unwrap();
        pending.insert(request_id.clone(), tx);
    }
    let command = json!({"type": "reset_session", "request_id": request_id});
    {
        let mut stdin = state.stdin.lock().unwrap();
        writeln!(stdin, "{}", command.to_string()).map_err(|e| e.to_string())?;
        stdin.flush().map_err(|e| e.to_string())?;
    }
    let res = tokio::time::timeout(std::time::Duration::from_secs(10), rx)
        .await
        .map_err(|_| "Reset session timed out".to_string())?
        .map_err(|e| e.to_string())?;
    Ok(res)
}

#[tauri::command]
async fn query_gemini(
    prompt: String, 
    image: Option<String>, 
    state: State<'_, SidecarState>
) -> Result<SidecarResponse, String> {
    let (tx, rx) = oneshot::channel();
    let request_id = uuid::Uuid::new_v4().to_string();
    {
        let mut pending = state.pending_requests.lock().unwrap();
        pending.insert(request_id.clone(), tx);
    }
    let command = json!({
        "type": "query",
        "prompt": prompt,
        "image": image,
        "request_id": request_id
    });
    {
        let mut stdin = state.stdin.lock().unwrap();
        writeln!(stdin, "{}", command.to_string()).map_err(|e| e.to_string())?;
        stdin.flush().map_err(|e| e.to_string())?;
    }
    let res = tokio::time::timeout(std::time::Duration::from_secs(190), rx)
        .await
        .map_err(|_| "Query timed out after 190 seconds".to_string())?
        .map_err(|e| e.to_string())?;
    Ok(res)
}

#[tauri::command]
async fn hide_window(window: WebviewWindow) -> Result<(), String> {
    window.hide().map_err(|e| e.to_string())
}

#[tauri::command]
async fn show_window(window: WebviewWindow) -> Result<(), String> {
    window.show().map_err(|e| e.to_string())?;
    window.set_focus().map_err(|e| e.to_string())
}

#[tauri::command]
async fn toggle_window_visibility(window: WebviewWindow) -> Result<bool, String> {
    let is_visible = window.is_visible().map_err(|e| e.to_string())?;
    if is_visible {
        window.hide().map_err(|e| e.to_string())?;
        Ok(false)
    } else {
        position_window_bottom_center(&window).await?;
        window.show().map_err(|e| e.to_string())?;
        window.set_focus().map_err(|e| e.to_string())?;
        let _ = window.emit("window-shown", ());
        Ok(true)
    }
}

async fn position_window_bottom_center(window: &WebviewWindow) -> Result<(), String> {
    let monitor = window.primary_monitor().map_err(|e| e.to_string())?
        .ok_or("No primary monitor found")?;
    let monitor_size = monitor.size();
    let monitor_pos = monitor.position();
    let window_size = window.outer_size().map_err(|e| e.to_string())?;
    let x = monitor_pos.x + (monitor_size.width as i32 - window_size.width as i32) / 2;
    let y = monitor_pos.y + monitor_size.height as i32 - window_size.height as i32 - 80;
    window.set_position(tauri::Position::Physical(tauri::PhysicalPosition { x, y }))
        .map_err(|e| e.to_string())
}

#[tauri::command]
async fn quit_app<R: Runtime>(app: tauri::AppHandle<R>) {
    app.exit(0);
}

#[tauri::command]
async fn restart_app(app: tauri::AppHandle) {
    app.restart();
}

fn create_tray_menu<R: Runtime>(app: &tauri::AppHandle<R>) -> tauri::Result<Menu<R>> {
    let toggle_i = MenuItem::with_id(app, "toggle", "Toggle Copilot", true, None::<&str>)?;
    let quit_i = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&toggle_i, &quit_i])?;
    Ok(menu)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|_app, _args, _cwd| {}))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, Some(vec!["--hidden"])))
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            hide_window, show_window, toggle_window_visibility,
            take_screenshot, query_gemini, quit_app, restart_app,
            check_dependencies, install_gemini_cli, open_url, login_gemini_cli, install_node,
            reset_session
        ])
        .setup(|app| {
            // Check for the bundled main.exe first
            let resource_exe = app.path().resource_dir().unwrap_or_default()
                .join("_up_").join("python-sidecar").join("dist").join("main.exe");
            let resource_exe_alt = app.path().resource_dir().unwrap_or_default()
                .join("python-sidecar").join("dist").join("main.exe");
            
            let mut exe_path = None;
            if resource_exe.exists() {
                exe_path = Some(resource_exe);
            } else if resource_exe_alt.exists() {
                exe_path = Some(resource_exe_alt);
            } else {
                let current = std::env::current_dir().unwrap();
                let dev_exe = if current.ends_with("src-tauri") {
                    current.parent().unwrap().join("python-sidecar").join("dist").join("main.exe")
                } else {
                    current.join("python-sidecar").join("dist").join("main.exe")
                };
                if dev_exe.exists() {
                    exe_path = Some(dev_exe);
                }
            }

            let mut child = if let Some(path) = exe_path {
                Command::new(&path)
                    .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped())
                    .creation_flags(0x08000000)
                    .spawn().expect("Failed to spawn Python sidecar executable")
            } else {
                // Final fallback: try raw python script
                let current = std::env::current_dir().unwrap();
                let sidecar_path = if current.ends_with("src-tauri") {
                    current.parent().unwrap().join("python-sidecar").join("main.py")
                } else {
                    current.join("python-sidecar").join("main.py")
                };
                let python_exe = sidecar_path.parent().unwrap().join(".venv").join("Scripts").join("python.exe");
                let python_to_use = if python_exe.exists() { python_exe } else { std::path::PathBuf::from("python") };
                Command::new(python_to_use)
                    .arg(&sidecar_path).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped())
                    .creation_flags(0x08000000)
                    .spawn().expect("Failed to spawn Python sidecar via script")
            };
            
            let stdin = Arc::new(Mutex::new(child.stdin.take().expect("Failed to get stdin")));
            let pending_requests = Arc::new(Mutex::new(HashMap::<String, oneshot::Sender<SidecarResponse>>::new()));
            
            let pending_requests_clone = Arc::clone(&pending_requests);
            let app_handle_for_stdout = app.app_handle().clone();
            let stdout = child.stdout.take().expect("Failed to get stdout");
            
            std::thread::spawn(move || {
                let reader = BufReader::new(stdout);
                for line in reader.lines() {
                    if let Ok(line) = line {
                        if let Ok(val) = serde_json::from_str::<Value>(&line) {
                            if val.get("type").and_then(|v| v.as_str()) == Some("activity") {
                                // Real-time activity update
                                let _ = app_handle_for_stdout.emit("gemini-activity", val);
                            } else {
                                // Result response
                                if let Some(id) = val.get("request_id").and_then(|v| v.as_str()) {
                                    if let Ok(mut pending) = pending_requests_clone.lock() {
                                        if let Some(tx) = pending.remove(id) {
                                            if let Ok(resp) = serde_json::from_value::<SidecarResponse>(val) {
                                                let _ = tx.send(resp);
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            });

            let stderr = child.stderr.take().expect("Failed to get stderr");
            std::thread::spawn(move || {
                let reader = BufReader::new(stderr);
                for line in reader.lines() { if let Ok(line) = line { eprintln!("Sidecar: {}", line); } }
            });
            
            app.manage(SidecarState { stdin, pending_requests });

            let tray_menu = create_tray_menu(app.app_handle())?;
            let tray_icon = app.default_window_icon().unwrap().clone();
            TrayIconBuilder::new().icon(tray_icon).menu(&tray_menu).show_menu_on_left_click(false)
                .on_menu_event(|app, event| {
                    if event.id.as_ref() == "toggle" {
                        if let Some(window) = app.get_webview_window("main") {
                            tauri::async_runtime::spawn(async move { let _ = toggle_window_visibility(window).await; });
                        }
                    } else if event.id.as_ref() == "quit" { app.exit(0); }
                })
                .on_tray_icon_event(|tray, event| {
                    if let tauri::tray::TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                        if let Some(window) = tray.app_handle().get_webview_window("main") {
                            tauri::async_runtime::spawn(async move { let _ = toggle_window_visibility(window).await; });
                        }
                    }
                })
                .build(app.app_handle())?;
            
            let app_handle_shortcut = app.app_handle().clone();
            let shortcut = Shortcut::new(Some(tauri_plugin_global_shortcut::Modifiers::ALT), tauri_plugin_global_shortcut::Code::Space);
            let _ = app.global_shortcut().on_shortcut(shortcut, move |_app, _shortcut, event| {
                if event.state == ShortcutState::Pressed {
                    if let Some(window) = app_handle_shortcut.get_webview_window("main") {
                        tauri::async_runtime::spawn(async move { let _ = toggle_window_visibility(window).await; });
                    }
                }
            });
            
            let _ = app.autolaunch().enable();
            if let Some(window) = app.get_webview_window("main") {
                let _ = tauri::async_runtime::spawn(async move { let _ = position_window_bottom_center(&window).await; });
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event { window.hide().unwrap(); api.prevent_close(); }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

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
        .creation_flags(0x08000000)
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
        .creation_flags(0x08000000)
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
        let home = std::env::var("USERPROFILE").unwrap_or_default();
        let creds_path = std::path::PathBuf::from(home).join(".gemini").join("oauth_creds.json");
        
        if creds_path.exists() {
            let auth_result = Command::new("powershell.exe")
                .args(["-NoProfile", "-Command", "npx -y @google/gemini-cli@latest 'What time is it?' 2>&1"])
                .creation_flags(0x08000000)
                .output();
            if let Ok(out) = auth_result {
                let out_str = String::from_utf8_lossy(&out.stdout).to_lowercase();
                if out.status.success() && !out_str.contains("authentication") && !out_str.contains("login") && !out_str.contains("error") {
                    gemini_logged_in = true;
                }
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
        .creation_flags(0x08000000)
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
        .args([
            "/c", "start", "powershell", "-NoExit", "-Command", 
            "echo '------------------------------------------------'; echo '     GEMINI CLI AUTHENTICATION'; echo '------------------------------------------------'; echo ''; echo '1. A browser window will open shortly.'; echo '2. Log in with your Google Account.'; echo '3. Once you see \"Authentication Successful\", close this window.'; echo '   The app will automatically detect success.'; echo ''; echo '------------------------------------------------'; npx -y @google/gemini-cli@latest 'test'; pause"
        ])
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
