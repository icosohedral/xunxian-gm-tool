/* Test-only, version-pinned summon effect; hook is installed in process memory. */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
static u32 image_base;
static int in_progress;
__attribute__((visibility("default"))) volatile u32 gmtool_three_sign_counters[16];
static const u32 site_offset = 0xabc48d;
static const u8 original_bytes[5] = {0xe8, 0x8e, 0x2f, 0xb4, 0xff};
static int protect(void *page, u32 length, int flags) {
    int result;
    __asm__ volatile("pushl %%ebx\n\tmovl %1, %%ebx\n\tint $0x80\n\tpopl %%ebx"
                     : "=a"(result) : "r"(page), "a"(125), "c"(length), "d"(flags)
                     : "memory", "cc");
    return result;
}
static int sign_kind(int type) {
    if (type == 20 || type == 349) return 0;
    if (type == 21 || type == 350) return 1;
    if (type == 22 || type == 351) return 2;
    return -1;
}
static int source_level(int kind, int role) {
    static const int soldier[] = {1,10,20,30,40,50,60,68,74,81};
    static const int general[] = {25,35,45,55,63,70,77,84,91,95};
    static const int king[] = {56,64,71,78,85,92};
    if (kind == 0 && role >= 2430 && role < 2440) return soldier[role - 2430];
    if (kind == 1 && role >= 2451 && role < 2461) return general[role - 2451];
    if (kind == 2 && role >= 2471 && role < 2477) return king[role - 2471];
    return 0;
}
static int role_at_level(int kind, int level) {
    static const int soldier[] = {1,10,20,30,40,50,60,68,74,81};
    static const int general[] = {25,35,45,55,63,70,77,84,91,95};
    static const int king[] = {56,64,71,78,85,92};
    const int *levels = kind == 0 ? soldier : kind == 1 ? general : king;
    int count = kind == 0 ? 10 : kind == 1 ? 10 : 6;
    int start = kind == 0 ? 2430 : kind == 1 ? 2451 : 2471;
    int chosen = 0;
    for (int i = 1; i < count && levels[i] <= level; ++i) chosen = i;
    return start + chosen;
}
static int has_weapon(void *actor) {
    u8 *base = (u8 *)image_base;
    void *(*get_data)(void *) = (void *(*)(void *))(base + 0xae64ec);
    int (*is_player)(void *) = (int (*)(void *))(base + 0xb2d42e);
    void *(*get_items)(void *) = (void *(*)(void *))(base + 0xb44776);
    void *(*get_item)(void *, int) = (void *(*)(void *, int))(base + 0x68b3f6);
    void *data = actor ? get_data(actor) : (void *)0;
    if (!data || !is_player(data)) return 0;
    void *items = get_items(data);
    u8 *weapon = items ? (u8 *)get_item(items, 1810) : (u8 *)0;
    return weapon && *(u16 *)(weapon + 2) == 60075;
}
static int triple_trigger(void *method, const void *trigger) {
    u8 *base = (u8 *)image_base;
    int (*engine_trigger)(void *, const void *) = (int (*)(void *, const void *))(base + 0xab938a);
    if (!trigger || in_progress || *(const u16 *)((const u8 *)trigger + 6) != 20014)
        return engine_trigger(method, trigger);
    const u32 *event = (const u32 *)trigger;
    int type = (int)event[3], role = (int)event[4], kind = sign_kind(type);
    int level = kind < 0 ? 0 : source_level(kind, role);
    if (!level) return engine_trigger(method, trigger);
    void *(*get_source)(void *) = (void *(*)(void *))(base + 0xb1b2c0);
    void *actor = get_source(method);
    if (!has_weapon(actor)) return engine_trigger(method, trigger);
    ++gmtool_three_sign_counters[0];
    int (*get_count)(void *, int) = (int (*)(void *, int))(base + 0x91aa7e);
    int (*get_summon_id)(void *, int) = (int (*)(void *, int))(base + 0xbbc44a);
    void *manager = *(void **)((u8 *)actor + 0x68);
    int previous = manager ? get_summon_id(manager, type) : 0;
    in_progress = 1;
    int result = engine_trigger(method, trigger);
    manager = *(void **)((u8 *)actor + 0x68);
    int current = manager ? get_summon_id(manager, type) : 0;
    if (current && current != previous) {
        ++gmtool_three_sign_counters[1];
        int legacy = type >= 20 && type <= 22;
        int other_family_type = legacy ? 349 + kind : 20 + kind;
        if (get_count(actor, other_family_type) > 0) {
            void (*delete_summon)(void *, int, int, int) =
                (void (*)(void *, int, int, int))(base + 0x923216);
            delete_summon(actor, other_family_type, -1, 2);
            ++gmtool_three_sign_counters[5];
        }
        static const char *labels[] = {"dq", "jq", "lhq"};
        for (int k = 0; k < 3; ++k) {
            if (k == kind) continue;
            if (get_count(actor, 20 + k) > 0 || get_count(actor, 349 + k) > 0) {
                ++gmtool_three_sign_counters[3];
                continue;
            }
            u32 copy[11];
            for (u32 i = 0; i < 11; ++i) copy[i] = event[i];
            int wanted_type = legacy ? 20 + k : 349 + k;
            copy[3] = (u32)wanted_type;
            copy[4] = (u32)role_at_level(k, level);
            copy[10] = (u32)(const void *)labels[k];
            (void)engine_trigger(method, copy);
            if (get_count(actor, wanted_type) > 0) ++gmtool_three_sign_counters[2];
            else ++gmtool_three_sign_counters[4];
        }
    } else ++gmtool_three_sign_counters[6];
    in_progress = 0;
    return result;
}
__attribute__((visibility("default"))) int gmtool_three_sign_hook(u32 base, int install) {
    u8 patch[5];
    if (!base || (install != 0 && install != 1)) return -1;
    u8 *site = (u8 *)(base + site_offset);
    u32 relative = (u32)(void *)&triple_trigger - (u32)(site + 5);
    patch[0] = 0xe8;
    for (u32 i = 0; i < 4; ++i) patch[i+1] = (u8)(relative >> (i*8));
    for (u32 i = 0; i < 5; ++i) if (site[i] != (install ? original_bytes[i] : patch[i])) return -2;
    if (protect((void *)((base + site_offset) & ~4095U),4096,7)) return -3;
    image_base = base;
    for (u32 i = 0; i < 5; ++i) site[i] = install ? patch[i] : original_bytes[i];
    if (protect((void *)((base + site_offset) & ~4095U),4096,5)) return -4;
    return 0;
}
