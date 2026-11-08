/* Tongshen (3054) enables targeting for the sign summons already present. */
typedef unsigned char u8;
typedef unsigned int u32;
static u32 image_base;
struct sign_slot { void *actor; void *owner; int id; u32 ticks; };
static struct sign_slot slots[512];
__attribute__((visibility("default"))) volatile u32 gmtool_sign_search_counters[16];

static int is_sign(int type) {
    return (type >= 20 && type <= 22) || (type >= 349 && type <= 351);
}

static int active_in_list(void *owner, void *actor, int id) {
    u8 *manager = *(u8 **)((u8 *)owner + 0x68);
    if (!manager) return 0;
    void *scene = *(void **)(manager + 8);
    if (!scene) return 0;
    u8 *head = manager + 12;
    u8 *node = *(u8 **)head;
    void *(*get_char)(void *, int) = (void *)((u8 *)image_base + 0xb4e9c0);
    for (int i = 0; node && node != head && i < 256; ++i) {
        int *entry = *(int **)(node + 8);
        if (entry && is_sign(entry[0]) && entry[1] == id && get_char(scene, id) == actor)
            return 1;
        node = *(u8 **)node;
    }
    return 0;
}

static void enable_current(void *owner) {
    if (!owner) return;
    u8 *base = (u8 *)image_base;
    void *(*data)(void *) = (void *)(base + 0xae64ec);
    int (*player)(void *) = (void *)(base + 0xb2d42e);
    void *owner_data = data(owner);
    if (!owner_data || !player(owner_data)) return;
    u8 *manager = *(u8 **)((u8 *)owner + 0x68);
    if (!manager) return;
    void *scene = *(void **)(manager + 8);
    if (!scene) return;
    u8 *head = manager + 12;
    u8 *node = *(u8 **)head;
    void *(*get_char)(void *, int) = (void *)(base + 0xb4e9c0);
    int (*dead)(void *) = (void *)(base + 0x71c4b2);
    for (int i = 0; node && node != head && i < 256; ++i) {
        int *entry = *(int **)(node + 8);
        if (entry && is_sign(entry[0])) {
            void *actor = get_char(scene, entry[1]);
            if (actor && !dead(actor)) {
                int slot = -1;
                for (int j = 0; j < 512; ++j) {
                    if (slots[j].actor == actor && slots[j].owner == owner &&
                        slots[j].id == entry[1]) { slot = j; break; }
                    if (slot < 0 && !slots[j].actor) slot = j;
                }
                if (slot >= 0) {
                    slots[slot] = (struct sign_slot){actor, owner, entry[1], 0};
                    ++gmtool_sign_search_counters[9];
                }
            }
        }
        node = *(u8 **)node;
    }
}

__attribute__((used,noinline)) static int trait_helper(void *manager, int skill,
                                                          int trait, void *caster) {
    int (*original)(void *, int, int) = (void *)((u8 *)image_base + 0xb27b90);
    int result = original(manager, skill, trait);
    if (result && skill == 3054) {
        ++gmtool_sign_search_counters[10];
        enable_current(caster);
    }
    return result;
}

/* At this call site the caller's EBP points to ProcessFirstSkillMethod's frame. */
__attribute__((naked)) static int trait_dispatch(void) {
    __asm__ volatile("pushl %%ebp\n\tmovl %%esp, %%ebp\n\t"
                     "movl (%%ebp), %%eax\n\tmovl 8(%%eax), %%eax\n\t"
                     "pushl %%eax\n\tpushl 16(%%ebp)\n\tpushl 12(%%ebp)\n\t"
                     "pushl 8(%%ebp)\n\tcall trait_helper\n\taddl $16, %%esp\n\t"
                     "popl %%ebp\n\tret" ::: "eax", "memory");
}

