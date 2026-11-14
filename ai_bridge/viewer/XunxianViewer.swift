// 寻仙实时动态：接收 AI 桥插件发来的游戏数据包，解析成中文事件，显示在游戏旁边的悬浮窗里。
//
// 数据来源见 ai_bridge/README.md：插件在 127.0.0.1:47800 上用 TCP 发送，
// 每帧是 "XXB1" | 序号 u32 | 毫秒时间 u32 | 长度 u32 | 原始包。本程序就是那个接收端。
// 字段偏移来自录像 XML 与原始包的逐字节对照，以及客户端序列化函数 0x48f3f0 的反编译结果，
// 见 doc/游戏实时事件桥.md。

import AppKit
import Foundation
import Network
import SwiftUI

// MARK: - 名称表

struct NameTables: Decodable {
    var skills: [String: String] = [:]
    var chars: [String: String] = [:]
    var states: [String: String] = [:]
    var scenes: [String: String] = [:]
    var items: [String: String] = [:]
    var stills: [String: String] = [:]

    static func load() -> NameTables {
        guard let url = Bundle.main.url(forResource: "names", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let tables = try? JSONDecoder().decode(NameTables.self, from: data) else { return NameTables() }
        return tables
    }

    func skill(_ id: Int) -> String { skills[String(id)] ?? "技能#\(id)" }
    func state(_ id: Int) -> String { states[String(id)] ?? "状态#\(id)" }
    func scene(_ id: Int) -> String { scenes[String(id)] ?? "场景#\(id)" }
    func item(_ id: Int) -> String { items[String(id)] ?? "物品#\(id)" }
    func still(_ id: Int) -> String { stills[String(id)] ?? "物件#\(id)" }
}

// MARK: - 事件

enum EventCategory: String, CaseIterable, Identifiable {
    case combat = "战斗"
    case gain = "收获"
    case view = "出现/离开"
    case state = "状态"
    case stats = "数值"
    case move = "移动"
    case system = "系统"
    case other = "其他"
    case unknown = "未解析"

    var id: String { rawValue }

    var color: Color {
        switch self {
        case .combat: return .orange
        case .gain: return .yellow
        case .view: return .blue
        case .state: return .purple
        case .stats: return .teal
        case .move: return .gray
        case .system: return .green
        case .other: return .brown
        case .unknown: return .secondary
        }
    }

    /// 默认显示的分类：移动、数值、其他和未解析的包量大，默认隐藏（血蓝显示在顶部状态栏）
    /// 「其他」是结构已知、但含义不明或价值不大的包；「未解析」只剩完全不认识的包类型
    static let defaultVisible: Set<EventCategory> = [.combat, .gain, .view, .state, .system]
}

struct LiveEvent: Identifiable {
    let id: Int
    let time: Date
    let category: EventCategory
    let text: String
}

// MARK: - 世界状态与包解析

struct Entity {
    var id: Int
    var name: String
    var excelID: Int
    var level: Int?
    var isNPC: Bool
    var x: Float?
    var z: Float?
}

/// 小端字节读取
struct Reader {
    let bytes: [UInt8]

    func u8(_ o: Int) -> Int? { o < bytes.count ? Int(bytes[o]) : nil }
    func u16(_ o: Int) -> Int? {
        o + 2 <= bytes.count ? Int(bytes[o]) | Int(bytes[o + 1]) << 8 : nil
    }
    func u32(_ o: Int) -> Int? {
        guard o + 4 <= bytes.count else { return nil }
        return Int(UInt32(bytes[o]) | UInt32(bytes[o + 1]) << 8 | UInt32(bytes[o + 2]) << 16 | UInt32(bytes[o + 3]) << 24)
    }
    func i32(_ o: Int) -> Int? {
        guard o + 4 <= bytes.count else { return nil }
        let v = UInt32(bytes[o]) | UInt32(bytes[o + 1]) << 8 | UInt32(bytes[o + 2]) << 16 | UInt32(bytes[o + 3]) << 24
        return Int(Int32(bitPattern: v))
    }
    func f32(_ o: Int) -> Float? {
        guard o + 4 <= bytes.count else { return nil }
        let v = UInt32(bytes[o]) | UInt32(bytes[o + 1]) << 8 | UInt32(bytes[o + 2]) << 16 | UInt32(bytes[o + 3]) << 24
        return Float(bitPattern: v)
    }
    /// 从 o 开始读以 0 结尾的 GBK 字符串
    func gbk(_ o: Int, max: Int = 64) -> String {
        guard o < bytes.count else { return "" }
        var end = o
        while end < bytes.count, end - o < max, bytes[end] != 0 { end += 1 }
        return Reader.decodeGBK(Array(bytes[o..<end]))
    }

