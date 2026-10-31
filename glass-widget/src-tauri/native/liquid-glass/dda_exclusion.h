#pragma once
#include <windows.h>

// WCA_EXCLUDED_FROM_DDA is deliberately scoped to Desktop Duplication.
// Other screenshot APIs may still capture these windows.
inline bool SetExcludedFromDesktopDuplication(HWND hwnd, bool exclude) {
    struct CompositionAttributeData {
        int attribute;
        void* data;
        SIZE_T size;
    };
    using SetAttribute = BOOL (WINAPI *)(HWND, CompositionAttributeData*);
    const auto setAttribute = reinterpret_cast<SetAttribute>(
        GetProcAddress(GetModuleHandleW(L"user32.dll"), "SetWindowCompositionAttribute"));
    if (!setAttribute) return false;
    BOOL value = exclude ? TRUE : FALSE;
    CompositionAttributeData data{24, &value, sizeof(value)};
    return setAttribute(hwnd, &data) != FALSE;
}
