// 小黄原生对话窗口和押大小玩法；金额按铜为最小单位。
#include "inc/exceldef.h"
#include "inc/senddef.h"
#include "inc/globaldef.h"

#define XH_INTRO 50000
#define XH_BIG_SMALL 50001
#define XH_CARDS 50002
#define XH_WHEEL 50003
#define XH_RULES 50004
#define XH_LEAVE 50005
#define XH_PREVIEW 50006
#define XH_BACK 50007
#define XH_BET_INTRO 50008
#define XH_BET_100 50009
#define XH_BET_10 50010
#define XH_BET_1 50011
#define XH_GUESS_INTRO 50012
#define XH_GUESS_BIG 50013
#define XH_GUESS_SMALL 50014
#define XH_WIN 50015
#define XH_LOSE 50016
#define XH_NOT_ENOUGH 50017
#define XH_BUSY 50018
#define XH_AGAIN 50019
#define XH_PAY_FAIL 50020
#define XH_CHAT_WIN 50021
#define XH_CHAT_LOSE 50022
#define XH_RESULT_SENT 50023
#define XH_FLAG 208

void ShowMain(int iScnID, int iNpcID, int iPlayerGID)
{
    CPlayerUnitS* unit = G_GetPlayer(iPlayerGID);
    if (unit == NULL || unit->GetDataMng() == NULL || G_GetScn(iScnID) == NULL) return;
    int money = unit->GetDataMng()->GetAtb(PlayerAtb_Money);
    g_pScriptMng->BeginDlg(XH_INTRO, iNpcID,
        money / 1000000, money / 10000 % 100, money / 100 % 100, money % 100);
    g_pScriptMng->AddSelDlg(XH_BIG_SMALL, 0, "SelBigSmall");
    g_pScriptMng->AddSelDlg(XH_CARDS, 0, "SelCards");
    g_pScriptMng->AddSelDlg(XH_WHEEL, 0, "SelWheel");
    g_pScriptMng->AddSelDlg(XH_RULES, 0, "SelRules");
    g_pScriptMng->AddSelDlg(XH_LEAVE, 0, "SelLeave");
    g_pScriptMng->EndDlg(iPlayerGID, -1);
}

void Npc_Talk(int iScnID, int iNpcID, int iPlayerGID)
{
    CPlayerUnitS* unit = G_GetPlayer(iPlayerGID);
    if (unit != NULL && unit->GetDataMng() != NULL)
        unit->GetDataMng()->SetDWordTemp(XH_FLAG, 0);
    ShowMain(iScnID, iNpcID, iPlayerGID);
}

void ShowBet(int iScnID, int iNpcID, int iPlayerGID)
{
    CPlayerUnitS* unit = G_GetPlayer(iPlayerGID);
    if (unit == NULL || unit->GetDataMng() == NULL) return;
    int money = unit->GetDataMng()->GetAtb(PlayerAtb_Money);
    g_pScriptMng->BeginDlg(XH_BET_INTRO, iNpcID,
        money / 1000000, money / 10000 % 100, money / 100 % 100, money % 100);
    g_pScriptMng->AddSelDlg(XH_BET_100, 100000000, "SelBet");
    g_pScriptMng->AddSelDlg(XH_BET_10, 10000000, "SelBet");
    g_pScriptMng->AddSelDlg(XH_BET_1, 1000000, "SelBet");
    g_pScriptMng->AddSelDlg(XH_BACK, 0, "SelBack");
    g_pScriptMng->EndDlg(iPlayerGID, -1);
}

void SelBet(int iPar, int iScnID, int iNpcID, int iPlayerGID)
{
    if (iPar != 100000000 && iPar != 10000000 && iPar != 1000000) return;
    if (G_GetPlayer(iPlayerGID) == NULL) return;
    g_pScriptMng->BeginDlg(XH_GUESS_INTRO, iNpcID, iPar / 1000000);
    g_pScriptMng->AddSelDlg(XH_GUESS_BIG, iPar, "SelBig");
    g_pScriptMng->AddSelDlg(XH_GUESS_SMALL, iPar, "SelSmall");
    g_pScriptMng->AddSelDlg(XH_BACK, 0, "SelBack");
    g_pScriptMng->EndDlg(iPlayerGID, -1);
}

