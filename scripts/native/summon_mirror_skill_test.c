/* Test-only hook for the verified i386 TTYGZS_d.so. */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
static u32 image_base;
/* Per-process diagnostic counters, readable without altering gameplay. */
__attribute__((visibility("default"))) volatile u32 gmtool_mirror_counters[32];
static const u32 site_offset = 0x928c3b;
static const u8 original[5] = {0xe8,0x90,0x8c,0xcd,0xff};
static int protect(void *page, u32 length, int flags) {
    int result;
    __asm__ volatile("pushl %%ebx\n\tmovl %1, %%ebx\n\tint $0x80\n\tpopl %%ebx"
                     : "=a"(result) : "r"(page), "a"(125), "c"(length), "d"(flags)
                     : "memory", "cc");
    return result;
}
static int is_sign_type(int type) {
    return type == 20 || type == 21 || type == 22 || type == 349 ||
           type == 350 || type == 351 || type == 355 || type == 361;
}
static void mirror_skill(void *caster, int skill_id) {
    u8 *base = (u8 *)image_base;
    void (*original_first)(void *, int) = (void (*)(void *, int))(base + 0x91d7d0);
    original_first(caster, skill_id);
    ++gmtool_mirror_counters[0];
    if (skill_id == 60000) ++gmtool_mirror_counters[12];
    int npc_skill = skill_id == 3002 ? 60000 : skill_id == 3021 ? 60001 :
        skill_id == 3003 ? 60002 : skill_id == 3004 ? 60003 :
        skill_id == 3083 ? 60004 : skill_id == 3071 ? 60005 : 0;
    if (!caster || !npc_skill) return;
    ++gmtool_mirror_counters[1];
    void *(*get_data)(void *) = (void *(*)(void *))(base + 0xae64ec);
    int (*is_player)(void *) = (int (*)(void *))(base + 0xb2d42e);
    void *(*get_items)(void *) = (void *(*)(void *))(base + 0xb44776);
    void *(*get_item)(void *, int) = (void *(*)(void *, int))(base + 0x68b3f6);
    void *data = get_data(caster);
    if (!data || !is_player(data)) return;
    ++gmtool_mirror_counters[2];
    void *items = get_items(data);
    u8 *weapon = items ? (u8 *)get_item(items, 1810) : 0;
    if (!weapon || *(u16 *)(weapon + 2) != 60075) return;
    ++gmtool_mirror_counters[3];
    u8 *manager = *(u8 **)((u8 *)caster + 0x68);
    if (!manager) return;
    ++gmtool_mirror_counters[4];
    void *scene = *(void **)(manager + 8);
    if (!scene) return;
    /* GCC i386 std::list<SSummon*>: sentinel at manager+12; payload at node+8. */
    u8 *head = manager + 12;
    u8 *node = *(u8 **)head;
    void *(*get_char)(void *, int) = (void *(*)(void *, int))(base + 0xb4e9c0);
    void *(*get_target)(void *) = (void *(*)(void *))(base + 0xae9c8c);
    int (*get_target_id)(void *) = (int (*)(void *))(base + 0xae6558);
    for (int i = 0; node && node != head && i < 256; ++i) {
        ++gmtool_mirror_counters[5];
        u8 *next = *(u8 **)node;
        int *entry = *(int **)(node + 8);
        if (entry) gmtool_mirror_counters[11] = (u32)entry[0];
        if (entry && is_sign_type(entry[0])) {
            ++gmtool_mirror_counters[6];
            void *summon = get_char(scene, entry[1]);
            if (summon) ++gmtool_mirror_counters[7];
            if (summon && get_target(summon)) {
                ++gmtool_mirror_counters[8];
                int target_id = get_target_id(summon);
                /* NPC skill 60000 is a one-stage test copy using the nine-star projectile.
                 * The base task setter bypasses the NPC skill-list restriction. */
                int (*set_task)(void *, int, int, int, int, void *) =
                    (int (*)(void *, int, int, int, int, void *))(base + 0x91bd10);
                if (target_id > 0) {
                    int (*get_current_skill)(void *) =
                        (int (*)(void *))(base + 0xae9b10);
                    int current_skill = get_current_skill(summon);
                    /* Only the five documented sign-summon basic attacks. */
                    if (current_skill == 11000 || current_skill == 11003 ||
                        current_skill == 11004 || current_skill == 11006 ||
                        current_skill == 11007) {
                        u8 (*break_skill)(void *, int) =
                            (u8 (*)(void *, int))(base + 0x9288b4);
                        ++gmtool_mirror_counters[13];
                        (void)break_skill(summon, 1);
                    }
                    if (set_task(summon, 2, npc_skill, target_id, 0, (void *)0))
                        ++gmtool_mirror_counters[9];
                    else ++gmtool_mirror_counters[10];
                }
            }
        }
        node = next;
    }
}
__attribute__((visibility("default"))) int gmtool_mirror_hook(u32 base, int install) {
    u8 patch[5];
    if (!base || (install != 0 && install != 1)) return -1;
    u8 *site = (u8 *)(base + site_offset);
    u32 relative = (u32)(void *)&mirror_skill - (u32)(site + 5);
    patch[0] = 0xe8;
    for (u32 i = 0; i < 4; ++i) patch[i + 1] = (u8)(relative >> (i * 8));
    for (u32 i = 0; i < 5; ++i)
        if (site[i] != (install ? original[i] : patch[i])) return -2;
    if (protect((void *)((base + site_offset) & ~4095U), 4096, 7)) return -3;
    image_base = base;
    for (u32 i = 0; i < 5; ++i) site[i] = install ? patch[i] : original[i];
    if (protect((void *)((base + site_offset) & ~4095U), 4096, 5)) return -4;
    return 0;
}