    static let gbkEncoding = String.Encoding(rawValue: CFStringConvertEncodingToNSStringEncoding(
        CFStringEncoding(CFStringEncodings.GB_18030_2000.rawValue)))

    static func decodeGBK(_ bytes: [UInt8]) -> String {
        String(data: Data(bytes), encoding: gbkEncoding) ?? ""
    }

    /// 在整个包里找可读的 GBK 中文片段（至少 2 个汉字），给未解析的包做参考
    func readableStrings() -> [String] {
        var result: [String] = []
        var i = 0
        while i < bytes.count {
            var j = i
            while j < bytes.count {
                let b = bytes[j]
                if b >= 0x81, b <= 0xFE, j + 1 < bytes.count, bytes[j + 1] >= 0x40, bytes[j + 1] != 0x7F {
                    j += 2
                } else if b >= 0x20, b < 0x7F {
                    j += 1
                } else { break }
            }
            if j > i {
                let s = Reader.decodeGBK(Array(bytes[i..<j]))
                if s.unicodeScalars.filter({ $0.value >= 0x4E00 && $0.value <= 0x9FFF }).count >= 2 {
                    result.append(s)
                }
                i = j
            } else {
                i += 1
            }
        }
        return result
    }
}

struct GameDecoder {
    let names: NameTables
    var entities: [Int: Entity] = [:]
    var selfID: Int?
    var sceneName = "—"
    var hp: Int?
    var mp: Int?
    var mpPercent: Int?
    var hpPercent: Int?
    var money: Int?        // 属性 35，单位铜
    var exp: Int?          // 属性 41，当前经验值
    var inCombat: Set<Int> = []

    var selfEntity: Entity? { selfID.flatMap { entities[$0] } }

    func who(_ id: Int) -> String {
        if id == selfID { return "【\(entities[id]?.name ?? "自己")】" }
        if let e = entities[id] { return "【\(e.name)】" }
        return "【对象#\(id)】"
    }

    private func pos(_ x: Float?, _ z: Float?) -> String {
        guard let x, let z else { return "" }
        return String(format: "(%.0f, %.0f)", x, z)
    }

