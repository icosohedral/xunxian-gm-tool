#include "../scripts/native/unclean_scatter_targets_test.c"
static int weapon_id=60079,isplayer=1,original=1;static u8 weapon_mem[16];
static int orig(void*p,int id,int col){(void)p;(void)id;(void)col;return original;}
static void* identity(void*p){return p;}
static int player(void*p){(void)p;return isplayer;}
static void* item(void*p,int slot){(void)p;(void)slot;*(u16*)(weapon_mem+2)=weapon_id;return weapon_mem;}
static void jump(u32 a,void*fn){u8*s=(u8*)a;u32 r=(u32)fn-a-5;s[0]=0xe9;for(int i=0;i<4;i++)s[i+1]=(u8)(r>>(8*i));}
void _start(void){u32 base=0x40000000u;int ret;u32 args[6]={base,0x1000000,7,0x32,0xffffffff,0};
 __asm__ volatile("pushl %%ebx; movl %2,%%ebx; int $0x80; popl %%ebx":"=a"(ret):"a"(90),"r"(args):"memory","cc");if((u32)ret!=base)goto fail;
 image_base=base;jump(base+0xb1b330,orig);jump(base+0xb1b2c0,identity);jump(base+0xae64ec,identity);jump(base+0xb2d42e,player);jump(base+0xb44776,identity);jump(base+0x68b3f6,item);
 int ids[4]={30079,30143,30275,30276};for(int i=0;i<4;i++)if(scatter_limit((void*)1,ids[i],11)!=0x7fffffff)goto fail;
 if(scatter_limit((void*)1,30079,12)!=1||scatter_limit((void*)1,30080,11)!=1)goto fail;
 weapon_id=60078;if(scatter_limit((void*)1,30079,11)!=1)goto fail;weapon_id=60079;isplayer=0;if(scatter_limit((void*)1,30079,11)!=1)goto fail;
 isplayer=1;original=3;if(scatter_limit((void*)1,30079,11)!=3)goto fail;original=1;
 u8*s=(u8*)(base+0xb1c859);u32 rel=base+0x657e50-(u32)s-5;s[0]=0xe8;for(int i=0;i<4;i++)s[i+1]=(u8)(rel>>(8*i));
 if(gmtool_unclean_targets_hook(base,1))goto fail;rel=0;for(int i=0;i<4;i++)rel|=(u32)s[i+1]<<(8*i);if((u32)s+5+rel!=(u32)scatter_limit)goto fail;
 if(gmtool_unclean_targets_hook(base,0))goto fail;rel=0;for(int i=0;i<4;i++)rel|=(u32)s[i+1]<<(8*i);if((u32)s+5+rel!=base+0x657e50)goto fail;
 __asm__ volatile("int $0x80"::"a"(1),"b"(0));fail:__asm__ volatile("int $0x80"::"a"(1),"b"(1));}
