/* Temporary test hook for the verified i386 TTYGZS_d.so build. Install only
 * while the game thread is stopped. This modifies process memory, not files. */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;

static u32 image_base;
static const u32 sites[2] = {0x926bf8, 0x926c2e};
static const u8 original[2][5] = {
    {0xe8, 0x63, 0xcf, 0xd2, 0xff},
    {0xe8, 0x2d, 0xcf, 0xd2, 0xff}
};

static int protect(void *page, u32 length, int flags) {
    int result;
    __asm__ volatile("pushl %%ebx\n\tmovl %1, %%ebx\n\tint $0x80\n\tpopl %%ebx"
                     : "=a"(result) : "r"(page), "a"(125), "c"(length), "d"(flags)
                     : "memory", "cc");
    return result;
}

static int damage_hook(void *target, int damage, void *attacker, void *method, int flags) {
    u8 *base = (u8 *)image_base;
    int (*original_damage)(void *, int, void *, void *, int) =
        (int (*)(void *, int, void *, void *, int))(base + 0x9242e2);
    int result = original_damage(target, damage, attacker, method, flags);
    if (target && attacker && result > 0) {
        void *(*get_data)(void *) = (void *(*)(void *))(base + 0xae64ec);
        int (*is_player)(void *) = (int (*)(void *))(base + 0xb2d42e);
        void *(*get_items)(void *) = (void *(*)(void *))(base + 0xb44776);
        void *(*get_item)(void *, int) = (void *(*)(void *, int))(base + 0x68b3f6);
        void *(*get_state)(void *) = (void *(*)(void *))(base + 0x8d0450);
        u64 (*get_id)(void *) = (u64 (*)(void *))(base + 0xb3efd2);
        void (*add_state)(void *, int, u64, int) =
            (void (*)(void *, int, u64, int))(base + 0x90ddf4);
        void *source_data = get_data(attacker);
        if (source_data && is_player(source_data)) {
            void *items = get_items(source_data);
            u8 *weapon = items ? (u8 *)get_item(items, 1810) : 0;
            if (weapon) {
                void *(*get_excel)(void *, int) = (void *(*)(void *, int))(base + 0xb06c64);
                int (*get_int)(void *, int, int) = (int (*)(void *, int, int))(base + 0xc5ca14);
                void *row = get_excel(items, *(u16 *)(weapon + 2));
                int slot, has_trait = 0;
                if (row) for (slot = 25; slot <= 29; ++slot)
                    if (get_int(row, slot, 1) == 65000) has_trait = 1;
                if (!has_trait) return result;
                void *target_data = get_data(target);
                void *state = target_data ? get_state(target_data) : 0;
                if (state) add_state(state, 16890, get_id(source_data), 0);
            }
        }
    }
    return result;
}

/* Install/remove return zero only when both call sites match expectations. */
__attribute__((visibility("default"))) int gmtool_weapon_hook(u32 base, int install) {
    u8 patch[2][5];
    u32 i, j;
    if (!base || (install != 0 && install != 1)) return -1;
    for (i = 0; i < 2; i++) {
        u8 *site = (u8 *)(base + sites[i]);
        u32 relative = (u32)(void *)&damage_hook - (u32)(site + 5);
        patch[i][0] = 0xe8;
        for (j = 0; j < 4; j++) patch[i][j + 1] = (u8)(relative >> (j * 8));
        for (j = 0; j < 5; j++)
            if (site[j] != (install ? original[i][j] : patch[i][j])) return -2;
    }
    if (protect((void *)((base + sites[0]) & ~4095U), 4096, 7)) return -3;
    image_base = base;
    for (i = 0; i < 2; i++) {
        u8 *site = (u8 *)(base + sites[i]);
        for (j = 0; j < 5; j++) site[j] = install ? patch[i][j] : original[i][j];
    }
    if (protect((void *)((base + sites[0]) & ~4095U), 4096, 5)) return -4;
    return 0;
}