    /// 解析一个包，返回要显示的事件（分类，文字）。会顺带更新世界状态。
    mutating func decode(_ packet: [UInt8]) -> [(EventCategory, String)] {
        let r = Reader(bytes: packet)
        guard let type = r.u8(0) else { return [] }
        switch type {
        case 0xCA: // 进入场景
            let scene = r.u16(2) ?? 0
            selfID = r.u16(6)
            entities = [:]
            sceneName = names.scene(scene)
            return [(.system, "进入场景 \(sceneName)（ID \(scene)）\(pos(r.f32(8), r.f32(12)))")]

        case 0xB4: // 角色 / 怪物进入视野
            guard let id = r.u16(1) else { return [] }
            let excel = r.u16(3) ?? 0
            var name = r.gbk(44, max: 32)
            if name.isEmpty { name = names.chars[String(excel)] ?? "对象#\(id)" }
            let e = Entity(id: id, name: name, excelID: excel, level: r.u8(8), isNPC: false, x: r.f32(12), z: r.f32(20))
            entities[id] = e
            if id == selfID { return [] }
            let hp = r.u8(9).map { $0 < 100 ? "，血量 \($0)%" : "" } ?? ""
            return [(.view, "\(who(id)) Lv\(e.level ?? 0) 进入视野 \(pos(e.x, e.z))\(hp)")]

        case 0xBC: // 场景物件进入视野（采集点、可交互物件），wExcelID 对应 object/still_list.txt
            guard let id = r.u16(1) else { return [] }
            let excel = r.u16(3) ?? 0
            let name = names.still(excel)
            entities[id] = Entity(id: id, name: name, excelID: excel, level: nil, isNPC: true, x: r.f32(5), z: r.f32(9))
            return [(.view, "物件「\(name)」进入视野 \(pos(r.f32(5), r.f32(9)))")]

        case 0xD0: // 对象移除：物件被采集或离开视野，其他对象离开视野
            guard let id = r.u16(2) else { return [] }
            let gone = entities[id]
            entities[id] = nil
            if let gone, gone.isNPC { return [(.view, "物件「\(gone.name)」消失（被采集或离开视野）")] }
            return [(.view, "\(who(id)) 离开视野")]

        case 0x80: // 对象死亡或消失：bDeadType @3
            // 实测：1 只在血量降到 0 后出现，是真正的死亡（尸体还在）；
            // 2 用在 NPC 离开、怪物走出视野、尸体消失等情况，所以只算消失
            guard let id = r.u16(1) else { return [] }
            let kind = r.u8(3) ?? 0
            let name = who(id)
            if kind == 1 { return [(.combat, "\(name) 死亡")] }
            entities[id] = nil
            return [(.view, "\(name) 消失")]

        case 0x88: // 技能开始
            guard let id = r.u16(1), let skill = r.u16(3) else { return [] }
            let target = r.u16(5) ?? 0
            var text = "\(who(id)) 施放「\(names.skill(skill))」"
            if target != 0 { text += " → \(who(target))" }
            if id == selfID, let x = r.f32(14), let z = r.f32(18) {
                entities[id]?.x = x
                entities[id]?.z = z
            }
            return [(.combat, text)]

        case 0x87: // 技能结束
            guard let id = r.u16(1) else { return [] }
            let skill = r.u16(3) ?? 0
            return [(.combat, skill == 0 ? "\(who(id)) 技能结束" : "\(who(id)) 「\(names.skill(skill))」结束")]

        case 0x86: // 选中目标
            guard let id = r.u16(1), let target = r.u16(3) else { return [] }
            return [(.combat, target == 0 ? "\(who(id)) 取消目标" : "\(who(id)) 选中 \(who(target))")]

        case 0x8F: // 状态增删：0 添加，1 移除，2 同步已有状态
            guard let id = r.u16(1), let kind = r.u8(3), let state = r.u16(4) else { return [] }
            let name = names.state(state)
            switch kind {
            case 0: return [(.state, "\(who(id)) 获得状态「\(name)」")]
            case 1: return [(.state, "\(who(id)) 失去状态「\(name)」")]
            // 2 是同步已有状态，包结构不同（录像里存成 msg_bin），这里的编号不是状态 ID，先不显示具体名称
            default: return [(.other, "\(who(id)) 状态同步（类型 \(kind)，\(packet.count) 字节）")]
            }

        case 0x81: // 移动
            guard let id = r.u16(1), let x = r.f32(4), let z = r.f32(12) else { return [] }
            entities[id]?.x = x
            entities[id]?.z = z
            let speed = r.f32(24) ?? 0
            return [(.move, speed > 0 ? "\(who(id)) 移动到 \(pos(x, z))" : "\(who(id)) 停在 \(pos(x, z))")]

        case 0x83: // 转向：低 15 位角度，最高位 bNow
            guard let id = r.u16(1), let raw = r.u16(3) else { return [] }
            return [(.move, "\(who(id)) 转向 \(raw & 0x7FFF)°")]

        case 0x84: // 切换移动方式
            guard let id = r.u16(1) else { return [] }
            return [(.move, "\(who(id)) 切换移动方式 \(r.u8(3) ?? 0)")]

        case 0xB6: // 属性值；8 = 蓝量
            guard let attr = r.u16(1), let value = r.i32(3) else { return [] }
            switch attr {
            case 7:  // 推测是生命值：随时间回复，和蓝量一起更新
                hp = value
                return [(.stats, "生命 \(value)")]
            case 8:
                mp = value
                return [(.stats, "蓝量 \(value)")]
            case 35: // 金钱，单位铜；数值可能很大，按无符号读
                let now = r.u32(3) ?? 0
                defer { money = now }
                guard let before = money else { return [(.stats, "金钱 \(Self.formatMoney(now))")] }
                let diff = now - before
                if diff == 0 { return [] }
                let sign = diff > 0 ? "+" : "−"
                return [(.gain, "金钱 \(sign)\(Self.formatMoney(abs(diff)))（现有 \(Self.formatMoney(now))）")]
            case 41: // 当前经验值；击杀获得的经验由 0x91 头顶飘字单独显示，这里只记总量
                let now = r.u32(3) ?? 0
                defer { exp = now }
                if let before = exp, now != before {
                    return [(.stats, "当前经验 \(now)（\(now > before ? "+" : "")\(now - before)）")]
                }
                return [(.stats, "当前经验 \(now)")]
            default:
                return [(.stats, "属性 #\(attr) = \(value)")]
            }

        case 0x85: // 对象标志：bFlagType @3，wData0 @4，wData1 @6
            guard let id = r.u16(1), let flag = r.u8(3) else { return [] }
            let data = r.u16(4) ?? 0
            switch flag {
            case 8:  // 血量百分比
                if id == selfID { hpPercent = data }
                return [(.stats, "\(who(id)) 血量 \(data)%")]
            case 9:  // 蓝量百分比
                if id == selfID { mpPercent = data }
                return [(.stats, "\(who(id)) 蓝量 \(data)%")]
            case 1:  // 推测：战斗状态，1 进入战斗，0 脱离战斗
                let was = inCombat.contains(id)
                if data != 0 { inCombat.insert(id) } else { inCombat.remove(id) }
                if was == (data != 0) { return [] }
                return [(.combat, data != 0 ? "\(who(id)) 进入战斗" : "\(who(id)) 脱离战斗")]
            case 0:
                return [(.other, "\(who(id)) 所在区域 #\(data)")]
            case 20:
                return [(.other, "\(who(id)) 关联召唤物 \(who(r.u16(6) ?? 0))")]
            case 26:
                return [(.other, "\(who(id)) 的主人是 \(who(data))")]
            default:
                return [(.other, "\(who(id)) 标志 #\(flag) = \(data)")]
            }

        case 0xBF: // 冷却计时，dKeepTime 单位秒
            let keep = r.i32(4) ?? 0
            return [(.combat, "冷却 #\(r.u16(2) ?? 0)：\(Self.duration(keep))")]

        case 0x98, 0x9D: // 头顶称号
            guard let id = r.u16(1) else { return [] }
            let title = r.gbk(3, max: 40)
            return title.isEmpty ? [] : [(.view, "\(who(id)) 称号「\(title)」")]

        case 0x89: // 装备外观：wID @1，@3 的最低位是 bEquipType、其余位是栏位 bIdx，wData @4
            guard let id = r.u16(1), let bits = r.u8(3) else { return [] }
            return [(.other, "\(who(id)) 装备栏 \(bits >> 1) 变为 #\(r.u16(4) ?? 0)（类型 \(bits & 1)）")]

        case 0xDA: // 天气
            return [(.system, "天气变化：类型 \(r.u8(1) ?? 0)，状态 \(r.u8(2) ?? 0)")]

        case 0xE3: // 系统消息，例如 GM 物品发放
            return [(.system, systemMessage(r))]

        case 0xC9:
            return [(.system, "收到自己的完整角色数据（\(packet.count) 字节）")]

        // 以下是结构已知、含义不明或价值不大的包，归入「其他」
        case 0xBA: // 内容大多是随机数据，录像器也会跳过，推测是同步 / 校验包
            return [(.other, "同步包 0xBA（\(packet.count) 字节）")]
        case 0xCC: // 物品 / 背包操作，bWork @1 决定格式（见客户端序列化函数 0x49f880）
            return itemPacket(r)
        case 0xD4: // 与 0xcc 类似的数据更新，含义待查
            return [(.other, String(format: "数据更新 0xD4：编号 %d，类型 %d", r.u16(2) ?? 0, r.u8(1) ?? 0))]
        case 0xCB: // 技能相关数据：bPlayerType @1，wSkillID @2，bData @4
            return [(.other, "技能数据「\(names.skill(r.u16(2) ?? 0))」= \(r.u8(4) ?? 0)")]
        case 0xB7: // bFlagType @1，wFlagID @2，bData @4
            return [(.other, "标志 0xB7：类型 \(r.u8(1) ?? 0)，编号 \(r.u16(2) ?? 0)，值 \(r.u8(4) ?? 0)")]
        case 0xB8: // bFlagType @1，wData @2
            return [(.other, "标志 0xB8：类型 \(r.u8(1) ?? 0)，值 \(r.u16(2) ?? 0)")]
        case 0x8E: // wID @1，iTime @4，iData @8
            return [(.other, "\(who(r.u16(1) ?? 0)) 计时数据 \(r.i32(4) ?? 0) / \(r.i32(8) ?? 0)")]
        case 0x91: // 头顶飘字：wID @1，数值 @3，byPateType @7
            guard let id = r.u16(1), let value = r.i32(3), let kind = r.u8(7) else { return [] }
            switch kind {
            case 6:   // 实测：击杀豺时出现在怪物头上，是造成的伤害
                return [(.combat, "\(who(id)) 受到伤害 \(value)")]
            case 50:  // 实测：击杀豺后出现在自己头上，推测是获得的经验
                return [(.gain, "\(who(id)) 获得经验 +\(value)")]
            default:
                return [(.other, "\(who(id)) 头顶数字 \(value)（类型 \(kind)）")]
            }
        case 0xA2, 0xA3, 0xFF, 0xF1:
            let strings = r.readableStrings()
            let hint = strings.isEmpty ? "" : "：" + strings.prefix(3).joined(separator: "、")
            return [(.other, String(format: "数据包 0x%02X（%d 字节）", type, packet.count) + hint)]

        case 0x00..<0x80:
            // 命令包：走分发函数的另一路（第二个钩子），实测只在登录、选角、进入游戏时出现
            return [(.other, String(format: "命令包 0x%02X（%d 字节，登录 / 选角流程）", type, packet.count))]

        default:
            let strings = r.readableStrings()
            let hint = strings.isEmpty ? "" : "：" + strings.prefix(3).joined(separator: "、")
            return [(.unknown, String(format: "未解析的包 0x%02X（%d 字节）", type, packet.count) + hint)]
        }
    }

