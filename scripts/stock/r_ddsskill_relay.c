/********************************************************
Copyright 2002-2006, Pixel Software Corporation, 
All rights reserved->

脚本内容:	

具体类容:	调用打地鼠的技能

作者:		张冰蕾

创建时间：	2008-10-13

历史记录:	

*********************************************************/
#include "../../inc/globaldef.h"
#include "../../inc/senddef.h"
#include "dadishu.h"

#define PateType_EnemyHP 5
int g_iPlayerID = 0;

void Run( int iScnID,int iPlayerGID,int iData1,int iData2,int iData3,int iData4 )
{
	if (iData4 == 91827364)
	{
		G_RunScript("r_stock_trade", "Run", iScnID, iPlayerGID, iData1, iData2, iData3, 0);
		return;
	}
	//G_Printf("dds_skill, skill = %d\n", iData1);
	
	UtilityData ud;
	if(!ud.CheckScn(iScnID))							return;
    if(!ud.CheckPlayerGID(iPlayerGID))					return;


	CScnObjMngS* pScnObjMngS = ud.GetObjMng();
	CServerChaPlayer* pPlayerCha = ud.GetPlayer();
	CPlayerDataMngS* pPlayerMng = ud.GetPlayerMng();
	CStateMngS*	pStateMng =	G_GetStateMngS(pPlayerMng);
	if(pPlayerMng->GetAreaExcelID() == 1102)
	{
		SStateGroup* stategroup = pStateMng->GetStateGroup(308);
		if(stategroup != NULL)	return;
		if(!pPlayerCha->SetCurTask(Task_Skill, 340, iData2, UseSkillFromScript(), NULL))//iData1:技能id  iData2：目标autoid
		{
			G_Printf("skill failed");
			return;
		}
	}else
		return;

	CServerCha * pNpcCha = pScnObjMngS->GetServerCha( iData2 ); 
	if(pNpcCha == NULL)	return;
	CChaDataMngBase* pNpcMng = pNpcCha->GetChaDataMng();

	int type = pNpcMng->GetDWordTemp(n_dds_npc_type);//--------分别死亡，该调技能的调技能，该把标志为点清零的清零
	if(type == 0)	return;
	if(type == 1)	//敲的是老鼠
	{
		int color = pNpcMng->GetDWordTemp(n_dds_ds_color);
		G_RunScript("n_dds_dishu","DiShu_Dead", iScnID, iData2, iPlayerGID, color);
	//	
	}else if(type == 2) //敲的是宝箱
	{
		int index = pNpcMng->GetDWordTemp(n_dds_box_index);
		G_RunScript("n_dds_box","Open_Box",iScnID, iData2, iPlayerGID, index);

	}else 
	if(type == 3)	//敲的是土块什么的
	{
		int bonus_type = pNpcMng->GetDWordTemp(n_dds_bonus_type);
		int count = pNpcMng->GetDWordTemp(n_dds_bonus_count);
		switch(bonus_type)
		{
		case 1:
			{
				if(count < 4)
				{
					count++;
					pNpcMng->SetDWordTemp(n_dds_bonus_count, count);
					pNpcCha->SendPacket( SENDMODE_TO_ONEPLAYER, iPlayerGID, STC_CHA_BEHIT, count, PateType_EnemyHP, 0, 0 );
					//SendPacket( int iSendMode,int iData,int iCmd,int iPar0 = 0,int iPar1 = 0,int iPar2 = 0,int iPar3 = 0 );
				}else
				{
					G_RunScript("n_dds_bonus","Open_Bonus", iScnID, iData2, iPlayerGID, bonus_type);
				}
			}
			break;
		case 2:
			{
				if(count < 9) 
				{
					count++;
					pNpcMng->SetDWordTemp(n_dds_bonus_count, count);
					pNpcCha->SendPacket( SENDMODE_TO_ONEPLAYER, iPlayerGID, STC_CHA_BEHIT, count, PateType_EnemyHP, 0, 0 );
				}else
				{
					G_RunScript("n_dds_bonus","Open_Bonus", iScnID, iData2, iPlayerGID, bonus_type);
				}
			}
			break;
		case 3:
			{
				if(count < 14)
				{
					count++;
					pNpcMng->SetDWordTemp(n_dds_bonus_count, count);
					pNpcCha->SendPacket( SENDMODE_TO_ONEPLAYER, iPlayerGID, STC_CHA_BEHIT, count, PateType_EnemyHP, 0, 0 );
				}else
				{
					G_RunScript("n_dds_bonus","Open_Bonus", iScnID, iData2, iPlayerGID, bonus_type);
				}
			}
			break;
		default:
			break;
		}
			
	}
	
}

