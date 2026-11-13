// 寻仙登录器：保存账号密码，一键启动客户端并自动登录。
//
// 原理：tty3d.exe 支持 `-q "x:账号:x:密码"` 参数，登录窗口首次显示时会读取它并自动登录。
// 登录器的辅助文件都放在客户端目录的 launcher/ 子文件夹：启动模板 start_crossover.bat、
// 每次生成的 _launcher.bat、账号文件 launcher_accounts.json。账号密码启动时通过环境变量交给 bat。

import AppKit
import Foundation
import Security
import SwiftUI

// MARK: - 设置

struct LauncherSettings: Codable, Equatable {
    var clientDir: String = ""
    var host: String = "game.xunxian.wiki"
    var port: Int = 3100
    var bottle: String = "Xunxian64"
    var winePath: String = "/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/wine"
}

struct SavedAccount: Codable, Identifiable, Hashable {
    var id: String { account }
    var account: String
    var password: String = ""
    var note: String = ""
    var lastLogin: Date?

    init(account: String) { self.account = account }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        account = try c.decode(String.self, forKey: .account)
        password = try c.decodeIfPresent(String.self, forKey: .password) ?? ""
        note = try c.decodeIfPresent(String.self, forKey: .note) ?? ""
        lastLogin = try c.decodeIfPresent(Date.self, forKey: .lastLogin)
    }
}

// MARK: - 账号文件

enum AccountFile {
    static let name = "launcher_accounts.json"

    static func url(_ clientDir: String) -> URL {
        GameLauncher.supportURL(clientDir).appendingPathComponent(name)
    }

    static func load(_ clientDir: String) -> [SavedAccount]? {
        // 旧版把账号文件放在客户端根目录，读到时挪进 launcher/
        let legacy = URL(fileURLWithPath: clientDir).appendingPathComponent(name)
        let fm = FileManager.default
        if !fm.fileExists(atPath: url(clientDir).path), fm.fileExists(atPath: legacy.path) {
            try? fm.createDirectory(at: GameLauncher.supportURL(clientDir), withIntermediateDirectories: true)
            try? fm.moveItem(at: legacy, to: url(clientDir))
        }
        guard let data = try? Data(contentsOf: url(clientDir)) else { return nil }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try? decoder.decode([SavedAccount].self, from: data)
    }

    static func save(_ accounts: [SavedAccount], to clientDir: String) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        encoder.dateEncodingStrategy = .iso8601
        try FileManager.default.createDirectory(at: GameLauncher.supportURL(clientDir), withIntermediateDirectories: true)
        try encoder.encode(accounts).write(to: url(clientDir), options: .atomic)
    }
}

// MARK: - 旧版钥匙串（仅用于把旧数据迁移到账号文件）

enum LegacyKeychain {
    static let service = "wiki.xunxian.launcher"

