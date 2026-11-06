#pragma GCC diagnostic ignored "-Wmisleading-indentation"
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

typedef struct {void *actor; int skill; u32 tick; int armed;} Pending;
typedef struct {void *summon; int method; int amount; u32 tick; int active;} CopyHit;
static Pending pending[64]; static CopyHit copies[256];
__attribute__((visibility("default"))) volatile u32 gmtool_sync80_counters[16];
__attribute__((visibility("default"))) volatile u32 gmtool_sync80_reject_log[129];
static int skill_for_method(int id) {
 switch(id) {
 case 30004:case 30153:case 30006:case 30154:case 30391:case 30392:case 30393:case 30155:return 3002;
 case 30007:case 30156:case 30060:case 30028:case 30157:return 3003;
 case 30064:case 30158:case 30066:case 30159:case 30160:return 3004;
 case 30067:return 3021;
 case 30191:case 30195:case 30196:case 30197:return 3071;
 case 30220:case 30223:case 30235:case 30218:case 30240:case 30241:case 30222:case 30242:case 30224:return 3083;
 default:return 0; }
}
static int npc_skill_for(int skill) {switch(skill){case 3002:return 60000;case 3021:return 60001;case 3003:return 60002;case 3004:return 60003;case 3083:return 60004;case 3071:return 60005;default:return 0;}}
static u32 nowtick(void){u32 (*get)(void)=(void*)(image_base+0xae572a);return get();}
static Pending *pending_for(void *actor) {for(int i=0;i<64;i++)if(pending[i].actor==actor)return &pending[i];for(int i=0;i<64;i++)if(!pending[i].armed || nowtick()-pending[i].tick>250){pending[i].actor=actor;pending[i].armed=0;return &pending[i];}return 0;}
static int method_number(void *method){if(!method)return 0;u32 *data=*(u32 **)((u8*)method+0x34);return data?(int)data[0]:0;}
static void start_copies(void *owner,int skill,int result) {
 u8 *base=(u8*)image_base;u8 *mgr=*(u8 **)((u8*)owner+0x68);if(!mgr)return;void *scene=*(void**)(mgr+8);if(!scene)return;
 void *(*get_char)(void*,int)=(void*)(base+0xb4e9c0);void *(*get_target)(void*)=(void*)(base+0xae9c8c);int(*get_target_id)(void*)=(void*)(base+0xae6558);
 int(*current)(void*)=(void*)(base+0xae9b10);u8(*break_skill)(void*,int)=(void*)(base+0x9288b4);int(*set_task)(void*,int,int,int,int,void*)=(void*)(base+0x91bd10);
 int npc=npc_skill_for(skill);int method=npc==60000?990001:990010+(npc-60000);u8 *head=mgr+12,*node=*(u8**)head;u32 tick=nowtick();
 for(int i=0;node&&node!=head&&i<256;i++) {u8 *next=*(u8**)node;int *e=*(int**)(node+8);int t=e?e[0]:0;
  if(t==20||t==21||t==22||t==349||t==350||t==351||t==355||t==361){void *summon=get_char(scene,e[1]);
   if(summon&&get_target(summon)) {int target=get_target_id(summon);if(target>0){int cur=current(summon);if(cur==11000||cur==11003||cur==11004||cur==11006||cur==11007||(cur>=7265&&cur<=7277))break_skill(summon,1);
    CopyHit *slot=0;for(int j=0;j<256;j++)if(copies[j].summon==summon){slot=&copies[j];break;}if(!slot)for(int j=0;j<256;j++)if(!copies[j].active||tick-copies[j].tick>250){slot=&copies[j];break;}
    if(slot){slot->summon=summon;slot->method=method;int pct=(t==20||t==349)?80:(t==21||t==350||t==361)?50:30;slot->amount=result/100*pct+(result%100)*pct/100;slot->tick=tick;slot->active=1;
     if(set_task(summon,2,npc,target,0,0))++gmtool_sync80_counters[2];else{slot->active=0;++gmtool_sync80_counters[3];
      u32 seq=gmtool_sync80_reject_log[0]++;u32 at=1+(seq%16)*8;
      void *current_method=*(void **)((u8 *)summon+0x50);
      gmtool_sync80_reject_log[at]=tick;gmtool_sync80_reject_log[at+1]=(u32)t;
      gmtool_sync80_reject_log[at+2]=(u32)e[1];gmtool_sync80_reject_log[at+3]=(u32)cur;
      gmtool_sync80_reject_log[at+4]=(u32)method_number(current_method);
      gmtool_sync80_reject_log[at+5]=(u32)npc;gmtool_sync80_reject_log[at+6]=(u32)target;
      gmtool_sync80_reject_log[at+7]=(u32)skill;
     }}
   }} }
  node=next;
 }
}
static int copied_amount(void *attacker,void *method) {
 int id=method_number(method);if(id<990001||id>990015)return -1;u32 tick=nowtick();
 for(int i=0;i<256;i++){CopyHit *c=&copies[i];if(c->active&&c->summon==attacker&&c->method==id&&tick-c->tick<=250){c->active=0;++gmtool_sync80_counters[4];return c->amount;}}return -1;
}
static int damage_hook(void *target, int damage, void *attacker, void *method, int flags) {
    u8 *base = (u8 *)image_base;
    int (*original_damage)(void *, int, void *, void *, int) =
        (int (*)(void *, int, void *, void *, int))(base + 0x9242e2);
    int outgoing = attacker ? summon_count_for_weapon(attacker) : 0;
    int incoming = target ? summon_count_for_weapon(target) : 0;
    if (outgoing > 0) { ++gmtool_summon_damage_counters[0]; gmtool_summon_damage_counters[2] = (u32)outgoing; }
    if (incoming > 0) { ++gmtool_summon_damage_counters[1]; gmtool_summon_damage_counters[3] = (u32)incoming; }
    int copy = attacker ? copied_amount(attacker, method) : -1;
    int adjusted = adjust_damage(copy >= 0 ? copy : damage, outgoing, incoming);
    if (adjusted > 0 && sign_summon_protected(target)) {
        ++gmtool_summon_damage_counters[20];
        adjusted /= 10;
    }
    int result = original_damage(target, adjusted, attacker, method, flags);
    if (attacker && result > 0 && outgoing > 0) {
        int skill = skill_for_method(method_number(method));
        if (skill) {Pending *p = pending_for(attacker);u32 tick=nowtick();
            if(p && p->armed && p->skill==skill && tick-p->tick<=250) {
                p->armed=0;++gmtool_sync80_counters[1];gmtool_sync80_counters[5]=(u32)result;start_copies(attacker,skill,result);
            }
        }
    }

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

static void mirror_deferred(void *caster,int skill) {
 void (*original_first)(void*,int)=(void*)(image_base+0x91d7d0);original_first(caster,skill);
 if(!caster||!npc_skill_for(skill)||summon_count_for_weapon(caster)<=0)return;
 Pending *p=pending_for(caster);if(!p)return;p->actor=caster;p->skill=skill;p->tick=nowtick();p->armed=1;++gmtool_sync80_counters[0];
}
__attribute__((visibility("default"))) int gmtool_mirror_hook(u32 base,int install) {
 if(!base||(install!=0&&install!=1))return -1;u8 *site=(u8*)(base+0x928c3b);u32 target=install?base+0x6018d0:(u32)(void*)&mirror_deferred;
 u32 rel=target-(u32)(site+5);if(site[0]!=0xe8)return -2;for(u32 j=0;j<4;j++)if(site[j+1]!=(u8)(rel>>(j*8)))return -2;
 if(protect((void*)((base+0x928c3b)&~4095U),4096,7))return -3;image_base=base;target=install?(u32)(void*)&mirror_deferred:base+0x6018d0;rel=target-(u32)(site+5);site[0]=0xe8;for(u32 j=0;j<4;j++)site[j+1]=(u8)(rel>>(j*8));if(protect((void*)((base+0x928c3b)&~4095U),4096,5))return -4;return 0;
}
