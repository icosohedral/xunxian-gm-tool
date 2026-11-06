/* Experimental: gameplay behavior and client display require validation.
 * For TTYGZS_d.so SHA-256 9a19e9c8deaefeb8ba35d61f10ccc6f32d166ef2ee2aedf1f3ecf06cfa7109bb.
 * Replaces only the SafeGetInt call at CFlyUnitS::Hit+0x47. */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
static u32 image_base;
__attribute__((visibility("default"))) volatile u32 gmtool_unclean_counters[4];

/* Original caller's frame holds its CFlyUnitS this pointer at ebp+8. */
__attribute__((noinline,used)) static int scatter_flag(void *row,int column,int opt,void *fly) {
    int (*getint)(void*,int,int)=(void*)(image_base+0xc5ca14);
    int original=getint(row,column,opt);
    if (!original || column!=11 || !fly) return original;
    int unit=getint(row,1,0);
    if (unit!=809 && unit!=1190 && unit!=6680) return original;
    void *(*src)(void*)=(void*)(image_base+0xb1b2c0);
    void *actor=src(fly);
    if (!actor) return original;
    void *(*data)(void*)=(void*)(image_base+0xae64ec);
    int (*player)(void*)=(void*)(image_base+0xb2d42e);
    void *(*items)(void*)=(void*)(image_base+0xb44776);
    void *(*item)(void*,int)=(void*)(image_base+0x68b3f6);
    void *d=data(actor); if (!d || !player(d)) return original;
    void *bag=items(d); u8 *weapon=bag ? item(bag,1810) : 0;
    if (!weapon || *(u16*)(weapon+2)!=60079) return original;
    ++gmtool_unclean_counters[0]; gmtool_unclean_counters[1]=unit;
    return 0;
}
__attribute__((naked,noinline)) static int scatter_entry(void *row __attribute__((unused)),int column __attribute__((unused)),int opt __attribute__((unused))) {
    __asm__ volatile("subl $12,%esp\n\tpushl 8(%ebp)\n\tpushl 28(%esp)\n\tpushl 28(%esp)\n\tpushl 28(%esp)\n\tcall scatter_flag\n\taddl $28,%esp\n\tret");
}
static int protect(void *p,u32 n,int f){int r;__asm__ volatile("pushl %%ebx\n\tmovl %1, %%ebx\n\tint $0x80\n\tpopl %%ebx":"=a"(r):"r"(p),"a"(125),"c"(n),"d"(f):"memory","cc");return r;}
__attribute__((visibility("default"))) int gmtool_unclean_scatter_hook(u32 base,int install) {
    if (!base || (install!=0 && install!=1)) return -1;
    u8 *site=(u8*)(base+0xa79473); u32 old=base+0x6559b0,hook=(u32)(void*)&scatter_entry;
    u32 rel=(install?old:hook)-(u32)(site+5);
    if(site[0]!=0xe8)return -2;
    for(u32 i=0;i<4;i++)if(site[i+1]!=(u8)(rel>>(8*i)))return -2;
    if(protect((void*)((u32)site&~4095U),4096,7))return -3;
    image_base=base;rel=(install?hook:old)-(u32)(site+5);
    for(u32 i=0;i<4;i++)site[i+1]=(u8)(rel>>(8*i));
    if(protect((void*)((u32)site&~4095U),4096,5))return -4;
    return 0;
}
