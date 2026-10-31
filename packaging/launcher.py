"""Start the widget from PyInstaller's private, temporary extraction directory."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(sys._MEIPASS)
    # Fixed WebView2 120+ on Windows 10 runs its renderer in an AppContainer.
    # Grant read/execute only on the runtime extracted for this instance.
    subprocess.run(['icacls.exe', str(root / 'webview2-runtime'), '/grant',
                    '*S-1-15-2-2:(OI)(CI)(RX)', '*S-1-15-2-1:(OI)(CI)(RX)'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    data = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'KugouMusicPlay'
    data.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env['KUGO_BUNDLED_BRIDGE'] = str(root / 'bridge' / 'kugou-bridge.exe')
    env['KUGO_MCP_HOME'] = str(root / 'mcp')
    env['KUGO_BRIDGE_LOG'] = str(data / 'bridge.log')
    # The bridge has its own PyInstaller bootloader and must start independently.
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return subprocess.call([str(root / 'KUGOUPLAY.exe')], env=env, cwd=str(data))


if __name__ == '__main__':
    try:
        code = main()
        if code:
            raise RuntimeError(f'播放器启动失败（代码 {code}）')
    except Exception as exc:
        ctypes.windll.user32.MessageBoxW(None, str(exc), '酷狗桌面播放器', 0x10)
        sys.exit(1)
