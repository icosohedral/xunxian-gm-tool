/* 60079 scatter target cap; verified TTYGZS_d.so sha256 9a19e9c8deaefeb8ba35d61f10ccc6f32d166ef2ee2aedf1f3ecf06cfa7109bb. */
typedef unsigned char u8;typedef unsigned short u16;typedef unsigned int u32;
static u32 image_base;
__attribute__((visibility("default"))) volatile u32 gmtool_unclean_target_counters[4];
__attribute__((noinline,used)) static int scatter_limit(void *method,int id,int column) {
    int (*orig)(void*,int,int)=(void*)(image_base+0xb1b330);
    int value=orig(method,id,column);
    if(!method||column!=11||value!=1||(id!=30079&&id!=30143&&id!=30275&&id!=30276))return value;
    void *(*src)(void*)=(void*)(image_base+0xb1b2c0);
    void *actor=src(method);if(!actor)return value;
    void *(*data)(void*)=(void*)(image_base+0xae64ec);
    int (*player)(void*)=(void*)(image_base+0xb2d42e);
    void *(*items)(void*)=(void*)(image_base+0xb44776);
    void *(*item)(void*,int)=(void*)(image_base+0x68b3f6);
    void *d=data(actor);if(!d||!player(d))return value;
    void *bag=items(d);u8 *w=bag?item(bag,1810):0;
    if(!w||*(u16*)(w+2)!=60079)return value;
    ++gmtool_unclean_target_counters[0];gmtool_unclean_target_counters[1]=(u32)id;
    return 0x7fffffff;
}
static int protect(void*p,u32 n,int f){int r;__asm__ volatile("pushl %%ebx\n\tmovl %1, %%ebx\n\tint $0x80\n\tpopl %%ebx":"=a"(r):"r"(p),"a"(125),"c"(n),"d"(f):"memory","cc");return r;}
__attribute__((visibility("default"))) int gmtool_unclean_targets_hook(u32 base,int install){
 if(!base||(install!=0&&install!=1))return -1;
 u8 *s=(u8*)(base+0xb1c859);u32 old=base+0x657e50,hook=(u32)(void*)&scatter_limit;
 u32 rel=(install?old:hook)-(u32)(s+5);if(s[0]!=0xe8)return -2;
 for(u32 i=0;i<4;i++)if(s[i+1]!=(u8)(rel>>(i*8)))return -2;
 if(protect((void*)((u32)s&~4095U),4096,7))return -3;
 image_base=base;rel=(install?hook:old)-(u32)(s+5);
 for(u32 i=0;i<4;i++)s[i+1]=(u8)(rel>>(i*8));
 if(protect((void*)((u32)s&~4095U),4096,5))return -4;
 return 0;
}
