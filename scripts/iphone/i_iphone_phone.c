// iPhone 手机：使用道具打开游戏原生对话框。
#include "../inc/globaldef.h"
#include "../inc/senddef.h"
#include "../inc/exceldef.h"

#define PHONE_ITEM_ID 60080
#define PHONE_HOME 50032
#define PHONE_CONTACTS 50033
#define PHONE_STOCKS 50034
#define PHONE_DELIVERY 50035
#define PHONE_PENDING 50036
#define PHONE_BACK 50037
#define PHONE_CLOSE 50038
#define PHONE_HOLDINGS 50039
#define PHONE_CONTACTS_TITLE 50040
#define PHONE_POLICE 50041
#define PHONE_AMBULANCE 50042
#define PHONE_TENCENT 50043
#define PHONE_NVIDIA 50044
#define PHONE_ANTHROPIC 50045
#define PHONE_DELIVERY_TITLE 50046
#define PHONE_DELIVERY_OPEN 50047
#define PHONE_DELIVERY_CLOSE 50048

void HideDelivery(int iPlayerGID)
{
    g_pScriptMng->OutputMsg(PHONE_DELIVERY_CLOSE, 0, SENDMODE_TO_ONEPLAYER,
                            iPlayerGID);
}

void OpenPhone(int iPlayerGID, int iScnID, int iExcelID, int iUnused)
{
    G_Printf("iphone open player=%d scene=%d item=%d\n", iPlayerGID, iScnID, iExcelID);
    if (G_GetScn(iScnID) == NULL || G_GetPlayer(iPlayerGID) == NULL) return;
    HideDelivery(iPlayerGID);
    g_pScriptMng->BeginDlg(PHONE_HOME, -PHONE_ITEM_ID);
    g_pScriptMng->AddSelDlg(PHONE_CONTACTS, 0, "SelContacts");
    g_pScriptMng->AddSelDlg(PHONE_STOCKS, 0, "SelStocks");
    g_pScriptMng->AddSelDlg(PHONE_DELIVERY, 0, "SelDelivery");
    g_pScriptMng->AddSelDlg(50065, 0, "SelMusic");
    g_pScriptMng->EndDlg(iPlayerGID, 0);
}

void SelContacts(int iPar, int iScnID, int iExcelID, int iPlayerGID)
{
    G_Printf("iphone SelContacts par=%d scn=%d item=%d player=%d\n",
             iPar, iScnID, iExcelID, iPlayerGID);
    if (G_GetPlayer(iPlayerGID) == NULL) return;
    HideDelivery(iPlayerGID);
    g_pScriptMng->BeginDlg(PHONE_CONTACTS_TITLE, -PHONE_ITEM_ID);
    g_pScriptMng->AddSelDlg(PHONE_POLICE, 1, "CallContact");
    g_pScriptMng->AddSelDlg(PHONE_AMBULANCE, 2, "CallContact");
    g_pScriptMng->AddSelDlg(PHONE_BACK, 0, "SelBack");
    g_pScriptMng->EndDlg(iPlayerGID, 0);
}

string PhoneStockValue(int stock, int shares)
{
    if (shares < 0 || shares > 1000) shares = 0;
    CPGTime now = G_GetCurrentTime();
    int slot = (now.GetYear() * 372 + now.GetMonth() * 31 + now.GetDay()) * 288 + now.GetHour() * 12 + now.GetMinute() / 5;
    int base = stock == 1 ? 1000000 : (stock == 2 ? 3000000 : 5000000);
    int seed = (slot % 100000) * 7919 + stock * 104729;
    int price = base + (base / 1000) * (seed % 60001 - 30000) / 1000;
    int remainder = (price % 10000) * shares;
    int gold = (price / 10000) * shares + remainder / 10000;
    remainder = remainder % 10000;
    string result;
    result += gold / 100; result += "砖";
    result += gold % 100; result += "金";
    result += remainder / 100; result += "银";
    result += remainder % 100; result += "铜";
    return result;
}

