// 把一张带透明背景的圆角方块图标整理成 macOS 标准 1024×1024 图标：
// 找出不透明的主体，重新切出干净的圆角，居中放到 824×824 的主体区域，四周透明。
// 用法：swift make_icon.swift <输入.png> <输出.png> [--trim N] [--pixelated]
//   --trim N      直接裁掉四周 N 像素（例如游戏图标自带的边框），不再自动找主体
//   --pixelated   放大时用最近邻插值，保留像素画的颗粒感（适合很小的原图）
//   --background 上色,下色
//                 原图是透明背景上的人物/物件（没有方块底）时用：先画一个从上到下渐变的圆角方块底，
//                 再把原图里的可见部分缩放后居中放上去。颜色写成 #RRGGBB
//   --fill 比例   配合 --background：物件最多占圆角方块边长的比例，默认 0.8

import AppKit
import CoreGraphics
import Foundation

var args = [String]()
var trim: Int?
var pixelated = false
var background: (CGColor, CGColor)?
var fill: CGFloat = 0.8

func color(_ hex: Substring) -> CGColor? {
    let text = hex.hasPrefix("#") ? hex.dropFirst() : hex
    guard text.count == 6, let value = UInt32(text, radix: 16) else { return nil }
    return CGColor(srgbRed: CGFloat((value >> 16) & 0xff) / 255, green: CGFloat((value >> 8) & 0xff) / 255,
                   blue: CGFloat(value & 0xff) / 255, alpha: 1)
}
var rest = CommandLine.arguments.makeIterator()
while let arg = rest.next() {
    switch arg {
    case "--trim": trim = rest.next().flatMap { Int($0) }
    case "--pixelated": pixelated = true
    case "--fill":
        guard let value = rest.next().flatMap({ Double($0) }), value > 0, value <= 1 else {
            FileHandle.standardError.write("--fill 应为 0 到 1 之间的小数\n".data(using: .utf8)!); exit(1)
        }
        fill = CGFloat(value)
    case "--background":
        let parts = (rest.next() ?? "").split(separator: ",")
        if parts.count == 2, let top = color(parts[0]), let bottom = color(parts[1]) { background = (top, bottom) }
        else { FileHandle.standardError.write("--background 格式应为 #RRGGBB,#RRGGBB\n".data(using: .utf8)!); exit(1) }
    default: args.append(arg)
    }
}
guard args.count == 3,
      let source = NSImage(contentsOfFile: args[1]),
      let image = source.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("用法：swift make_icon.swift <输入.png> <输出.png> [--trim N] [--pixelated] [--background 上色,下色]\n".data(using: .utf8)!)
    exit(1)
}

let width = image.width, height = image.height
var pixels = [UInt8](repeating: 0, count: width * height * 4)
let space = CGColorSpace(name: CGColorSpace.sRGB)!
let reader = CGContext(data: &pixels, width: width, height: height, bitsPerComponent: 8,
                       bytesPerRow: width * 4, space: space,
                       bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
reader.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))

let body: CGRect
if background != nil {
    // 透明背景上的物件：取所有可见像素的外接矩形
    var minX = width, minY = height, maxX = -1, maxY = -1
    for y in 0..<height {
        for x in 0..<width where pixels[(y * width + x) * 4 + 3] > 16 {
            minX = min(minX, x); maxX = max(maxX, x); minY = min(minY, y); maxY = max(maxY, y)
        }
    }
    guard maxX >= 0 else { FileHandle.standardError.write("原图是全透明的\n".data(using: .utf8)!); exit(1) }
    body = CGRect(x: minX, y: minY, width: maxX - minX + 1, height: maxY - minY + 1)
} else if let trim {
    body = CGRect(x: trim, y: trim, width: width - trim * 2, height: height - trim * 2)
} else {
    // 主体的边界：每行/每列几乎不透明的像素足够多，才算主体（排除零星噪点）
    func opaque(_ x: Int, _ y: Int) -> Bool { pixels[(y * width + x) * 4 + 3] > 240 }
    let minRun = max(8, width / 20)
    var rows = [Int](), cols = [Int]()
    for y in 0..<height where (0..<width).filter({ opaque($0, y) }).count >= minRun { rows.append(y) }
    for x in 0..<width where (0..<height).filter({ opaque(x, $0) }).count >= minRun { cols.append(x) }
    guard let top = rows.first, let bottom = rows.last, let left = cols.first, let right = cols.last else {
        FileHandle.standardError.write("没找到不透明的主体\n".data(using: .utf8)!)
        exit(1)
    }
    // 内缩几像素，避开原图边缘的半透明过渡
    let inset = max(2, (right - left) / 100)
    // 位图行序为自上而下，CGImage.cropping 也按自上而下的坐标
    body = CGRect(x: left + inset, y: top + inset,
                  width: right - left - inset * 2, height: bottom - top - inset * 2)
}
let cropped = image.cropping(to: body)!
print("主体范围：x \(Int(body.minX))–\(Int(body.maxX))，y \(Int(body.minY))–\(Int(body.maxY))（原图 \(width)×\(height)）")

let canvas = 1024, size: CGFloat = 824
let origin = (CGFloat(canvas) - size) / 2
let target = CGRect(x: origin, y: origin, width: size, height: size)
let writer = CGContext(data: nil, width: canvas, height: canvas, bitsPerComponent: 8, bytesPerRow: 0,
                       space: space, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
writer.interpolationQuality = pixelated ? .none : .high
writer.addPath(CGPath(roundedRect: target, cornerWidth: 185, cornerHeight: 185, transform: nil))
writer.clip()
if let (top, bottom) = background {
    // CG 坐标原点在左下，所以渐变从 maxY（上）画到 minY（下）
    let gradient = CGGradient(colorsSpace: space, colors: [top, bottom] as CFArray, locations: [0, 1])!
    writer.drawLinearGradient(gradient, start: CGPoint(x: 0, y: target.maxY), end: CGPoint(x: 0, y: target.minY), options: [])
    // 物件放进主体中间 fill 比例的区域，像素画用整数倍放大，避免像素块大小不一
    var scale = min(size * fill / body.width, size * fill / body.height)
    if pixelated { scale = max(1, scale.rounded(.down)) }
    let drawn = CGSize(width: body.width * scale, height: body.height * scale)
    writer.draw(cropped, in: CGRect(x: (target.midX - drawn.width / 2).rounded(), y: (target.midY - drawn.height / 2).rounded(),
                                    width: drawn.width, height: drawn.height))
} else {
    // 主体按短边铺满，多出来的部分居中裁掉
    let scale = max(size / body.width, size / body.height)
    let drawn = CGSize(width: body.width * scale, height: body.height * scale)
    writer.draw(cropped, in: CGRect(x: target.midX - drawn.width / 2, y: target.midY - drawn.height / 2,
                                    width: drawn.width, height: drawn.height))
}

let rep = NSBitmapImageRep(cgImage: writer.makeImage()!)
try rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: args[2]))
print("已生成 \(args[2])")
