fn main() {
    let manifest = tauri_build::AppManifest::new().commands(&["pick_media", "workspace", "choose_workspace"]);
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(manifest))
        .expect("build ArcherGate desktop resources");
}
