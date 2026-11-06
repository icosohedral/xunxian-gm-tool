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

__attribute__((visibility("default"))) volatile u32 gmtool_summon_damage_counters[32];
static int summon_count_for_weapon(void *actor) {
    u8 *base = (u8 *)image_base;
    void *(*get_data)(void *) = (void *(*)(void *))(base + 0xae64ec);
    int (*is_player)(void *) = (int (*)(void *))(base + 0xb2d42e);
    void *(*get_items)(void *) = (void *(*)(void *))(base + 0xb44776);
    void *(*get_item)(void *, int) = (void *(*)(void *, int))(base + 0x68b3f6);
    void *data = actor ? get_data(actor) : 0;
    if (!data || !is_player(data)) return 0;
    void *items = get_items(data);
    u8 *weapon = items ? (u8 *)get_item(items, 1810) : 0;
    if (!weapon || *(u16 *)(weapon + 2) != 60075) return 0;
    u8 *manager = *(u8 **)((u8 *)actor + 0x68);
    if (!manager || !*(void **)(manager + 8)) return 0;
    void *(*get_char)(void *, int) = (void *(*)(void *, int))(base + 0xb4e9c0);
    void *scene = *(void **)(manager + 8);
    u8 *head = manager + 12, *node = *(u8 **)head;
    int count = 0;
    for (int j = 4; j < 20; ++j) gmtool_summon_damage_counters[j] = 0;
    for (int i = 0; node && node != head && i < 256; ++i) {
        int *entry = *(int **)(node + 8);
        void *summon = entry ? get_char(scene, entry[1]) : 0;
        int type = entry ? entry[0] : 0;
        int is_sign = type == 20 || type == 21 || type == 22 ||
                      type == 349 || type == 350 || type == 351 ||
                      type == 355 || type == 361;
        if (is_sign && summon && !*((u8 *)summon + 8)) {
            if (count < 8) {
                gmtool_summon_damage_counters[4 + count] = (u32)entry[0];
                gmtool_summon_damage_counters[12 + count] = (u32)entry[1];
            }
            ++count;
        }
        node = *(u8 **)node;
    }
    return count;
}
static int sign_summon_protected(void *target) {
    u8 *base = (u8 *)image_base;
    void *(*get_data)(void *) = (void *(*)(void *))(base + 0xae64ec);
    int (*is_npc)(void *) = (int (*)(void *))(base + 0xb2d444);
    u8 *data = target ? (u8 *)get_data(target) : 0;
    if (!data || !is_npc(data)) return 0;
    u8 *extra = *(u8 **)(data + 0xb0);
    if (!extra) return 0;
    int type = *(int *)(extra + 4);
    void *owner = *(void **)(extra + 8);
    return type >= 20 && type <= 22 && owner && summon_count_for_weapon(owner) > 0;
}
static int adjust_damage(int damage, int outgoing, int incoming) {
    if (damage <= 0) return damage;
    if (outgoing > 0) {
        u64 bonus = (u64)(damage / 10) * (u32)(3 * outgoing) +
                    (u32)((damage % 10) * (3 * outgoing) / 10);
        u64 adjusted = (u64)(u32)damage + bonus;
        damage = adjusted > 2147483647ULL ? 2147483647 : (int)adjusted;
    }
    if (incoming > 0) {
        int reduction = incoming >= 4 ? 100 : incoming * 30;
        int remaining = 100 - reduction;
        damage = damage / 100 * remaining + damage % 100 * remaining / 100;
    }
    return damage;
}
static int damage_hook(void *target, int damage, void *attacker, void *method, int flags) {
    u8 *base = (u8 *)image_base;
    int (*original_damage)(void *, int, void *, void *, int) =
        (int (*)(void *, int, void *, void *, int))(base + 0x9242e2);
    int outgoing = attacker ? summon_count_for_weapon(attacker) : 0;
    int incoming = target ? summon_count_for_weapon(target) : 0;
    if (outgoing > 0) { ++gmtool_summon_damage_counters[0]; gmtool_summon_damage_counters[2] = (u32)outgoing; }
    if (incoming > 0) { ++gmtool_summon_damage_counters[1]; gmtool_summon_damage_counters[3] = (u32)incoming; }
    int adjusted = adjust_damage(damage, outgoing, incoming);
    if (adjusted > 0 && sign_summon_protected(target)) {
        ++gmtool_summon_damage_counters[20];
        adjusted /= 10;
    }
    int result = original_damage(target, adjusted, attacker, method, flags);
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
                /* Temporary test: weapon 60074 restores 5% of DealDamage's
                 * positive return value, rounded down to a whole HP point. */
                if (*(u16 *)(weapon + 2) == 60074 && result >= 20) {
                    void (*change_hp)(void *, int) =
                        (void (*)(void *, int))(base + 0x923b24);
                    change_hp(attacker, result / 20);
                }
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