    /// 0xcc 物品操作
    private func itemPacket(_ r: Reader) -> [(EventCategory, String)] {
        let work = r.u8(1) ?? 255
        switch work {
        case 0:  // 背包新增一条物品记录：byBox @2，wExcelID @3，wAutoID @5，byNum @11
            let item = names.item(r.u16(3) ?? 0)
            return [(.other, "背包新增「\(item)」×\(r.u16(11) ?? 0)（格子编号 \(r.u16(5) ?? 0)）")]
        case 1:  // 删除一条物品记录：wAutoID @2
            return [(.other, "背包移除物品（格子编号 \(r.u16(2) ?? 0)）")]
        case 2:  // 数量变化：wAutoID @2，byNum @4
            return [(.other, "背包物品数量变为 \(r.u16(4) ?? 0)（格子编号 \(r.u16(2) ?? 0)）")]
        case 3:  // 移动 / 合并：wSrcAutoID @2，wDestAutoID @4
            return [(.other, "背包物品移动（\(r.u16(2) ?? 0) → \(r.u16(4) ?? 0)）")]
        case 4:  // 获得物品通知：wExcelID @2，byNum @4，byBox @6
            return [(.gain, "获得物品「\(names.item(r.u16(2) ?? 0))」×\(r.u16(4) ?? 0)")]
        case 5:  // 失去物品通知（使用、消耗、出售等）：wExcelID @2，byNum @4，byBox @6
            return [(.gain, "失去物品「\(names.item(r.u16(2) ?? 0))」×\(r.u16(4) ?? 0)")]
        case 6:  // 物品属性变化：wAutoID @2，bDataType @4，dwData @5
            return [(.other, "物品属性变化（格子编号 \(r.u16(2) ?? 0)，类型 \(r.u8(4) ?? 0)，值 \(r.i32(5) ?? 0)）")]
        case 9:  // 商店物品列表：wExcelID @3，wMchtID @5，iItemNum @7，iPrize @23
            return [(.other, "商店物品「\(names.item(r.u16(3) ?? 0))」库存 \(r.i32(7) ?? 0)，价格 \(r.i32(23) ?? 0)")]
        default:
            return [(.other, "物品操作（类型 \(work)，\(r.bytes.count) 字节）")]
        }
    }