void SelStocks(int iPar, int iScnID, int iExcelID, int iPlayerGID)
{
    G_Printf("iphone SelStocks par=%d scn=%d item=%d player=%d\n",
             iPar, iScnID, iExcelID, iPlayerGID);
    CPlayerUnitS* unit = G_GetPlayer(iPlayerGID);
    if (unit == NULL || unit->GetDataMng() == NULL) return;
    CPlayerDataMngS* player = unit->GetDataMng();
    HideDelivery(iPlayerGID);
    int shares1 = player->GetDWordFlag(200);
    int shares2 = player->GetDWordFlag(201);
    int shares3 = player->GetDWordFlag(202);
    string value1 = PhoneStockValue(1, shares1);
    string value2 = PhoneStockValue(2, shares2);
    string value3 = PhoneStockValue(3, shares3);
    g_pScriptMng->BeginDlg(PHONE_HOLDINGS, -PHONE_ITEM_ID,
        shares1, value1.c_str(), shares2, value2.c_str(), shares3, value3.c_str());
    g_pScriptMng->AddSelDlg(PHONE_TENCENT, 0, "SelPending");
    g_pScriptMng->AddSelDlg(PHONE_NVIDIA, 0, "SelPending");
    g_pScriptMng->AddSelDlg(PHONE_ANTHROPIC, 0, "SelPending");
    g_pScriptMng->AddSelDlg(PHONE_BACK, 0, "SelBack");
    g_pScriptMng->EndDlg(iPlayerGID, 0);
}

void SelDelivery(int iPar, int iScnID, int iExcelID, int iPlayerGID)
{
    G_Printf("iphone SelDelivery par=%d scn=%d item=%d player=%d\n",
             iPar, iScnID, iExcelID, iPlayerGID);
    if (G_GetPlayer(iPlayerGID) == NULL) return;
    HideDelivery(iPlayerGID);
    g_pScriptMng->BeginDlg(PHONE_DELIVERY_TITLE, -PHONE_ITEM_ID);
    g_pScriptMng->AddSelDlg(50057, 1, "DeliveryShop");
    g_pScriptMng->AddSelDlg(50058, 2, "DeliveryShop");
    g_pScriptMng->AddSelDlg(50059, 3, "DeliveryShop");
    g_pScriptMng->AddSelDlg(PHONE_BACK, 0, "SelBack");
    g_pScriptMng->EndDlg(iPlayerGID, 0);
}

void DeliveryShop(int iPar, int iScnID, int iExcelID, int iPlayerGID)
{
    if (G_GetPlayer(iPlayerGID) == NULL || iPar < 1 || iPar > 3) return;
    g_pScriptMng->BeginDlg(50056 + iPar, -PHONE_ITEM_ID);
    if (iPar == 1) {
        g_pScriptMng->AddSelDlg(50049, 1, "BuyDelivery");
        g_pScriptMng->AddSelDlg(50050, 2, "BuyDelivery");
        g_pScriptMng->AddSelDlg(50051, 3, "BuyDelivery");
        g_pScriptMng->AddSelDlg(50052, 4, "BuyDelivery");
    } else if (iPar == 2) {
        g_pScriptMng->AddSelDlg(50053, 5, "BuyDelivery");
        g_pScriptMng->AddSelDlg(50064, 10, "BuyDelivery");
        g_pScriptMng->AddSelDlg(50060, 6, "BuyDelivery");
        g_pScriptMng->AddSelDlg(50061, 7, "BuyDelivery");
        g_pScriptMng->AddSelDlg(50062, 8, "BuyDelivery");
    } else {
        g_pScriptMng->AddSelDlg(50063, 9, "BuyDelivery");
    }
    g_pScriptMng->AddSelDlg(PHONE_BACK, 0, "SelDelivery");
    g_pScriptMng->EndDlg(iPlayerGID, 0);
}

