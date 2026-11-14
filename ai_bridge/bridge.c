/*
 * 寻仙 AI 桥：替身 gcassist 插件，把客户端收到的每个服务器数据包实时发给本机。
 *
 * 原理（见 doc/游戏实时事件桥.md）：
 *   CTTY3DGameMng::Receive (0x460b50) 处理完每个包后，都会调用录像器的
 *   AddMsg(包指针, 长度)（0x4a2d30）。不录像时它直接返回。本 DLL 在这个函数开头装一个跳转钩子，
 *   把包复制进环形缓冲区，再由后台线程用 TCP 发到 127.0.0.1:47800（可用环境变量 XXBRIDGE_PORT 改）。
 *
 * 网络分发函数 0x56b250 按首字节分流：>= 0x80 的游戏包交给 Receive（走上面的 AddMsg 钩子），
 * < 0x80 的命令包（玩家数据、物品、经验等）交给另一个处理器，不经过 AddMsg。
 * 所以在分发函数的 < 0x80 分支（0x56b329）再装一个钩子，两类包都能收到，且互不重复。
 *
 * 插件导出用 .def 转发到改名后的原插件 gcassist_real.dll，游戏的插件逻辑不受影响。
 * 只适用于 SHA-256 59e50674… 这个版本的 tty3d.exe：装钩子前会核对函数开头的字节，
 * 不一致就放弃，只写日志。
 */

#include <winsock2.h>
#include <windows.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define HOOK_ADDR   0x004A2D30u   /* AddMsg：>= 0x80 的游戏包 */
#define HOOK_RESUME 0x004A2D36u
#define CMD_HOOK_ADDR   0x0056B329u   /* 分发函数的 < 0x80 分支：命令包 */
#define CMD_HOOK_RESUME 0x0056B32Fu
#define DEFAULT_PORT 47800
#define RING_SIZE   (4u * 1024u * 1024u)
#define MAX_PACKET  60000

static const uint8_t kExpected[6] = {0x56, 0x8b, 0xf1, 0x8b, 0x46, 0x08};
/* mov edx, [ebp-0x41c] */
static const uint8_t kCmdExpected[6] = {0x8b, 0x95, 0xe4, 0xfb, 0xff, 0xff};

static uint8_t *g_ring;
static volatile uint32_t g_head, g_tail;   /* 写入位置 / 读取位置（单调递增，取模使用） */
static CRITICAL_SECTION g_lock;
static HANDLE g_event;
static volatile uint32_t g_seq, g_dropped;
static FILE *g_log;

static void log_line(const char *fmt, ...)
{
    if (!g_log) return;
    va_list ap;
    SYSTEMTIME t;
    GetLocalTime(&t);
    fprintf(g_log, "%02d:%02d:%02d ", t.wHour, t.wMinute, t.wSecond);
    va_start(ap, fmt);
    vfprintf(g_log, fmt, ap);
    va_end(ap);
    fputc('\n', g_log);
    fflush(g_log);
}

static void ring_write(const void *src, uint32_t len)
{
    const uint8_t *s = (const uint8_t *)src;
    uint32_t pos = g_head % RING_SIZE;
    uint32_t first = RING_SIZE - pos < len ? RING_SIZE - pos : len;
    memcpy(g_ring + pos, s, first);
    memcpy(g_ring, s + first, len - first);
    g_head += len;
}

static void ring_read(void *dst, uint32_t len)
{
    uint8_t *d = (uint8_t *)dst;
    uint32_t pos = g_tail % RING_SIZE;
    uint32_t first = RING_SIZE - pos < len ? RING_SIZE - pos : len;
    memcpy(d, g_ring + pos, first);
    memcpy(d + first, g_ring, len - first);
    g_tail += len;
}

/* 钩子在游戏主线程里调用：只做复制，尽量快 */
void __cdecl on_packet(const uint8_t *packet, int size)
{
    uint32_t header[3];
    if (!g_ring || !packet || size <= 0 || size > MAX_PACKET) return;
    header[0] = (uint32_t)size;
    header[1] = ++g_seq;
    header[2] = GetTickCount();
    EnterCriticalSection(&g_lock);
    if (RING_SIZE - (g_head - g_tail) >= sizeof header + (uint32_t)size) {
        ring_write(header, sizeof header);
        ring_write(packet, (uint32_t)size);
    } else {
        g_dropped++;
    }
    LeaveCriticalSection(&g_lock);
    SetEvent(g_event);
}

/* 跳转钩子：保存寄存器，调用 on_packet(包, 长度)，执行被覆盖的 3 条指令，再跳回原函数 */
__asm__(
    ".intel_syntax noprefix\n"
    ".globl _hook_addmsg\n"
    "_hook_addmsg:\n"
    "  pushad\n"
    "  pushfd\n"
    "  mov eax, [esp+40]\n"          /* 36 字节寄存器 + 返回地址之后是第 1 个参数 */
    "  mov edx, [esp+44]\n"
    "  push edx\n"
    "  push eax\n"
    "  call _on_packet\n"
    "  add esp, 8\n"
    "  popfd\n"
    "  popad\n"
    "  push esi\n"                   /* 原指令：push esi / mov esi, ecx / mov eax, [esi+8] */
    "  mov esi, ecx\n"
    "  mov eax, [esi+8]\n"
    "  push 0x004A2D36\n"
    "  ret\n"
    ".att_syntax\n");
extern void hook_addmsg(void);