    /// 金钱换算：1 砖 = 100 金，1 金 = 100 银，1 银 = 100 铜
    static func formatMoney(_ copper: Int) -> String {
        let units = [(1_000_000, "砖"), (10_000, "金"), (100, "银"), (1, "铜")]
        var rest = copper
        var parts: [String] = []
        for (size, name) in units where rest >= size {
            parts.append("\(rest / size)\(name)")
            rest %= size
        }
        return parts.isEmpty ? "0铜" : parts.joined()
    }

    static func duration(_ seconds: Int) -> String {
        if seconds < 60 { return "\(seconds) 秒" }
        if seconds < 3600 { return "\(seconds / 60) 分 \(seconds % 60) 秒" }
        return "\(seconds / 3600) 小时 \(seconds % 3600 / 60) 分"
    }

    private func systemMessage(_ r: Reader) -> String {
        let text = r.readableStrings().joined(separator: " ")
        var parts: [String] = []
        if let t = text.range(of: #"<title:([^>]*)>"#, options: .regularExpression) {
            parts.append(String(text[t]).replacingOccurrences(of: "<title:", with: "").replacingOccurrences(of: ">", with: ""))
        }
        if let range = text.range(of: #"wExcelID=(\d+),byNum=(\d+)"#, options: .regularExpression) {
            let nums = text[range].split(whereSeparator: { !$0.isNumber }).compactMap { Int($0) }
            if nums.count == 2 { parts.append("\(names.item(nums[0])) ×\(nums[1])") }
        }
        return "系统消息：" + (parts.isEmpty ? text : parts.joined(separator: "："))
    }
}

// MARK: - 网络接收

final class BridgeListener {
    var onPacket: (([UInt8], UInt32) -> Void)?
    var onStatus: ((String) -> Void)?
    private var listener: NWListener?
    private let queue = DispatchQueue(label: "bridge.listener")