void Settle(int bet, bool guessedBig, int iScnID, int iNpcID, int iPlayerGID)
{
    if (bet != 100000000 && bet != 10000000 && bet != 1000000) return;
    CPlayerUnitS* unit = G_GetPlayer(iPlayerGID);
    if (unit == NULL || unit->GetDataMng() == NULL || G_GetScn(iScnID) == NULL) return;
    CPlayerDataMngS* player = unit->GetDataMng();
    if (player->GetDWordTemp(XH_FLAG) != 0) {
        g_pScriptMng->BeginDlg(XH_BUSY, iNpcID);
        g_pScriptMng->AddSelDlg(XH_AGAIN, 0, "SelAgain");
        g_pScriptMng->EndDlg(iPlayerGID, -1);
        return;
    }
    player->SetDWordTemp(XH_FLAG, 1);
    if (player->GetAtb(PlayerAtb_Money) < bet ||
        !player->TrySubMoney(-bet, EBOUNDMONEY_TRADE)) {
        g_pScriptMng->BeginDlg(XH_NOT_ENOUGH, iNpcID);
        g_pScriptMng->AddSelDlg(XH_AGAIN, 0, "SelAgain");
        g_pScriptMng->EndDlg(iPlayerGID, -1);
        return;
    }
    int die = G_Rand(6) + 1;
    bool won = (die >= 4) == guessedBig;
    int payout = bet * 2;
    if (won && !player->TryAddMoney(payout, 0, EBOUNDMONEY_TRADE)) {
        g_pScriptMng->BeginDlg(XH_PAY_FAIL, iNpcID);
        g_pScriptMng->AddSelDlg(XH_AGAIN, 0, "SelAgain");
        g_pScriptMng->EndDlg(iPlayerGID, -1);
        return;
    }
    if (!player->SubMoney(-bet, G_IntToInt64(0), Log_MnyLost_Script, 0, NULL, EBOUNDMONEY_TRADE)) {
        g_pScriptMng->BeginDlg(XH_PAY_FAIL, iNpcID);
        g_pScriptMng->AddSelDlg(XH_AGAIN, 0, "SelAgain");
        g_pScriptMng->EndDlg(iPlayerGID, -1);
        return;
    }
    if (won) player->AddMoney(payout, G_IntToInt64(0), 0, 0, "xiaohuang_bigsmall", EBOUNDMONEY_TRADE);
    if (won)
        g_pScriptMng->OutputMsg(XH_CHAT_WIN, -iNpcID, SENDMODE_TO_NEARPLAYER,
            iPlayerGID, player->GetName(), bet / 1000000, die, payout / 1000000);
    else
        g_pScriptMng->OutputMsg(XH_CHAT_LOSE, -iNpcID, SENDMODE_TO_NEARPLAYER,
            iPlayerGID, player->GetName(), bet / 1000000, die);
    g_pScriptMng->BeginDlg(XH_RESULT_SENT, iNpcID);
    g_pScriptMng->AddSelDlg(XH_AGAIN, 0, "SelAgain");
    g_pScriptMng->EndDlg(iPlayerGID, -1);
}

void SelBig(int iPar, int iScnID, int iNpcID, int iPlayerGID)
{
    Settle(iPar, true, iScnID, iNpcID, iPlayerGID);
}

void SelSmall(int iPar, int iScnID, int iNpcID, int iPlayerGID)
{
    Settle(iPar, false, iScnID, iNpcID, iPlayerGID);
}

void SelAgain(int iPar, int iScnID, int iNpcID, int iPlayerGID)
{
    CPlayerUnitS* unit = G_GetPlayer(iPlayerGID);
    if (unit != NULL && unit->GetDataMng() != NULL)
        unit->GetDataMng()->SetDWordTemp(XH_FLAG, 0);
    ShowBet(iScnID, iNpcID, iPlayerGID);
}

void ShowPreview(int iScnID, int iNpcID, int iPlayerGID)
{
    if (G_GetPlayer(iPlayerGID) == NULL || G_GetScn(iScnID) == NULL) return;
    g_pScriptMng->BeginDlg(XH_PREVIEW, iNpcID);
    g_pScriptMng->AddSelDlg(XH_BACK, 0, "SelBack");
    g_pScriptMng->EndDlg(iPlayerGID, -1);
}

void SelBigSmall(int iPar, int iScnID, int iNpcID, int iPlayerGID)
{
    ShowBet(iScnID, iNpcID, iPlayerGID);
}

void SelCards(int iPar, int iScnID, int iNpcID, int iPlayerGID)
{
    ShowPreview(iScnID, iNpcID, iPlayerGID);
}

void SelWheel(int iPar, int iScnID, int iNpcID, int iPlayerGID)
{
    ShowPreview(iScnID, iNpcID, iPlayerGID);
}

void SelRules(int iPar, int iScnID, int iNpcID, int iPlayerGID)
{
    ShowPreview(iScnID, iNpcID, iPlayerGID);
}

void SelBack(int iPar, int iScnID, int iNpcID, int iPlayerGID)
{
    ShowMain(iScnID, iNpcID, iPlayerGID);
}

void SelLeave(int iPar, int iScnID, int iNpcID, int iPlayerGID)
{
    g_pScriptMng->EndTalk(iPlayerGID);
}