/* 命令包钩子：此时 [ebp-0x434] 是消息缓冲区（首字节为类型），[ebp-0x41c] 是长度 */
__asm__(
    ".intel_syntax noprefix\n"
    ".globl _hook_cmd\n"
    "_hook_cmd:\n"
    "  pushad\n"
    "  pushfd\n"
    "  mov eax, [ebp-0x434]\n"
    "  mov edx, [ebp-0x41c]\n"
    "  push edx\n"
    "  push eax\n"
    "  call _on_packet\n"
    "  add esp, 8\n"
    "  popfd\n"
    "  popad\n"
    "  mov edx, [ebp-0x41c]\n"     /* 被覆盖的原指令 */
    "  push 0x0056B32F\n"
    "  ret\n"
    ".att_syntax\n");
extern void hook_cmd(void);

static int send_all(SOCKET sock, const uint8_t *data, int len)
{
    while (len > 0) {
        int n = send(sock, (const char *)data, len, 0);
        if (n <= 0) return 0;
        data += n;
        len -= n;
    }
    return 1;
}

/* 用 TCP 发送：macOS 本机 UDP 单个数据报上限约 9KB，大包（如 25KB 的角色数据）会丢。
 * 没连上接收端时，包留在环形缓冲区里，满了才丢；每秒重试连接一次。 */
static DWORD WINAPI sender_thread(LPVOID unused)
{
    WSADATA wsa;
    SOCKET sock = INVALID_SOCKET;
    struct sockaddr_in addr;
    static uint8_t buf[16 + MAX_PACKET];
    const char *port_env = getenv("XXBRIDGE_PORT");
    int port = port_env ? atoi(port_env) : DEFAULT_PORT;
    uint32_t last_dropped = 0;
    DWORD last_try = 0;
    (void)unused;

    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        log_line("WSAStartup 失败");
        return 1;
    }
    memset(&addr, 0, sizeof addr);
    addr.sin_family = AF_INET;
    addr.sin_port = htons((u_short)port);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    log_line("发送线程启动，目标 127.0.0.1:%d（TCP）", port);

    for (;;) {
        WaitForSingleObject(g_event, 200);
        if (sock == INVALID_SOCKET) {
            if (GetTickCount() - last_try < 1000) continue;
            last_try = GetTickCount();
            sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
            if (connect(sock, (struct sockaddr *)&addr, sizeof addr) != 0) {
                closesocket(sock);
                sock = INVALID_SOCKET;
                continue;
            }
            log_line("已连上接收端");
        }
        for (;;) {
            uint32_t header[3];
            EnterCriticalSection(&g_lock);
            if (g_head == g_tail) {
                LeaveCriticalSection(&g_lock);
                break;
            }
            ring_read(header, sizeof header);
            ring_read(buf + 16, header[0]);
            LeaveCriticalSection(&g_lock);
            /* 帧格式：魔数 XXB1 | 序号 | 毫秒时间 | 长度 | 原始包 */
            memcpy(buf, "XXB1", 4);
            memcpy(buf + 4, &header[1], 4);
            memcpy(buf + 8, &header[2], 4);
            memcpy(buf + 12, &header[0], 4);
            if (!send_all(sock, buf, (int)(16 + header[0]))) {
                log_line("接收端断开，序号 %u 的包没发出去", header[1]);
                closesocket(sock);
                sock = INVALID_SOCKET;
                break;
            }
        }
        if (g_dropped != last_dropped) {
            last_dropped = g_dropped;
            log_line("缓冲区满，累计丢弃 %u 个包", last_dropped);
        }
    }
}

static void open_log(HMODULE self)
{
    char path[MAX_PATH];
    char *slash;
    GetModuleFileNameA(self, path, MAX_PATH);
    slash = strrchr(path, '\\');
    if (slash) strcpy(slash + 1, "ai_bridge.log");
    else strcpy(path, "ai_bridge.log");
    g_log = fopen(path, "a");
}

/* 在 addr 处写 6 字节：JMP rel32 + NOP。写之前核对原字节，版本不对就不改 */
static int install_hook(uint32_t addr, const uint8_t *expected, void (*stub)(void), const char *label)
{
    uint8_t *target = (uint8_t *)(uintptr_t)addr;
    DWORD old;
    int32_t rel;
    if (memcmp(target, expected, 6) != 0) {
        log_line("%s：0x%08X 处字节不符（%02X %02X %02X %02X %02X %02X），客户端版本不对，不装钩子",
                 label, addr, target[0], target[1], target[2], target[3], target[4], target[5]);
        return 0;
    }
    if (!VirtualProtect(target, 6, PAGE_EXECUTE_READWRITE, &old)) {
        log_line("%s：VirtualProtect 失败：%lu", label, GetLastError());
        return 0;
    }
    rel = (int32_t)((uint32_t)(uintptr_t)stub - (addr + 5));
    target[0] = 0xE9;
    memcpy(target + 1, &rel, 4);
    target[5] = 0x90;
    VirtualProtect(target, 6, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, 6);
    log_line("%s：钩子已安装在 0x%08X", label, addr);
    return 1;
}

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(inst);
        open_log(inst);
        log_line("ai_bridge 已加载");
        g_ring = (uint8_t *)VirtualAlloc(NULL, RING_SIZE, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
        InitializeCriticalSection(&g_lock);
        g_event = CreateEventA(NULL, FALSE, FALSE, NULL);
        if (g_ring && g_event) {
            int ok = install_hook(HOOK_ADDR, kExpected, hook_addmsg, "游戏包（AddMsg）");
            ok |= install_hook(CMD_HOOK_ADDR, kCmdExpected, hook_cmd, "命令包（分发 < 0x80）");
            if (ok) CloseHandle(CreateThread(NULL, 0, sender_thread, NULL, 0, NULL));
        }
    }
    return TRUE;
}