    func start(port: UInt16 = 47800) {
        do {
            let params = NWParameters.tcp
            params.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: NWEndpoint.Port(rawValue: port)!)
            params.allowLocalEndpointReuse = true
            let listener = try NWListener(using: params)
            listener.newConnectionHandler = { [weak self] conn in self?.accept(conn) }
            listener.stateUpdateHandler = { [weak self] state in
                switch state {
                case .ready: self?.onStatus?("等待游戏连接（端口 \(port)）")
                case .failed(let error): self?.onStatus?("监听失败：\(error.localizedDescription)，端口可能被占用")
                default: break
                }
            }
            listener.start(queue: queue)
            self.listener = listener
        } catch {
            onStatus?("监听失败：\(error.localizedDescription)")
        }
    }

    private func accept(_ conn: NWConnection) {
        onStatus?("游戏已连接")
        var buffer = [UInt8]()
        func receive() {
            conn.receive(minimumIncompleteLength: 1, maximumLength: 1 << 16) { [weak self] data, _, done, error in
                guard let self else { return }
                if let data { buffer.append(contentsOf: data) }
                while buffer.count >= 16 {
                    guard buffer[0] == 0x58, buffer[1] == 0x58, buffer[2] == 0x42, buffer[3] == 0x31 else {
                        self.onStatus?("数据格式不对，断开连接")
                        conn.cancel()
                        return
                    }
                    let r = Reader(bytes: buffer)
                    let tick = UInt32(truncatingIfNeeded: r.i32(8) ?? 0)
                    let size = Int(UInt32(truncatingIfNeeded: r.i32(12) ?? 0))
                    guard buffer.count >= 16 + size else { break }
                    let packet = Array(buffer[16..<(16 + size)])
                    buffer.removeFirst(16 + size)
                    self.onPacket?(packet, tick)
                }
                if done || error != nil {
                    self.onStatus?("游戏已断开，等待重新连接")
                    conn.cancel()
                } else {
                    receive()
                }
            }
        }
        conn.start(queue: queue)
        receive()
    }
}

