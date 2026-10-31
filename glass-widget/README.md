# 磨砂玻璃桌面组件

一个 Windows 桌面上的 200 × 200 逻辑像素 Tauri 2 窗口。左键拖动空白处，点击右上角叉号退出程序。界面包含玻璃层、可旋转的 3D 封面、歌曲信息、歌词、进度及播放/暂停按钮。右下角拖动可按方形缩放。窗口默认置顶。

在 Windows 显示缩放下，逻辑像素会按系统缩放，实际物理像素可能大于 200。CSS 的 `--glass-tint` 可调整表面遮盖程度。原生渲染层使用 DXGI 桌面复制与 GPU 着色器模糊并折射窗口后方的画面；它不传输画面，但需要屏幕捕获资源。为避免捕获自身形成反馈和拖动重影，组件及玻璃渲染层会被排除在普通截图和录屏之外。锁屏或安全桌面期间捕获会暂停。原生实现来自 [electron-liquid-glass](https://github.com/hicccc77/electron-liquid-glass)，许可证见 `src-tauri/native/liquid-glass/LICENSE`。

## 构建

需要 Rust MSVC 工具链、Microsoft C++ Build Tools、WebView2 和 Node.js，参见 [Tauri 环境要求](https://v2.tauri.app/start/prerequisites/)。在此目录运行：

```powershell
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
npm install
npm run build
```

生成的 `src-tauri/target/release/glass-desktop-widget.exe` 可直接启动。为避开旧版系统 WebView2 的透明窗口残影，程序会优先使用同目录 `webview2-runtime` 中的微软固定版运行时。复制程序时请同时复制该目录；没有该目录时会回退到系统运行时。固定版不会自动更新，需定期从微软官方渠道更新。开发时可运行 `npm run dev`。组件启动时自动启动或复用项目根目录的酷狗本地服务，因此需要保留项目目录以及已安装的 kugo-mcp。构建程序记录项目路径，移动项目后请重新构建。组件关闭时仅结束自己启动的服务。

外观参考 [Liquid-Glass-Vue](https://github.com/mianbeishiwole/Liquid-Glass-Vue) 的半透明玻璃概念，未复制其源码。

## 酷狗连接

已恢复至“继续这个过程，完成后开启下一段优化”消息前的代码。连接、播放控制、四秒采样与四秒操作确认保持该历史版本的行为；封面和歌词按实际歌曲哈希查询。

仅保留动态本机端口、启动握手与单实例，避免端口占用。界面、唱片、缩放和按钮不另作改动。项目根目录 README.md 和 kogouminplay.md 记录恢复边界与验证。

当前查询策略：可见时每 4.4 秒原生采样，隐藏后停止缓存轮询，30 秒无人读取后暂停原生采样；恢复显示时唤醒且遵守限频。控制后约 600 毫秒做一次确认。封面地址和歌词按真实 hash 缓存最近 5 首，并行获取且取消旧曲任务。Meting 按需启动、查询结束后空闲 30 秒退出，缓存命中无需启动。唱片自动绘制上限 10 FPS，转速不变，拖动仍立即绘制。没有控件读取或歌曲搜索匹配。详见根目录接口文档最新一节。
