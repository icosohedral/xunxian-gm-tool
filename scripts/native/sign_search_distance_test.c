typedef unsigned char u8;typedef unsigned int u32;
static u32 image_base;
__attribute__((visibility("default"))) volatile u32 gmtool_sign_distance_counters[4];
static int distance(void *actor){u8*b=(u8*)image_base;int(*orig)(void*)=(void*)(b+0x93f170);int value=orig(actor);if(!actor)return value;void*(*data)(void*)=(void*)(b+0xae64ec);int(*npc)(void*)=(void*)(b+0xb2d444);void*d=data(actor);if(!d||!npc(d))return value;u8*x=*(u8**)((u8*)d+0xb0);if(!x)return value;int type=*(int*)(x+4);if(!((type>=20&&type<=22)||(type>=349&&type<=351))||!*(void**)(x+8))return value;++gmtool_sign_distance_counters[0];gmtool_sign_distance_counters[1]=value;return 15;}
static int protect(void*p,u32 n,int f){int r;__asm__ volatile("pushl %%ebx\n\tmovl %1, %%ebx\n\tint $0x80\n\tpopl %%ebx":"=a"(r):"r"(p),"a"(125),"c"(n),"d"(f):"memory","cc");return r;}
__attribute__((visibility("default"))) int gmtool_sign_distance_hook(u32 base,int install){
 if(!base||(install!=0&&install!=1))return -1;
 u32 offsets[2]={0x94acff,0x94afe2};u32 old=base+0x619000,hook=(u32)(void*)&distance;
 for(int j=0;j<2;j++){u8*s=(u8*)(base+offsets[j]);u32 rel=(install?old:hook)-(u32)(s+5);if(s[0]!=0xe8)return -2;for(u32 i=0;i<4;i++)if(s[i+1]!=(u8)(rel>>(8*i)))return -2;}
 if(protect((void*)((base+offsets[0])&~4095U),4096,7))return -3;
 image_base=base;for(int j=0;j<2;j++){u8*s=(u8*)(base+offsets[j]);u32 rel=(install?hook:old)-(u32)(s+5);for(u32 i=0;i<4;i++)s[i+1]=(u8)(rel>>(8*i));}
 if(protect((void*)((base+offsets[0])&~4095U),4096,5))return -4;
 return 0;
}