// MARK: - 界面数据

@MainActor
final class LiveModel: ObservableObject {
    @Published var events: [LiveEvent] = []
    @Published var status = "启动中…"
    @Published var visible: Set<EventCategory> = EventCategory.defaultVisible
    @Published var paused = false
    @Published var packetCount = 0
    @Published var sceneName = "—"
    @Published var selfLine = ""
    @Published var wealthLine = ""
    @Published var nearby = 0

    private var decoder = GameDecoder(names: NameTables.load())
    /// 原始包日志：~/Library/Logs/XunxianViewer/packets-日期时间.jsonl，便于事后分析新包类型
    private var rawLog: FileHandle?
    private var rawSeq = 0
    private let listener = BridgeListener()
    private var nextID = 0
    private let maxEvents = 3000

    nonisolated init() {}

    func start() {
        openRawLog()
        listener.onStatus = { [weak self] text in Task { @MainActor in self?.status = text } }
        listener.onPacket = { [weak self] packet, _ in Task { @MainActor in self?.handle(packet) } }
        listener.start()
    }

    var shown: [LiveEvent] { events.filter { visible.contains($0.category) } }

    func clear() { events.removeAll() }

    private func openRawLog() {
        let dir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/XunxianViewer")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let f = DateFormatter()
        f.dateFormat = "yyyyMMdd-HHmmss"
        let url = dir.appendingPathComponent("packets-\(f.string(from: Date())).jsonl")
        FileManager.default.createFile(atPath: url.path, contents: nil)
        rawLog = try? FileHandle(forWritingTo: url)
    }

    private func logRaw(_ packet: [UInt8]) {
        guard let rawLog else { return }
        rawSeq += 1
        let hex = packet.map { String(format: "%02x", $0) }.joined()
        let line = "{\"seq\":\(rawSeq),\"ms\":\(Int(Date().timeIntervalSince1970 * 1000)),\"bType\":\(packet.first ?? 0),\"size\":\(packet.count),\"hex\":\"\(hex)\"}\n"
        rawLog.write(Data(line.utf8))
    }

    func handle(_ packet: [UInt8]) {
        logRaw(packet)
        packetCount += 1
        let produced = decoder.decode(packet)
        sceneName = decoder.sceneName
        nearby = decoder.entities.count - (decoder.selfID != nil && decoder.entities[decoder.selfID!] != nil ? 1 : 0)
        if let me = decoder.selfEntity {
            var line = me.name
            if let x = me.x, let z = me.z { line += String(format: "  (%.0f, %.0f)", x, z) }
            if let hp = decoder.hp { line += "  血 \(hp)" }
            if let mp = decoder.mp { line += "  蓝 \(mp)" }
            if let p = decoder.mpPercent { line += "（\(p)%）" }
            if let p = decoder.hpPercent { line += "  血量 \(p)%" }
            selfLine = line
        }
        var wealth: [String] = []
        if let money = decoder.money { wealth.append("金钱 \(GameDecoder.formatMoney(money))") }
        if let exp = decoder.exp { wealth.append("经验 \(exp)") }
        wealthLine = wealth.joined(separator: "  ")
        guard !paused else { return }
        let now = Date()
        for (category, text) in produced {
            nextID += 1
            events.append(LiveEvent(id: nextID, time: now, category: category, text: text))
        }
        if events.count > maxEvents { events.removeFirst(events.count - maxEvents) }
    }
}

