// 给 .app 设置 Finder 自定义图标。
// macOS 26 会把只带 .icns 的 App 图标套进灰色圆角框，自定义图标不会，所以编译后额外设置一次。
// 用法：swift set_icon.swift <App 路径> <图标.png>

import AppKit

let args = CommandLine.arguments
guard args.count == 3, let image = NSImage(contentsOfFile: args[2]) else {
    FileHandle.standardError.write("用法：swift set_icon.swift <App 路径> <图标.png>\n".data(using: .utf8)!)
    exit(1)
}
if !NSWorkspace.shared.setIcon(image, forFile: args[1], options: []) {
    FileHandle.standardError.write("设置图标失败：\(args[1])\n".data(using: .utf8)!)
    exit(1)
}