    private static func query(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    static func load(_ account: String) -> String? {
        var q = query(account)
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func delete(_ account: String) {
        SecItemDelete(query(account) as CFDictionary)
    }
}

// MARK: - 启动逻辑

enum LaunchError: LocalizedError {
    case message(String)
    var errorDescription: String? {
        if case .message(let text) = self { return text }
        return nil
    }
}

enum GameLauncher {
    /// 客户端目录下存放登录器辅助文件的子文件夹
    static let supportDir = "launcher"
    static let batName = "_launcher.bat"
    static let templateName = "start_crossover.bat"

    static func supportURL(_ clientDir: String) -> URL {
        URL(fileURLWithPath: clientDir).appendingPathComponent(supportDir)
    }

    static func isClientDir(_ path: String) -> Bool {
        FileManager.default.fileExists(atPath: (path as NSString).appendingPathComponent("release/tty3d.exe"))
    }

    /// 找客户端目录：登录器放在客户端目录里时自动识别。
    static func guessClientDir() -> String? {
        var url = Bundle.main.bundleURL.deletingLastPathComponent()
        for _ in 0..<3 {
            if isClientDir(url.path) { return url.path }
            url = url.deletingLastPathComponent()
        }
        return nil
    }

    static func validate(account: String, password: String) throws {
        if account.isEmpty { throw LaunchError.message("请填写账号") }
        if password.isEmpty { throw LaunchError.message("请填写密码") }
        let banned = CharacterSet(charactersIn: ":\"%\r\n")
        if account.rangeOfCharacter(from: banned) != nil || password.rangeOfCharacter(from: banned) != nil {
            throw LaunchError.message("账号和密码不能包含 : \" % 或换行")
        }
    }

    static func isPublicIPv4(_ text: String) -> Bool {
        let parts = text.split(separator: ".").compactMap { UInt8($0) }
        guard parts.count == 4 else { return false }
        let (a, b) = (parts[0], parts[1])
        if a == 0 || a == 10 || a == 127 || a >= 224 { return false }
        if a == 169 && b == 254 { return false }
        if a == 172 && (16...31).contains(b) { return false }
        if a == 192 && b == 168 { return false }
        if a == 100 && (64...127).contains(b) { return false }
        return true
    }

    static func resolve(_ host: String) async throws -> String {
        if isPublicIPv4(host) { return host }
        if let ip = systemResolve(host), isPublicIPv4(ip) { return ip }
        // 本机 DNS 失败时走 Cloudflare DoH，与原启动脚本一致
        var comps = URLComponents(string: "https://cloudflare-dns.com/dns-query")!
        comps.queryItems = [URLQueryItem(name: "name", value: host), URLQueryItem(name: "type", value: "A")]
        var request = URLRequest(url: comps.url!, timeoutInterval: 8)
        request.setValue("application/dns-json", forHTTPHeaderField: "Accept")
        let (data, _) = try await URLSession.shared.data(for: request)
        struct Answer: Decodable { let type: Int; let data: String }
        struct Reply: Decodable { let Answer: [Answer]? }
        let reply = try JSONDecoder().decode(Reply.self, from: data)
        if let ip = reply.Answer?.first(where: { $0.type == 1 })?.data, isPublicIPv4(ip) { return ip }
        throw LaunchError.message("无法解析服务器地址 \(host)")
    }

    private static func systemResolve(_ host: String) -> String? {
        var hints = addrinfo(ai_flags: 0, ai_family: AF_INET, ai_socktype: SOCK_STREAM,
                             ai_protocol: 0, ai_addrlen: 0, ai_canonname: nil, ai_addr: nil, ai_next: nil)
        var result: UnsafeMutablePointer<addrinfo>?
        guard getaddrinfo(host, nil, &hints, &result) == 0, let info = result else { return nil }
        defer { freeaddrinfo(result) }
        guard let addr = info.pointee.ai_addr else { return nil }
        var buffer = [CChar](repeating: 0, count: Int(INET_ADDRSTRLEN))
        let ok = addr.withMemoryRebound(to: sockaddr_in.self, capacity: 1) { sin -> Bool in
            var inAddr = sin.pointee.sin_addr
            return inet_ntop(AF_INET, &inAddr, &buffer, socklen_t(INET_ADDRSTRLEN)) != nil
        }
        return ok ? String(cString: buffer) : nil
    }

    /// 以 start_crossover.bat 为模板（保留 GBK 编码的服务器名），换上最新 IP，
    /// 并追加 -q 参数。账号密码用环境变量占位，bat 文件里不含密码。
    static func writeBat(clientDir: String, ip: String, port: Int) throws {
        let dir = supportURL(clientDir)
        // 模板优先在 launcher/ 里找，兼容旧版放在根目录的情况
        let candidates = [dir.appendingPathComponent(templateName),
                          URL(fileURLWithPath: clientDir).appendingPathComponent(templateName)]
        guard let data = candidates.lazy.compactMap({ try? Data(contentsOf: $0) }).first else {
            throw LaunchError.message("找不到 launcher/\(templateName)")
        }
        // GBK 中文字节都 ≥ 0x80，按 Latin-1 处理可逐字节原样保留
        let text = String(data: data, encoding: .isoLatin1) ?? ""
        let pattern = try NSRegularExpression(pattern: #"^(start\s.*-g\s+")[0-9.]+:\d+(,[^"]*")(.*)$"#,
                                              options: [.anchorsMatchLines])
        let range = NSRange(text.startIndex..., in: text)
        guard pattern.firstMatch(in: text, range: range) != nil else {
            throw LaunchError.message("start_crossover.bat 格式不符合预期")
        }
        let replacement = "$1\(ip):\(port)$2 -q \"0:%XXL_USER%:0:%XXL_PASS%\""
        var output = pattern.stringByReplacingMatches(in: text, range: range, withTemplate: replacement)
        // 生成的 bat 在 launcher/ 里，先切回上一级的客户端根目录，release\tty3d.exe 才找得到
        let cdLine = try NSRegularExpression(pattern: #"^cd\s+/d\s+.*$"#, options: [.anchorsMatchLines, .caseInsensitive])
        output = cdLine.stringByReplacingMatches(in: output, range: NSRange(output.startIndex..., in: output),
                                                 withTemplate: #"cd /d "%~dp0..""#)
        output = output.replacingOccurrences(of: "\r\n", with: "\n").replacingOccurrences(of: "\n", with: "\r\n")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try output.data(using: .isoLatin1)!.write(to: dir.appendingPathComponent(batName))
    }

    static func launch(settings: LauncherSettings, account: String, password: String) async throws -> String {
        try validate(account: account, password: password)
        guard isClientDir(settings.clientDir) else {
            throw LaunchError.message("客户端目录无效，请在设置里选择 MhXunxian 文件夹")
        }
        guard FileManager.default.isExecutableFile(atPath: settings.winePath) else {
            throw LaunchError.message("找不到 CrossOver 的 wine：\(settings.winePath)")
        }
        let ip = try await resolve(settings.host)
        try writeBat(clientDir: settings.clientDir, ip: ip, port: settings.port)

        let process = Process()
        process.executableURL = URL(fileURLWithPath: settings.winePath)
        process.arguments = ["--bottle", settings.bottle, "--workdir", settings.clientDir, "cmd", "/c", "\(supportDir)\\\(batName)"]
        process.currentDirectoryURL = URL(fileURLWithPath: settings.clientDir)
        var env = ProcessInfo.processInfo.environment
        env["XXL_USER"] = account
        env["XXL_PASS"] = password
        process.environment = env
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()
        let status: Int32 = await withCheckedContinuation { cont in
            DispatchQueue.global().async {
                process.waitUntilExit()
                cont.resume(returning: process.terminationStatus)
            }
        }
        if status != 0 { throw LaunchError.message("CrossOver 启动失败（退出码 \(status)）") }
        return ip
    }
}

// MARK: - 数据存储

@MainActor
final class Store: ObservableObject {
    @Published var accounts: [SavedAccount] = []
    @Published var settings = LauncherSettings()
    @Published var saveError: String?

    private let defaults = UserDefaults.standard

    init() {
        if let data = defaults.data(forKey: "settings"),
           let value = try? JSONDecoder().decode(LauncherSettings.self, from: data) {
            settings = value
        }
        if !GameLauncher.isClientDir(settings.clientDir), let dir = GameLauncher.guessClientDir() {
            settings.clientDir = dir
            saveSettings()
        }
        reloadAccounts()
    }

    func saveSettings() {
        defaults.set(try? JSONEncoder().encode(settings), forKey: "settings")
        reloadAccounts()
    }

    /// 从客户端目录读取账号文件；第一次运行时把旧版（UserDefaults + 钥匙串）数据迁移过来。
    func reloadAccounts() {
        guard GameLauncher.isClientDir(settings.clientDir) else { accounts = []; return }
        if let list = AccountFile.load(settings.clientDir) {
            accounts = list
            return
        }
        accounts = []
        guard let data = defaults.data(forKey: "accounts"),
              var legacy = try? JSONDecoder().decode([SavedAccount].self, from: data) else { return }
        for index in legacy.indices {
            legacy[index].password = LegacyKeychain.load(legacy[index].account) ?? ""
        }
        accounts = legacy
        if saveAccounts() {
            for item in legacy { LegacyKeychain.delete(item.account) }
            defaults.removeObject(forKey: "accounts")
        }
    }

    @discardableResult
    private func saveAccounts() -> Bool {
        do {
            try AccountFile.save(accounts, to: settings.clientDir)
            saveError = nil
            return true
        } catch {
            saveError = "账号文件保存失败：\(error.localizedDescription)"
            return false
        }
    }

    func password(for account: String) -> String {
        accounts.first(where: { $0.account == account })?.password ?? ""
    }

    func upsert(account: String, password: String, touch: Bool) {
        var item = accounts.first(where: { $0.account == account }) ?? SavedAccount(account: account)
        item.password = password
        if touch { item.lastLogin = Date() }
        accounts.removeAll { $0.account == account }
        accounts.append(item)
        accounts.sort { ($0.lastLogin ?? .distantPast) > ($1.lastLogin ?? .distantPast) }
        saveAccounts()
    }

    func setNote(_ note: String, for account: String) {
        guard let index = accounts.firstIndex(where: { $0.account == account }) else { return }
        accounts[index].note = note.trimmingCharacters(in: .whitespacesAndNewlines)
        saveAccounts()
    }

    func remove(_ account: String) {
        accounts.removeAll { $0.account == account }
        saveAccounts()
    }
}

// MARK: - 界面

struct ContentView: View {
    @StateObject private var store = Store()
    @State private var selection: String?
    @State private var account = ""
    @State private var password = ""
    @State private var noteTarget: String?
    @State private var noteDraft = ""
    @State private var showPassword = false
    @State private var remember = true
    @State private var busy = false
    @State private var status = ""
    @State private var statusIsError = false
    @State private var showSettings = false
    @FocusState private var focusedField: InputField?

    enum InputField { case account, password }

    var body: some View {
        HStack(spacing: 0) {
            accountList
                .frame(width: 210)
            Divider()
            form
                .frame(width: 340)
        }
        .frame(minHeight: 400, idealHeight: 430, maxHeight: .infinity)
        .sheet(isPresented: $showSettings) {
            SettingsView(settings: $store.settings) {
                store.saveSettings()
                showSettings = false
            }
        }
        .onAppear {
            if !GameLauncher.isClientDir(store.settings.clientDir) { showSettings = true }
            if let first = store.accounts.first { select(first.account) }
        }
    }

    private var accountList: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("已保存的账号")
                .font(.headline)
                .padding([.horizontal, .top], 12)
                .padding(.bottom, 6)
            List(selection: $selection) {
                ForEach(store.accounts) { item in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(item.account).font(.body)
                        if !item.note.isEmpty {
                            Text(item.note).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .tag(item.account)
                    .contextMenu {
                        Button("登录") { select(item.account); startLogin() }
                        Button(item.note.isEmpty ? "设置备注…" : "修改备注…") {
                            noteDraft = item.note
                            noteTarget = item.account
                        }
                        Divider()
                        Button("删除", role: .destructive) { delete(item.account) }
                    }
                }
            }
            .onChange(of: selection) { _, value in
                if let value { select(value) }
            }
            .alert("设置备注", isPresented: Binding(
                get: { noteTarget != nil },
                set: { if !$0 { noteTarget = nil } }
            )) {
                TextField("例如角色名，留空则清除", text: $noteDraft)
                Button("保存") {
                    if let target = noteTarget { store.setNote(noteDraft, for: target) }
                    noteTarget = nil
                }
                .keyboardShortcut(.defaultAction)
                Button("取消", role: .cancel) { noteTarget = nil }
            } message: {
                Text(noteTarget ?? "")
            }
            HStack {
                Button {
                    selection = nil
                    account = ""; password = ""
                    status = ""
                } label: { Label("新账号", systemImage: "plus") }
                Spacer()
                Button {
                    if let selection { delete(selection) }
                } label: { Image(systemName: "trash") }
                .disabled(selection == nil)
                .help("删除选中的账号和它保存的密码")
            }
            .buttonStyle(.borderless)
            .padding(10)
        }
    }

    private var form: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
                .padding(.bottom, 18)

            VStack(alignment: .leading, spacing: 14) {
                field("账号") {
                    InputBox(focused: focusedField == .account) {
                        TextField("游戏账号", text: $account)
                            .focused($focusedField, equals: .account)
                            .onSubmit(startLogin)
                    }
                }
                field("密码") {
                    InputBox(focused: focusedField == .password) {
                        Group {
                            if showPassword {
                                TextField("密码", text: $password)
                            } else {
                                SecureField("密码", text: $password)
                            }
                        }
                        .focused($focusedField, equals: .password)
                        .onSubmit(startLogin)
                        Button { showPassword.toggle() } label: {
                            Image(systemName: showPassword ? "eye.slash" : "eye")
                                .foregroundStyle(.secondary)
                                .frame(width: 20, height: 20)
                                .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .help(showPassword ? "隐藏密码" : "显示密码")
                    }
                }
            }
            .padding(16)
            .background(RoundedRectangle(cornerRadius: 10).fill(.quaternary.opacity(0.5)))
            .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(.separator, lineWidth: 0.5))

            Toggle("记住账号和密码", isOn: $remember)
                .font(.callout)
                .padding(.top, 12)
                .padding(.leading, 2)

            Spacer(minLength: 16)

            VStack(spacing: 10) {
                if !status.isEmpty {
                    statusBanner
                }
                Button(action: startLogin) {
                    HStack(spacing: 8) {
                        if busy {
                            ProgressView().controlSize(.small)
                        } else {
                            Image(systemName: "play.fill")
                        }
                        Text(busy ? "正在启动…" : "登录游戏")
                    }
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                }
                .controlSize(.extraLarge)
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(busy)
            }
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 20)
    }

    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                Text(selection == nil ? "新账号" : account)
                    .font(.title2.weight(.semibold))
                    .lineLimit(1)
                    .truncationMode(.middle)
                Label("\(store.settings.host):\(String(store.settings.port))", systemImage: "server.rack")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button { showSettings = true } label: {
                Image(systemName: "gearshape")
                    .font(.title3)
            }
            .buttonStyle(.borderless)
            .help("设置")
        }
    }

    private var statusBanner: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Image(systemName: statusIsError ? "exclamationmark.triangle.fill" : (busy ? "hourglass" : "checkmark.circle.fill"))
                .foregroundStyle(statusIsError ? Color.red : (busy ? Color.secondary : Color.green))
            Text(status)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .font(.callout)
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(RoundedRectangle(cornerRadius: 8)
            .fill((statusIsError ? Color.red : Color.secondary).opacity(0.1)))
    }

    private func field<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.callout.weight(.medium))
                .foregroundStyle(.secondary)
            content()
        }
    }

    private func select(_ name: String) {
        guard let item = store.accounts.first(where: { $0.account == name }) else { return }
        selection = name
        account = item.account
        password = item.password
        status = ""
    }

    private func delete(_ name: String) {
        store.remove(name)
        if account == name { account = ""; password = "" }
        selection = nil
    }

    private func startLogin() {
        guard !busy else { return }
        let name = account.trimmingCharacters(in: .whitespaces)
        let pass = password
        do {
            try GameLauncher.validate(account: name, password: pass)
        } catch {
            status = error.localizedDescription; statusIsError = true
            return
        }
        busy = true
        status = "正在解析服务器地址…"; statusIsError = false
        let settings = store.settings
        Task {
            do {
                let ip = try await GameLauncher.launch(settings: settings, account: name, password: pass)
                if remember {
                    store.upsert(account: name, password: pass, touch: true)
                    selection = name
                }
                status = "已启动客户端（服务器 \(ip)），稍候会自动登录 \(name)。"
                statusIsError = false
                if let saveError = store.saveError {
                    status += "\n" + saveError
                    statusIsError = true
                }
            } catch {
                status = error.localizedDescription
                statusIsError = true
            }
            busy = false
        }
    }
}

