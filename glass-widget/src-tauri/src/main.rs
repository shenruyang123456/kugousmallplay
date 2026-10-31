#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[cfg(windows)]
unsafe extern "C" {
    fn glass_start(hwnd: isize, x: i32, y: i32, width: i32, height: i32, scale: f32, callback: extern "C" fn(i32, f32, f32, f32)) -> i32;
    fn glass_move(id: i32, x: i32, y: i32, width: i32, height: i32, scale: f32);
    fn glass_refresh(id: i32);
    fn glass_stop();
}

#[cfg(windows)]
struct GlassPanel(i32);

#[cfg(windows)]
#[tauri::command]
fn refresh_corner_lights(panel: tauri::State<'_, GlassPanel>) {
    unsafe { glass_refresh(panel.0) };
}

#[cfg(windows)]
static APP_HANDLE: std::sync::OnceLock<tauri::AppHandle> = std::sync::OnceLock::new();

#[cfg(windows)]
extern "C" fn corner_light(id: i32, r: f32, g: f32, b: f32) {
    use tauri::Emitter;
    if let Some(app) = APP_HANDLE.get() {
        let _ = app.emit("glass-corner-light", (id, r, g, b));
    }
}

use std::{io::{BufRead, BufReader, Read, Write}, net::TcpStream, process::{Child, Command, Stdio}, sync::Mutex, time::Duration};

struct BridgeProcess {
    child: Option<Child>,
    port: Option<u16>,
    handshake: Option<std::sync::mpsc::Receiver<std::io::Result<String>>>,
}
struct Bridge(Mutex<BridgeProcess>);

fn local_request(port: u16, path: &str, body: Option<&str>) -> Result<serde_json::Value, String> {
    let mut stream = TcpStream::connect_timeout(&std::net::SocketAddr::from(([127, 0, 0, 1], port)), Duration::from_secs(2)).map_err(|e| e.to_string())?;
    stream.set_read_timeout(Some(Duration::from_secs(if body.is_some() { 50 } else { 12 }))).map_err(|e| e.to_string())?;
    let payload = body.unwrap_or("");
    let method = if body.is_some() { "POST" } else { "GET" };
    write!(stream, "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: http://127.0.0.1:{port}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{payload}", payload.len()).map_err(|e| e.to_string())?;
    let mut data = Vec::new();
    stream.read_to_end(&mut data).map_err(|e| e.to_string())?;
    let split = data.windows(4).position(|v| v == b"\r\n\r\n").ok_or("服务响应无效")?;
    let headers = String::from_utf8_lossy(&data[..split]);
    let result: serde_json::Value = serde_json::from_slice(&data[split + 4..]).map_err(|e| e.to_string())?;
    if !headers.starts_with("HTTP/1.0 200") && !headers.starts_with("HTTP/1.1 200") && !headers.starts_with("HTTP/1.0 202") && !headers.starts_with("HTTP/1.1 202") {
        return Err(result.get("error").and_then(|v| v.as_str()).unwrap_or("本地服务请求失败").to_string());
    }
    Ok(result)
}

fn ensure_bridge(bridge: &Bridge) -> Result<u16, String> {
    let mut process = bridge.0.lock().map_err(|e| e.to_string())?;
    if let Some(child) = process.child.as_mut() {
        if child.try_wait().map_err(|e| e.to_string())?.is_some() {
            process.child = None;
            process.port = None;
            process.handshake = None;
        }
    }
    if let Some(port) = process.port { return Ok(port); }
    if process.child.is_some() { return receive_endpoint(&mut process); }
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).parent().and_then(|p| p.parent()).ok_or("无法定位项目目录")?;
    let mcp = std::env::var_os("KUGO_MCP_HOME").map(std::path::PathBuf::from).unwrap_or_else(|| std::path::PathBuf::from(std::env::var_os("USERPROFILE").unwrap_or_default()).join(".codex/mcp/kugo-mcp"));
    let python = mcp.join("venv/Scripts/python.exe");
    let server = root.join("server.py");
    if !python.is_file() || !server.is_file() { return Err("找不到本地服务或 kugo-mcp，请检查安装与项目目录".into()); }
    let mut command = Command::new(python);
    command.arg(server).arg("--port").arg("0").arg("--parent-pid").arg(std::process::id().to_string()).env("PYTHONIOENCODING", "utf-8").stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)] { use std::os::windows::process::CommandExt; command.creation_flags(0x08000000); }
    let mut child = command.spawn().map_err(|e| format!("无法启动本地服务：{e}"))?;
    let stdout = child.stdout.take().ok_or("无法读取本地服务地址")?;
    process.child = Some(child);
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let mut line = String::new();
        let result = BufReader::new(stdout).read_line(&mut line).map(|_| line);
        let _ = tx.send(result);
    });
    process.handshake = Some(rx);
    receive_endpoint(&mut process)
}

fn receive_endpoint(process: &mut BridgeProcess) -> Result<u16, String> {
    let line = process.handshake.as_ref().ok_or("本地服务地址尚未就绪")?
        .recv_timeout(Duration::from_secs(1)).map_err(|_| "正在启动本地服务…")?
        .map_err(|e| e.to_string())?;
    let identity: serde_json::Value = serde_json::from_str(&line).map_err(|_| "本地服务启动失败，请检查 bridge.log")?;
    if identity["service"] != "kugoumusicplay" || identity["version"] != 6 { return Err("本地服务版本不匹配".into()); }
    let port = identity["port"].as_u64().filter(|n| *n > 0 && *n <= 65535).ok_or("本地服务地址无效")? as u16;
    process.port = Some(port);
    process.handshake = None;
    Ok(port)
}

