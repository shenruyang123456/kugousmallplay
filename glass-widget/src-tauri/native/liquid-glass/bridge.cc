#include "session.h"
#include "dda_exclusion.h"
#include <algorithm>
#include <cstdint>

using CornerCallback = void (*)(int, float, float, float);
static int band_width = 0;
static int band_height = 0;

static void update_corner_bands(int id, int width, int height) {
    const LONG w = std::max(1, width / 4);
    const LONG h = std::max(1, height / 4);
    GlassSession::Instance().SetLumaBands(id, {
        {0, {0, 0, w, h}}, {1, {width - w, 0, width, h}},
        {2, {0, height - h, w, height}}, {3, {width - w, height - h, width, height}}
    });
}

extern "C" int glass_start(intptr_t window, int x, int y, int width, int height, float scale, CornerCallback callback) {
    HWND hwnd = reinterpret_cast<HWND>(window);
    // Prevent the renderer from sampling its own UI, while leaving other
    // screen capture methods available.
    if (!SetExcludedFromDesktopDuplication(hwnd, true)) return 0;
    PanelConfig config;
    config.bounds = {x, y, x + width, y + height};
    config.dpr = scale;
    config.anchor = hwnd;
    config.params.cornerRadius = 30.0f * scale;
    config.params.blurSigma = 2.5f * scale;
    config.params.displacementScale = 70.0f;
    config.params.aberrationIntensity = 0.5f;
    config.params.saturation = 1.0f;
    int id = GlassSession::Instance().CreatePanel(config);
    if (!GlassSession::Instance().IsPanelReady(id)) {
        GlassSession::Instance().Shutdown();
        SetExcludedFromDesktopDuplication(hwnd, false);
        return 0;
    }
    GlassSession::Instance().SetLumaCallback([callback](int, std::vector<LumaBandStats> bands) {
        if (!callback) return;
        for (const auto& band : bands) callback(band.id, band.r, band.g, band.b);
    });
    update_corner_bands(id, width, height);
    band_width = width;
    band_height = height;
    GlassSession::Instance().ShowPanel(id, 0);
    return id;
}

extern "C" void glass_move(int id, int x, int y, int width, int height, float scale) {
    RECT bounds{x, y, x + width, y + height};
    GlassSession::Instance().SetPanelBounds(id, bounds);
    GlassParams params;
    params.cornerRadius = 30.0f * scale;
    params.blurSigma = 2.5f * scale;
    params.displacementScale = 70.0f;
    params.aberrationIntensity = 0.5f;
    params.saturation = 1.0f;
    GlassSession::Instance().SetPanelParams(id, params);
    if (width != band_width || height != band_height) {
        update_corner_bands(id, width, height);
        band_width = width;
        band_height = height;
    }
}

extern "C" void glass_refresh(int id) {
    update_corner_bands(id, band_width, band_height);
}

extern "C" void glass_stop() {
    GlassSession::Instance().Shutdown();
}