/// 自绘输入框：边框里可以放图标等附加控件。
struct InputBox<Content: View>: View {
    var focused: Bool
    @ViewBuilder var content: Content

    var body: some View {
        HStack(spacing: 6) {
            content
        }
        .textFieldStyle(.plain)
        .font(.body)
        .padding(.horizontal, 10)
        .frame(height: 30)
        .background(RoundedRectangle(cornerRadius: 7).fill(Color(nsColor: .textBackgroundColor)))
        .overlay(
            RoundedRectangle(cornerRadius: 7)
                .strokeBorder(focused ? Color.accentColor : Color(nsColor: .separatorColor),
                              lineWidth: focused ? 2 : 1)
        )
        .animation(.easeOut(duration: 0.12), value: focused)
    }
}

struct SettingsView: View {
    @Binding var settings: LauncherSettings
    var onDone: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("设置").font(.title3.weight(.semibold))
            Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 10) {
                GridRow {
                    Text("客户端目录").gridColumnAlignment(.trailing)
                    HStack {
                        TextField("MhXunxian 文件夹", text: $settings.clientDir)
                        Button("选择…", action: chooseDir)
                    }
                }
                GridRow {
                    Text("")
                    Text(GameLauncher.isClientDir(settings.clientDir) ? "✓ 已找到 release/tty3d.exe" : "✗ 这个目录里没有 release/tty3d.exe")
                        .font(.caption)
                        .foregroundStyle(GameLauncher.isClientDir(settings.clientDir) ? .green : .red)
                }
                GridRow {
                    Text("服务器")
                    HStack {
                        TextField("域名或 IP", text: $settings.host)
                        Text("端口")
                        TextField("", value: $settings.port, format: .number.grouping(.never))
                            .frame(width: 70)
                    }
                }
                GridRow {
                    Text("CrossOver 容器")
                    TextField("Bottle 名称", text: $settings.bottle)
                }
                GridRow {
                    Text("wine 路径")
                    TextField("", text: $settings.winePath)
                }
            }
            .textFieldStyle(.roundedBorder)
            HStack {
                Spacer()
                Button("完成", action: onDone).keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 560)
    }

    private func chooseDir() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.prompt = "选择"
        panel.message = "选择包含 release/tty3d.exe 的 MhXunxian 文件夹"
        if panel.runModal() == .OK, let url = panel.url {
            settings.clientDir = url.path
        }
    }
}

#if !LAUNCHER_TEST
@main
struct XunxianLauncherApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate

    var body: some Scene {
        WindowGroup("寻仙登录器") {
            ContentView()
        }
        .windowResizability(.contentSize)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        // macOS 26 会给只有 .icns 的 App 套灰框，这里直接用 PNG 设置程序坞图标
        if let url = Bundle.main.url(forResource: "AppIcon", withExtension: "png"),
           let image = NSImage(contentsOf: url) {
            NSApp.applicationIconImage = image
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}
#endif