#[tauri::command]
async fn bridge_request(app: tauri::AppHandle, operation: String) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        use tauri::Manager;
        let state = app.state::<Bridge>();
        let port = ensure_bridge(&state)?;
        match operation.as_str() {
            "state" => local_request(port, "/api/state", None),
            "cover" => local_request(port, "/api/cover", None),
            "play" => local_request(port, "/api/control", Some(r#"{"action":"play"}"#)),
            "pause" => local_request(port, "/api/control", Some(r#"{"action":"pause"}"#)),
            "next" => local_request(port, "/api/control", Some(r#"{"action":"next"}"#)),
            "prev" => local_request(port, "/api/control", Some(r#"{"action":"prev"}"#)),




            _ => Err("不支持的操作".into()),
        }
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
fn resize_square(window: tauri::WebviewWindow, size: f64) -> Result<(), String> {
    if !size.is_finite() { return Err("窗口大小无效".into()); }
    let size = size.clamp(200.0, 500.0);
    window.set_size(tauri::LogicalSize::new(size, size)).map_err(|e| e.to_string())
}

fn main() {
    #[cfg(windows)]
    {
        #[link(name = "kernel32")]
        unsafe extern "system" {
            fn CreateMutexW(attributes: *const std::ffi::c_void, owner: i32, name: *const u16) -> isize;
            fn GetLastError() -> u32;
        }
        let name: Vec<u16> = "Local\\KugouMusicPlay.Widget".encode_utf16().chain(Some(0)).collect();
        let mutex = unsafe { CreateMutexW(std::ptr::null(), 0, name.as_ptr()) };
        if mutex != 0 && unsafe { GetLastError() } == 183 { return; }
        // The OS closes this process-owned handle on exit.
    }

    #[cfg(windows)]
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let runtime = dir.join("webview2-runtime");
            if runtime.join("msedgewebview2.exe").is_file() {
                // Set before Tauri starts threads or creates the WebView2 environment.
                std::env::set_var("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER", runtime);
            }
        }
    }
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![refresh_corner_lights, bridge_request, resize_square])
        .setup(|app| {
            use tauri::Manager;
            app.manage(Bridge(Mutex::new(BridgeProcess { child: None, port: None, handshake: None })));
            #[cfg(windows)]
            {
                let _ = APP_HANDLE.set(app.handle().clone());
                let window = app.get_webview_window("main").ok_or("未找到组件窗口")?;
                let position = window.outer_position()?;
                let size = window.outer_size()?;
                let scale = window.scale_factor()? as f32;
                let hwnd = window.hwnd()?.0 as isize;
                let id = unsafe { glass_start(hwnd, position.x, position.y, size.width as i32, size.height as i32, scale, corner_light) };
                if id == 0 { return Err("无法为液态玻璃启动桌面捕获保护".into()); }
                app.manage(GlassPanel(id));
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            #[cfg(windows)]
            {
                use tauri::Manager;
                if window.label() != "main" { return; }
                match event {
                    tauri::WindowEvent::Moved(_) | tauri::WindowEvent::Resized(_) | tauri::WindowEvent::ScaleFactorChanged { .. } => {
                        if let (Some(panel), Ok(position), Ok(size), Ok(scale)) = (
                            window.try_state::<GlassPanel>(), window.outer_position(), window.outer_size(), window.scale_factor()
                        ) {
                            unsafe { glass_move(panel.0, position.x, position.y, size.width as i32, size.height as i32, scale as f32) };
                        }
                    }
                    tauri::WindowEvent::Destroyed => {
                        if let Some(bridge) = window.try_state::<Bridge>() {
                            if let Ok(mut child) = bridge.0.lock() {
                                // The service watches this widget's process handle and exits
                                // gracefully, including its metadata MCP and control worker.
                                child.child.take();
                            }
                        }
                        unsafe { glass_stop() }
                    },
                    _ => {}
                }
            }
            #[cfg(not(windows))]
            let _ = (window, event);
        })
        .run(tauri::generate_context!())
        .expect("无法启动桌面组件");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn delayed_start_keeps_the_same_handshake_receiver() {
        let (tx, rx) = std::sync::mpsc::channel();
        let mut process = BridgeProcess { child: None, port: None, handshake: Some(rx) };
        assert!(receive_endpoint(&mut process).is_err());
        assert!(process.handshake.is_some());
        tx.send(Ok(r#"{"service":"kugoumusicplay","version":6,"port":53127}"#.into())).unwrap();
        assert_eq!(receive_endpoint(&mut process).unwrap(), 53127);
        assert_eq!(process.port, Some(53127));
    }

    #[test]
    fn rejects_invalid_startup_endpoint() {
        let (tx, rx) = std::sync::mpsc::channel();
        let mut process = BridgeProcess { child: None, port: None, handshake: Some(rx) };
        tx.send(Ok(r#"{"service":"kugoumusicplay","version":6,"port":0}"#.into())).unwrap();
        assert!(receive_endpoint(&mut process).is_err());
        assert!(process.port.is_none());
    }
}