void BuyDelivery(int iPar, int iScnID, int iExcelID, int iPlayerGID)
{
    int item = 0; int count = 0; int price = 0;
    if (iPar == 1) { item = 19239; count = 200; price = 200000; }
    else if (iPar == 2) { item = 19249; count = 200; price = 200000; }
    else if (iPar == 3) { item = 1484; count = 50; price = 5000000; }
    else if (iPar == 4) { item = 12031; count = 50; price = 10000000; }
    else if (iPar == 5) { item = 2015; count = 200; price = 10000; }
    else if (iPar == 6) { item = 12505; count = 10; price = 10000000; }
    else if (iPar == 7) { item = 2012; count = 1; price = 100000; }
    else if (iPar == 8) { item = 2006; count = 1; price = 1000000; }
    else if (iPar == 9) { item = 7819; count = 1; price = 1000000; }
    else if (iPar == 10) { item = 12506; count = 10; price = 10000000; }
    else return;
    UtilityData ud;
    if (!ud.CheckPlayerGID(iPlayerGID)) return;
    CItemMngS* items = ud.GetItemMng();
    CPlayerDataMngS* player = ud.GetPlayerMng();
    int result = 50054;
    if (!player->TrySubMoney(-price, EBOUNDMONEY_TRADE)) result = 50055;
    else if (!items->Script_TryAddItemFromExcelID(item, count, ItemBox_Bag)) result = 50056;
    else if (!player->SubMoney(-price, G_IntToInt64(0), Log_MnyLost_Script, 0, NULL, EBOUNDMONEY_TRADE)) result = 50055;
    else items->Script_AddItem(item, count, ItemBox_Bag);
    g_pScriptMng->BeginDlg(result, -PHONE_ITEM_ID);
    int shop = 1;
    if ((iPar >= 5 && iPar <= 8) || iPar == 10) shop = 2;
    else if (iPar == 9) shop = 3;
    g_pScriptMng->AddSelDlg(PHONE_BACK, shop, "DeliveryShop");
    g_pScriptMng->AddSelDlg(PHONE_BACK, 0, "SelBack");
    g_pScriptMng->EndDlg(iPlayerGID, 0);
}

void Item_Use(int iScnID, int iPlayerGID, int iExcelID,
              int iItemUsePar1, int iItemUsePar2)
{
    G_Printf("iphone use player=%d scene=%d item=%d\n", iPlayerGID, iScnID, iExcelID);
    if (iExcelID != PHONE_ITEM_ID || G_GetScn(iScnID) == NULL) return;
    CPlayerUnitS* player = G_GetPlayer(iPlayerGID);
    if (player == NULL || player->GetDataMng() == NULL) return;
    player->GetDataMng()->TalkToItem(iExcelID);
    G_AddTimerFunc4(NULL, "OpenPhone", 0, iPlayerGID, iScnID, iExcelID, 0);
}

void SelPending(int iPar, int iScnID, int iExcelID, int iPlayerGID)
{
    if (G_GetPlayer(iPlayerGID) == NULL) return;
    g_pScriptMng->BeginDlg(PHONE_PENDING, -PHONE_ITEM_ID);
    g_pScriptMng->AddSelDlg(PHONE_BACK, 0, "SelBack");
    g_pScriptMng->AddSelDlg(PHONE_CLOSE, 0, "SelClose");
    g_pScriptMng->EndDlg(iPlayerGID, 0);
}

void SelBack(int iPar, int iScnID, int iExcelID, int iPlayerGID)
{
    OpenPhone(iPlayerGID, iScnID, iExcelID, 0);
}

void SelClose(int iPar, int iScnID, int iExcelID, int iPlayerGID)
{
    HideDelivery(iPlayerGID);
    g_pScriptMng->EndTalk(iPlayerGID);
}

