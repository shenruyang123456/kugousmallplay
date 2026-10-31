fn main() {
    #[cfg(target_os = "windows")]
    {
        let dir = "native/liquid-glass";
        let mut build = cc::Build::new();
        build.cpp(true).std("c++17").include(dir)
            .define("NOMINMAX", None).define("WIN32_LEAN_AND_MEAN", None)
            .define("UNICODE", None).define("_UNICODE", None);
        for file in ["capture.cc", "d3d_utils.cc", "panel.cc", "renderer.cc", "session.cc", "bridge.cc"] {
            build.file(format!("{dir}/{file}"));
        }
        build.compile("liquid_glass_native");
        for lib in ["d3d11", "dxgi", "dcomp", "d3dcompiler", "user32"] {
            println!("cargo:rustc-link-lib={lib}");
        }
        println!("cargo:rerun-if-changed={dir}");
    }
    tauri_build::build()
}