static void search_logic(void *actor) {
    u8 *base = (u8 *)image_base;
    void (*original)(void *) = (void *)(base + 0x931dc4);
    original(actor);
    if (!actor) return;
    void *(*data)(void *) = (void *)(base + 0xae64ec);
    int (*npc)(void *) = (void *)(base + 0xb2d444);
    void *npc_data = data(actor);
    if (!npc_data || !npc(npc_data)) return;
    u8 *extra = *(u8 **)((u8 *)npc_data + 0xb0);
    if (!extra || !is_sign(*(int *)(extra + 4))) return;
    void *owner = *(void **)(extra + 8);
    if (!owner) return;
    for (int i = 0; i < 512; ++i) {
        struct sign_slot *slot = &slots[i];
        if (slot->actor != actor || slot->owner != owner) continue;
        int (*dead)(void *) = (void *)(base + 0x71c4b2);
        if (dead(actor) || !active_in_list(slot->owner, actor, slot->id)) {
            slot->actor = 0;
            ++gmtool_sign_search_counters[11];
            continue;
        }
        if (++slot->ticks % 200) return;
        ++gmtool_sign_search_counters[0];
        void (*search)(void *) = (void *)(base + 0x94ab0a);
        search(actor);
        return;
    }
}

/* SearchEnemy reads this at both its area search and candidate filter sites. */
static int search_distance(void *actor) {
    u8 *base = (u8 *)image_base;
    int (*original)(void *) = (void *)(base + 0x93f170);
    int value = original(actor);
    if (!actor) return value;
    for (int i = 0; i < 512; ++i) {
        struct sign_slot *slot = &slots[i];
        if (slot->actor != actor) continue;
        int (*dead)(void *) = (void *)(base + 0x71c4b2);
        if (dead(actor) || !active_in_list(slot->owner, actor, slot->id)) {
            slot->actor = 0;
            return value;
        }
        ++gmtool_sign_search_counters[12];
        return 15;
    }
    return value;
}

static int protect(void *p, u32 n, int f) {
    int r;
    __asm__ volatile("pushl %%ebx\n\tmovl %1, %%ebx\n\tint $0x80\n\tpopl %%ebx"
                     : "=a"(r) : "r"(p), "a"(125), "c"(n), "d"(f) : "memory", "cc");
    return r;
}
static int patch(u32 base, int install, u32 off, u32 old, u32 hook) {
    u8 *site = (u8 *)(base + off);
    old += base;
    u32 rel = (install ? old : hook) - (u32)(site + 5);
    if (site[0] != 0xe8) return -2;
    for (u32 i = 0; i < 4; ++i) if (site[i+1] != (u8)(rel >> (8*i))) return -2;
    if (protect((void *)((u32)site & ~4095U), 4096, 7)) return -3;
    image_base = base;
    rel = (install ? hook : old) - (u32)(site + 5);
    for (u32 i = 0; i < 4; ++i) site[i+1] = (u8)(rel >> (8*i));
    if (protect((void *)((u32)site & ~4095U), 4096, 5)) return -4;
    return 0;
}
__attribute__((visibility("default"))) int gmtool_sign_search_hook(u32 base, int install) {
    if (!base || (install != 0 && install != 1)) return -1;
    return patch(base, install, 0x950504, 0x651f50, (u32)(void *)&search_logic);
}
__attribute__((visibility("default"))) int gmtool_sign_activation_hook(u32 base, int install) {
    if (!base || (install != 0 && install != 1)) return -1;
    return patch(base, install, 0x91d7fa, 0x62b110, (u32)(void *)&trait_dispatch);
}
__attribute__((visibility("default"))) int gmtool_sign_distance_hook(u32 base, int install) {
    if (!base || (install != 0 && install != 1)) return -1;
    u32 offsets[2] = {0x94acff, 0x94afe2};
    u8 *sites[2] = {(u8 *)(base + offsets[0]), (u8 *)(base + offsets[1])};
    u32 old = base + 0x619000;
    u32 hook = (u32)(void *)&search_distance;
    for (int j = 0; j < 2; ++j) {
        u32 rel = (install ? old : hook) - (u32)(sites[j] + 5);
        if (sites[j][0] != 0xe8) return -2;
        for (u32 i = 0; i < 4; ++i)
            if (sites[j][i+1] != (u8)(rel >> (8*i))) return -2;
    }
    if (protect((void *)((u32)sites[0] & ~4095U), 4096, 7)) return -3;
    image_base = base;
    for (int j = 0; j < 2; ++j) {
        u32 rel = (install ? hook : old) - (u32)(sites[j] + 5);
        for (u32 i = 0; i < 4; ++i) sites[j][i+1] = (u8)(rel >> (8*i));
    }
    if (protect((void *)((u32)sites[0] & ~4095U), 4096, 5)) return -4;
    return 0;
}