void CallContact(int iPar, int iScnID, int iExcelID, int iPlayerGID)
{
    int summonType = 0;
    int chaId = 0;
    if (iPar == 1) { summonType = 430; chaId = 37236; }
    else if (iPar == 2) { summonType = 431; chaId = 37650; }
    else return;
    UtilityData ud;
    if (!ud.CheckScn(iScnID) || !ud.CheckPlayerGID(iPlayerGID)) return;
    CServerChaPlayer* player = ud.GetPlayer();
    CPlayerDataMngS* data = ud.GetPlayerMng();
    if (player == NULL || data == NULL) return;
    CServerCha* npc = player->Summon_Add(summonType, chaId,
        data->GetPosX(), data->GetPosZ(), data->GetPosAreaID(), "", 0);
    if (iPar == 2 && data->GetStateMng() != NULL)
        data->GetStateMng()->AddStateGroup(<uint16>(23002), G_IntToInt64(0), STATE_DEFAULT_TIME);
    int success = 0;
    if (npc != NULL) success = 1;
    G_Printf("iphone summon player=%d type=%d cha=%d ok=%d\n",
             iPlayerGID, summonType, chaId, success);
    g_pScriptMng->EndTalk(iPlayerGID);
}

void SelMusic(int iPar, int iScnID, int iExcelID, int iPlayerGID)
{
    if (G_GetPlayer(iPlayerGID) == NULL || iPar < 0 || iPar >= 14) return;
    HideDelivery(iPlayerGID);
    g_pScriptMng->BeginDlg(50066, -PHONE_ITEM_ID, iPar + 1, 14);
    if (iPar == 0) {
        g_pScriptMng->AddSelDlg(50067, 1, "PlayPhoneMusic");
        g_pScriptMng->AddSelDlg(50068, 2, "PlayPhoneMusic");
        g_pScriptMng->AddSelDlg(1, 3, "PlayPhoneMusic", "world_bf_bingchuan01");
        g_pScriptMng->AddSelDlg(1, 4, "PlayPhoneMusic", "world_bf_bingchuan02");
        g_pScriptMng->AddSelDlg(1, 5, "PlayPhoneMusic", "world_bf_dongtu01");
        g_pScriptMng->AddSelDlg(1, 6, "PlayPhoneMusic", "world_bf_dongtu02");
        g_pScriptMng->AddSelDlg(1, 7, "PlayPhoneMusic", "world_bf_hanling01");
        g_pScriptMng->AddSelDlg(1, 8, "PlayPhoneMusic", "world_bf_hanling02");
    }
    if (iPar == 1) {
        g_pScriptMng->AddSelDlg(1, 9, "PlayPhoneMusic", "world_bf_maoshanzhenyaota");
        g_pScriptMng->AddSelDlg(1, 10, "PlayPhoneMusic", "world_bf_xueyuan01");
        g_pScriptMng->AddSelDlg(1, 11, "PlayPhoneMusic", "world_bf_xueyuan02");
        g_pScriptMng->AddSelDlg(1, 12, "PlayPhoneMusic", "world_fb_banghui01");
        g_pScriptMng->AddSelDlg(1, 13, "PlayPhoneMusic", "world_fb_banghui02");
        g_pScriptMng->AddSelDlg(1, 14, "PlayPhoneMusic", "world_fb_huangling02");
        g_pScriptMng->AddSelDlg(1, 15, "PlayPhoneMusic", "world_hj_shenmi");
        g_pScriptMng->AddSelDlg(1, 16, "PlayPhoneMusic", "world_hj_xiane");
    }
    if (iPar == 2) {
        g_pScriptMng->AddSelDlg(1, 17, "PlayPhoneMusic", "world_shd_qingliu01");
        g_pScriptMng->AddSelDlg(1, 18, "PlayPhoneMusic", "world_shd_qingliu02");
        g_pScriptMng->AddSelDlg(1, 19, "PlayPhoneMusic", "world_theme_fly");
        g_pScriptMng->AddSelDlg(1, 20, "PlayPhoneMusic", "world_theme_start");
        g_pScriptMng->AddSelDlg(1, 21, "PlayPhoneMusic", "world_theme_village");
        g_pScriptMng->AddSelDlg(1, 22, "PlayPhoneMusic", "world_yj_guimen");
        g_pScriptMng->AddSelDlg(1, 23, "PlayPhoneMusic", "world_yj_huangquan");
        g_pScriptMng->AddSelDlg(1, 24, "PlayPhoneMusic", "world_yj_naihe");
    }
    if (iPar == 3) {
        g_pScriptMng->AddSelDlg(1, 25, "PlayPhoneMusic", "world_yj_qunyaohui");
        g_pScriptMng->AddSelDlg(1, 26, "PlayPhoneMusic", "world_zy_caodi01");
        g_pScriptMng->AddSelDlg(1, 27, "PlayPhoneMusic", "world_zy_caodi02");
        g_pScriptMng->AddSelDlg(1, 28, "PlayPhoneMusic", "world_zy_haihe");
        g_pScriptMng->AddSelDlg(1, 29, "PlayPhoneMusic", "world_zy_haihe01");
        g_pScriptMng->AddSelDlg(1, 30, "PlayPhoneMusic", "world_zy_haihe02");
        g_pScriptMng->AddSelDlg(1, 31, "PlayPhoneMusic", "world_zy_shadi");
        g_pScriptMng->AddSelDlg(1, 32, "PlayPhoneMusic", "world_zy_shandi01");
    }
    if (iPar == 4) {
        g_pScriptMng->AddSelDlg(1, 33, "PlayPhoneMusic", "world_zy_shandi02");
        g_pScriptMng->AddSelDlg(1, 34, "PlayPhoneMusic", "world_zy_shulin01");
        g_pScriptMng->AddSelDlg(1, 35, "PlayPhoneMusic", "world_zy_shulin02");
        g_pScriptMng->AddSelDlg(1, 36, "PlayPhoneMusic", "world_zy_yewai01");
        g_pScriptMng->AddSelDlg(1, 37, "PlayPhoneMusic", "xx_bailipo_01");
        g_pScriptMng->AddSelDlg(1, 38, "PlayPhoneMusic", "xx_baiyunguan_01");
        g_pScriptMng->AddSelDlg(1, 39, "PlayPhoneMusic", "xx_baiyunguan_02");
        g_pScriptMng->AddSelDlg(1, 40, "PlayPhoneMusic", "xx_bajiaodong_01");
    }
    if (iPar == 5) {
        g_pScriptMng->AddSelDlg(1, 41, "PlayPhoneMusic", "xx_bf_day");
        g_pScriptMng->AddSelDlg(1, 42, "PlayPhoneMusic", "xx_bf_main");
        g_pScriptMng->AddSelDlg(1, 43, "PlayPhoneMusic", "xx_bf_night");
        g_pScriptMng->AddSelDlg(1, 44, "PlayPhoneMusic", "xx_bzs_01");
        g_pScriptMng->AddSelDlg(1, 45, "PlayPhoneMusic", "xx_capital_day");
        g_pScriptMng->AddSelDlg(1, 46, "PlayPhoneMusic", "xx_capital_intro");
        g_pScriptMng->AddSelDlg(1, 47, "PlayPhoneMusic", "xx_capital_main");
        g_pScriptMng->AddSelDlg(1, 48, "PlayPhoneMusic", "xx_capital_night");
    }
    if (iPar == 6) {
        g_pScriptMng->AddSelDlg(1, 49, "PlayPhoneMusic", "xx_capital_palace");
        g_pScriptMng->AddSelDlg(1, 50, "PlayPhoneMusic", "xx_csg_day");
        g_pScriptMng->AddSelDlg(1, 51, "PlayPhoneMusic", "xx_fengchantai_01");
        g_pScriptMng->AddSelDlg(1, 52, "PlayPhoneMusic", "xx_field00");
        g_pScriptMng->AddSelDlg(1, 53, "PlayPhoneMusic", "xx_field00_sf");
        g_pScriptMng->AddSelDlg(1, 54, "PlayPhoneMusic", "xx_field01");
        g_pScriptMng->AddSelDlg(1, 55, "PlayPhoneMusic", "xx_field01_sf");
        g_pScriptMng->AddSelDlg(1, 56, "PlayPhoneMusic", "xx_field02");
    }
    if (iPar == 7) {
        g_pScriptMng->AddSelDlg(1, 57, "PlayPhoneMusic", "xx_field02_sf");
        g_pScriptMng->AddSelDlg(1, 58, "PlayPhoneMusic", "xx_field03");
        g_pScriptMng->AddSelDlg(1, 59, "PlayPhoneMusic", "xx_field03_sf");
        g_pScriptMng->AddSelDlg(1, 60, "PlayPhoneMusic", "xx_field04");
        g_pScriptMng->AddSelDlg(1, 61, "PlayPhoneMusic", "xx_field04_sf");
        g_pScriptMng->AddSelDlg(1, 62, "PlayPhoneMusic", "xx_field_chg01");
        g_pScriptMng->AddSelDlg(1, 63, "PlayPhoneMusic", "xx_field_gxt01");
        g_pScriptMng->AddSelDlg(1, 64, "PlayPhoneMusic", "xx_field_htzd01");
    }
    if (iPar == 8) {
        g_pScriptMng->AddSelDlg(1, 65, "PlayPhoneMusic", "xx_field_lg01");
        g_pScriptMng->AddSelDlg(1, 66, "PlayPhoneMusic", "xx_field_lg02");
        g_pScriptMng->AddSelDlg(1, 67, "PlayPhoneMusic", "xx_field_main");
        g_pScriptMng->AddSelDlg(1, 68, "PlayPhoneMusic", "xx_field_night01");
        g_pScriptMng->AddSelDlg(1, 69, "PlayPhoneMusic", "xx_field_night02");
        g_pScriptMng->AddSelDlg(1, 70, "PlayPhoneMusic", "xx_fight_envir07");
        g_pScriptMng->AddSelDlg(1, 71, "PlayPhoneMusic", "xx_fight_envir08");
        g_pScriptMng->AddSelDlg(1, 72, "PlayPhoneMusic", "xx_fight_envir09");
    }
    if (iPar == 9) {
        g_pScriptMng->AddSelDlg(1, 73, "PlayPhoneMusic", "xx_fight_folk04");
        g_pScriptMng->AddSelDlg(1, 74, "PlayPhoneMusic", "xx_fight_folk05");
        g_pScriptMng->AddSelDlg(1, 75, "PlayPhoneMusic", "xx_fight_folk06");
        g_pScriptMng->AddSelDlg(1, 76, "PlayPhoneMusic", "xx_fight_perc01");
        g_pScriptMng->AddSelDlg(1, 77, "PlayPhoneMusic", "xx_fight_perc02");
        g_pScriptMng->AddSelDlg(1, 78, "PlayPhoneMusic", "xx_fight_perc03");
        g_pScriptMng->AddSelDlg(1, 79, "PlayPhoneMusic", "xx_fight_theme10");
        g_pScriptMng->AddSelDlg(1, 80, "PlayPhoneMusic", "xx_fight_xmzc01");
    }
    if (iPar == 10) {
        g_pScriptMng->AddSelDlg(1, 81, "PlayPhoneMusic", "xx_football");
        g_pScriptMng->AddSelDlg(1, 82, "PlayPhoneMusic", "xx_fulingshan_01");
        g_pScriptMng->AddSelDlg(1, 83, "PlayPhoneMusic", "xx_fyxiang_01");
        g_pScriptMng->AddSelDlg(1, 84, "PlayPhoneMusic", "xx_gaojiazhuang_01");
        g_pScriptMng->AddSelDlg(1, 85, "PlayPhoneMusic", "xx_gaolaozhuang_01");
        g_pScriptMng->AddSelDlg(1, 86, "PlayPhoneMusic", "xx_gjzxgfb_01");
        g_pScriptMng->AddSelDlg(1, 87, "PlayPhoneMusic", "xx_gjzxgfb_02");
        g_pScriptMng->AddSelDlg(1, 88, "PlayPhoneMusic", "xx_gjzxgfb_03");
    }
    if (iPar == 11) {
        g_pScriptMng->AddSelDlg(1, 89, "PlayPhoneMusic", "xx_hongshuiyan_01");
        g_pScriptMng->AddSelDlg(1, 90, "PlayPhoneMusic", "xx_hunpo_01");
        g_pScriptMng->AddSelDlg(1, 91, "PlayPhoneMusic", "xx_linggu_01");
        g_pScriptMng->AddSelDlg(1, 92, "PlayPhoneMusic", "xx_linggu_02");
        g_pScriptMng->AddSelDlg(1, 93, "PlayPhoneMusic", "xx_longgong_01");
        g_pScriptMng->AddSelDlg(1, 94, "PlayPhoneMusic", "xx_main");
        g_pScriptMng->AddSelDlg(1, 95, "PlayPhoneMusic", "xx_pdh_01");
        g_pScriptMng->AddSelDlg(1, 96, "PlayPhoneMusic", "xx_pdh_02");
    }
    if (iPar == 12) {
        g_pScriptMng->AddSelDlg(1, 97, "PlayPhoneMusic", "xx_pingyuanxian_01");
        g_pScriptMng->AddSelDlg(1, 98, "PlayPhoneMusic", "xx_scene_beaming");
        g_pScriptMng->AddSelDlg(1, 99, "PlayPhoneMusic", "xx_scene_spring");
        g_pScriptMng->AddSelDlg(1, 100, "PlayPhoneMusic", "xx_scene_wedding");
        g_pScriptMng->AddSelDlg(1, 101, "PlayPhoneMusic", "xx_taihaoshan_01");
        g_pScriptMng->AddSelDlg(1, 102, "PlayPhoneMusic", "xx_temple_day");
        g_pScriptMng->AddSelDlg(1, 103, "PlayPhoneMusic", "xx_temple_main");
        g_pScriptMng->AddSelDlg(1, 104, "PlayPhoneMusic", "xx_temple_night");
    }
    if (iPar == 13) {
        g_pScriptMng->AddSelDlg(1, 105, "PlayPhoneMusic", "xx_xiaofz_01");
        g_pScriptMng->AddSelDlg(1, 106, "PlayPhoneMusic", "xx_xiaoyuegu_01");
        g_pScriptMng->AddSelDlg(1, 107, "PlayPhoneMusic", "xx_yamen_01");
        g_pScriptMng->AddSelDlg(1, 108, "PlayPhoneMusic", "xx_yunzhaidong_01");
        g_pScriptMng->AddSelDlg(1, 109, "PlayPhoneMusic", "xx_zdcjcb_01");
        g_pScriptMng->AddSelDlg(1, 110, "PlayPhoneMusic", "xx_zdcjcb_02");
        g_pScriptMng->AddSelDlg(1, 111, "PlayPhoneMusic", "xx_zhuyaodong_01");
        g_pScriptMng->AddSelDlg(1, 112, "PlayPhoneMusic", "xx_zy_main");
    }
    if (iPar > 0) g_pScriptMng->AddSelDlg(50073, iPar - 1, "SelMusic");
    if (iPar < 13) g_pScriptMng->AddSelDlg(50074, iPar + 1, "SelMusic");
    g_pScriptMng->AddSelDlg(50069, 1000 + iPar, "PlayPhoneMusic");
    g_pScriptMng->AddSelDlg(PHONE_BACK, 0, "SelBack");
    g_pScriptMng->EndDlg(iPlayerGID, 0);
}

void PlayPhoneMusic(int iPar, int iScnID, int iExcelID, int iPlayerGID)
{
    if (G_GetPlayer(iPlayerGID) == NULL) return;
    if (iPar >= 1 && iPar <= 112) {
        g_pScriptMng->OutputMsg(50070, 0, SENDMODE_TO_ONEPLAYER, iPlayerGID, iPar);
        SelMusic((iPar - 1) / 8, iScnID, iExcelID, iPlayerGID);
    } else if (iPar >= 1000 && iPar < 1014) {
        g_pScriptMng->OutputMsg(50072, 0, SENDMODE_TO_ONEPLAYER, iPlayerGID);
        SelMusic(iPar - 1000, iScnID, iExcelID, iPlayerGID);
    }
}
