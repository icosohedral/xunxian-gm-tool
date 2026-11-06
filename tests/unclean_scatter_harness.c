#include "../scripts/native/unclean_scatter_pierce_test.c"
static int unit=809, flag=1, isplayer=1, weapon_id=60079, calls;
static u8 weapon_mem[16];
static int getint_fake(void *row,int col,int opt){(void)row;(void)opt;calls++;return col==11?flag:col==1?unit:0;}
static void* src_fake(void *p){return p;}
static void* data_fake(void *p){return p;}
static int player_fake(void *p){(void)p;return isplayer;}
static void* items_fake(void *p){return p;}
static void* item_fake(void *p,int slot){(void)p;(void)slot;*(u16*)(weapon_mem+2)=weapon_id;return weapon_mem;}
static void jump(u32 a,void *fn){u8 *s=(u8*)a;u32 rel=(u32)fn-(a+5);s[0]=0xe9;for(int i=0;i<4;i++)s[i+1]=(u8)(rel>>(i*8));}
__attribute__((naked,noinline)) static int invoke(void *fly __attribute__((unused)),void *row __attribute__((unused)),int col __attribute__((unused)),int opt __attribute__((unused))) {__asm__ volatile("push %ebp; mov %esp,%ebp; sub $12,%esp; push 20(%ebp); push 16(%ebp); push 12(%ebp); call scatter_entry; add $24,%esp; leave; ret");}
void _start(void){
  u32 base=0x40000000u; int ret; u32 args[6]={base,0x1000000,7,0x32,0xffffffff,0};
  __asm__ volatile("pushl %%ebx; movl %2,%%ebx; int $0x80; popl %%ebx":"=a"(ret):"a"(90),"r"(args):"memory","cc");
  if ((u32)ret!=base) goto fail;
  image_base=base;
  jump(base+0xc5ca14,getint_fake);jump(base+0xb1b2c0,src_fake);jump(base+0xae64ec,data_fake);jump(base+0xb2d42e,player_fake);jump(base+0xb44776,items_fake);jump(base+0x68b3f6,item_fake);
  if(invoke((void*)0x999,(void*)0x123,11,0)!=0)goto fail;
  unit=1190;if(invoke((void*)0x999,(void*)0x123,11,0)!=0)goto fail;
  unit=6680;if(invoke((void*)0x999,(void*)0x123,11,0)!=0)goto fail;
  weapon_id=60078;if(invoke((void*)0x999,(void*)0x123,11,0)!=1)goto fail;
  weapon_id=60079;isplayer=0;if(invoke((void*)0x999,(void*)0x123,11,0)!=1)goto fail;
  isplayer=1;unit=123;if(invoke((void*)0x999,(void*)0x123,11,0)!=1)goto fail;
  unit=809;flag=0;if(invoke((void*)0x999,(void*)0x123,11,0)!=0)goto fail;
  flag=1;if(invoke((void*)0,(void*)0x123,11,0)!=1)goto fail;
  __asm__ volatile("int $0x80"::"a"(1),"b"(0));
fail: __asm__ volatile("int $0x80"::"a"(1),"b"(1));
}
