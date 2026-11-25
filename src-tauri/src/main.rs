#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    fs::{self, OpenOptions},
    io::{BufRead, BufReader, Write},
    os::windows::process::CommandExt,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{mpsc, Mutex},
    thread,
    time::{Duration, Instant},
};
use tauri::{Manager, RunEvent, Url, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_dialog::{DialogExt, FilePath, MessageDialogKind};

const HIDE_CONSOLE: u32 = 0x08000000;
const MEDIA: &[&str] = &["mp4", "mov", "wav", "mp3"];

struct Session {
    workspace: PathBuf,
    settings: PathBuf,
    backend: Mutex<Option<Child>>,
}

fn opened_path(path: FilePath) -> Option<PathBuf> {
    match path {
        FilePath::Path(path) => Some(path),
        FilePath::Url(url) => url.to_file_path().ok(),
    }
}

#[tauri::command]
async fn pick_media(app: tauri::AppHandle) -> Result<Vec<PathBuf>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        app.dialog()
            .file()
            .set_title("Select original media")
            .add_filter("Source media", MEDIA)
            .blocking_pick_files()
            .unwrap_or_default()
            .into_iter()
            .filter_map(opened_path)
            .collect()
    })
    .await
    .map_err(|error| error.to_string())
}

#[tauri::command]
fn workspace(state: tauri::State<'_, Session>) -> PathBuf {
    state.workspace.clone()
}

#[tauri::command]
async fn choose_workspace(app: tauri::AppHandle) -> Result<(), String> {
    let chosen = tauri::async_runtime::spawn_blocking({
        let app = app.clone();
        move || app.dialog().file().set_title("Open a private workspace")
            .blocking_pick_folder().and_then(opened_path)
    }).await.map_err(|error| error.to_string())?;
    if let Some(folder) = chosen {
        let state = app.state::<Session>();
        if folder != state.workspace && folder.is_absolute() {
            let config = serde_json::to_vec(&serde_json::json!({"workspace": folder})).map_err(|error| error.to_string())?;
            let staged = state.settings.with_extension("tmp");
            let mut file = fs::File::create(&staged).map_err(|error| error.to_string())?;
            file.write_all(&config).and_then(|_| file.sync_all()).map_err(|error| error.to_string())?;
            fs::rename(staged, &state.settings).map_err(|error| error.to_string())?;
            stop_backend(&state);
            app.restart();
        }
    }
    Ok(())
}

fn stop_backend(state: &Session) {
    if let Ok(mut backend) = state.backend.lock() {
        if let Some(mut child) = backend.take() {
            drop(child.stdin.take());
            let wait_until = Instant::now() + Duration::from_secs(4);
            while Instant::now() < wait_until {
                if matches!(child.try_wait(), Ok(Some(_))) {
                    return;
                }
                thread::sleep(Duration::from_millis(40));
            }
            let _ = Command::new("taskkill.exe").args(["/PID", &child.id().to_string(), "/T", "/F"])
                .creation_flags(HIDE_CONSOLE).output();
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn start(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let data = app.path().app_local_data_dir()?;
    fs::create_dir_all(&data)?;
    let settings = data.join("workspace.json");
    let workspace = match fs::read(&settings) {
        Ok(config) => {
            let value: serde_json::Value = serde_json::from_slice(&config)?;
            let path = PathBuf::from(value["workspace"].as_str().ok_or("Invalid workspace settings")?);
            if !path.is_absolute() { return Err("The workspace path must be absolute".into()); }
            path
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => data.join("workspace"),
        Err(error) => return Err(error.into()),
    };
    let payload = app.path().resource_dir()?.join("payload");
    let mut entropy = [0u8; 32];
    getrandom::fill(&mut entropy).map_err(|_| "Could not create a private session key")?;
    let token: String = entropy.iter().map(|byte| format!("{byte:02x}")).collect();
    let engine = payload.join("engine");
    let log_path = data.join("backend.log");
    let log = OpenOptions::new().write(true).truncate(true).create(true).open(&log_path)?;
    let mut backend = Command::new(payload.join("python").join("python.exe"))
        .args(["-u", "-m", "archergate.studio", "--workspace"])
        .arg(&workspace)
        .args(["serve", "--port", "0", "--desktop"])
        .current_dir(&engine)
        .env("ARCHERGATE_SESSION_TOKEN", &token)
        .env("ARCHERGATE_FFMPEG", payload.join("media-tools").join("ffmpeg.exe"))
        .env("ARCHERGATE_FFPROBE", payload.join("media-tools").join("ffprobe.exe"))
        .env("ARCHERGATE_WHISPER", payload.join("speech-tools").join("whisper-cli.exe"))
        .env("ARCHERGATE_WHISPER_MODEL", data.join("speech").join("ggml-tiny.en.bin"))
        .creation_flags(HIDE_CONSOLE)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(log)
        .spawn()?;
    let stdout = backend.stdout.take().ok_or("Backend stdout unavailable")?;
    let (send, receive) = mpsc::sync_channel(1);
    thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Some(origin) = line.strip_prefix("ArcherGate studio at ") {
                let _ = send.send(origin.to_owned());
                break;
            }
        }
    });
    app.manage(Session { workspace, settings, backend: Mutex::new(Some(backend)) });
    let origin = receive.recv_timeout(Duration::from_secs(25))
        .map_err(|_| format!("The media service could not start. See {}", log_path.display()))?;
    let allowed = Url::parse(&origin)?;
    if allowed.host_str() != Some("127.0.0.1") || allowed.port().is_none() {
        return Err("The media service returned an invalid address".into());
    }
    let bootstrap = Url::parse(&format!("{origin}/desktop-session/{token}"))?;
    let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(bootstrap))
        .title("ArcherGate Media Lab")
        .inner_size(1320.0, 900.0)
        .min_inner_size(700.0, 600.0)
        .on_navigation(move |target| target.origin() == allowed.origin())
        .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny)
        .build()?;
    let window_copy = window.clone();
    window.on_window_event(move |event| {
        if let WindowEvent::DragDrop(tauri::DragDropEvent::Drop { paths, .. }) = event {
            let media: Vec<&Path> = paths.iter().map(PathBuf::as_path).filter(|path|
                path.extension().and_then(|ext| ext.to_str()).is_some_and(|ext|
                    MEDIA.iter().any(|candidate| ext.eq_ignore_ascii_case(candidate)))).collect();
            if let Ok(detail) = serde_json::to_string(&media) {
                let _ = window_copy.eval(format!("window.dispatchEvent(new CustomEvent('archer-drop',{{detail:{detail}}}))"));
            }
        }
    });
    Ok(())
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![pick_media, workspace, choose_workspace])
        .setup(|app| {
            if let Err(error) = start(app) {
                if let Some(state) = app.try_state::<Session>() { stop_backend(&state); }
                let handle = app.handle().clone();
                app.dialog().message(error.to_string()).title("ArcherGate could not start")
                    .kind(MessageDialogKind::Error).show(move |_| handle.exit(1));
            }
            Ok(())
        })
        .build(tauri::generate_context!());
    if let Ok(app) = app {
        app.run(|handle, event| {
            if matches!(event, RunEvent::Exit) {
                if let Some(state) = handle.try_state::<Session>() { stop_backend(&state); }
            }
        });
    }
}