// MARK: - 界面

struct ContentView: View {
    @StateObject private var model: LiveModel

    init(model: LiveModel = LiveModel()) {
        _model = StateObject(wrappedValue: model)
    }
    @AppStorage("opacity") private var opacity = 0.92

    private static let timeFormat: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f
    }()

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            eventList
            Divider()
            footer
        }
        .frame(minWidth: 320, idealWidth: 380, minHeight: 300, idealHeight: 560)
        .background(.regularMaterial)
        .opacity(opacity)
        .onAppear {
            model.start()
            WindowConfigurator.makeFloating()
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Circle()
                    .fill(model.status == "游戏已连接" ? Color.green : Color.orange)
                    .frame(width: 8, height: 8)
                Text(model.status).font(.caption).foregroundStyle(.secondary)
                Spacer()
                Text("\(model.packetCount) 包").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            }
            HStack {
                Label(model.sceneName, systemImage: "map").font(.callout.weight(.medium))
                Spacer()
                Label("\(model.nearby)", systemImage: "person.2").font(.callout).help("视野内的对象数")
            }
            if !model.selfLine.isEmpty {
                Text(model.selfLine).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            }
            if !model.wealthLine.isEmpty {
                Text(model.wealthLine).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
    }

    private var eventList: some View {
        ScrollViewReader { proxy in
            List(model.shown) { event in
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(Self.timeFormat.string(from: event.time))
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.tertiary)
                    RoundedRectangle(cornerRadius: 1.5)
                        .fill(event.category.color)
                        .frame(width: 3, height: 12)
                    Text(event.text)
                        .font(.callout)
                        .textSelection(.enabled)
                }
                .id(event.id)
                .listRowSeparator(.hidden)
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .onChange(of: model.events.last?.id) { _, last in
                if let last, !model.paused { proxy.scrollTo(last, anchor: .bottom) }
            }
        }
    }

    private var footer: some View {
        VStack(spacing: 6) {
            HStack(spacing: 4) {
                ForEach(EventCategory.allCases) { category in
                    let on = model.visible.contains(category)
                    Button {
                        if on { model.visible.remove(category) } else { model.visible.insert(category) }
                    } label: {
                        Text(category.rawValue)
                            .font(.caption)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 3)
                            .background(Capsule().fill(on ? category.color.opacity(0.25) : Color.clear))
                            .overlay(Capsule().strokeBorder(category.color.opacity(on ? 0.6 : 0.3)))
                            .foregroundStyle(on ? .primary : .secondary)
                    }
                    .buttonStyle(.plain)
                }
            }
            HStack {
                Button(model.paused ? "继续" : "暂停") { model.paused.toggle() }
                Button("清空") { model.clear() }
                Spacer()
                Image(systemName: "circle.lefthalf.filled").foregroundStyle(.secondary)
                Slider(value: $opacity, in: 0.4...1).frame(width: 90)
            }
            .controlSize(.small)
        }
        .padding(8)
    }
}

/// 把主窗口设成悬浮在其他程序（包括游戏）上方
enum WindowConfigurator {
    @MainActor static func makeFloating() {
        DispatchQueue.main.async {
            for window in NSApp.windows {
                window.level = .floating
                window.collectionBehavior.insert([.canJoinAllSpaces, .fullScreenAuxiliary])
                window.isOpaque = false
                window.backgroundColor = .clear
                window.titlebarAppearsTransparent = true
                window.title = "寻仙实时动态"
            }
        }
    }
}

#if !VIEWER_TEST
@main
struct XunxianViewerApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate

    var body: some Scene {
        WindowGroup("寻仙实时动态") {
            ContentView()
        }
        .windowResizability(.contentMinSize)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}
#endif
