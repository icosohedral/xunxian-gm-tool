// 商城股票交易。客户端仅发送操作、股票 ID 和股数；价格与结算均在服务端计算。
#include "inc/exceldef.h"
#include "inc/senddef.h"
#include "inc/globaldef.h"

#define STOCK_SCRIPT_ID 2501
#define STOCK_REPLY 50030
#define STOCK_FLAG_1 200
#define STOCK_FLAG_2 201
#define STOCK_FLAG_3 202
#define STOCK_MAX_SHARES 1000
#define STOCK_MAX_MONEY 2147483647

int StockFlag(int stock)
{
    if (stock == 1) return STOCK_FLAG_1;
    if (stock == 2) return STOCK_FLAG_2;
    if (stock == 3) return STOCK_FLAG_3;
    return 0;
}

int StockSlot()
{
    CPGTime now = G_GetCurrentTime();
    int day = (now.GetYear() * 372 + now.GetMonth() * 31 + now.GetDay());
    return day * 288 + now.GetHour() * 12 + now.GetMinute() / 5;
}

int StockPrice(int stock, int slot)
{
    int base = stock == 1 ? 1000000 : (stock == 2 ? 3000000 : 5000000);
    int seed = (slot % 100000) * 7919 + stock * 104729;
    int fluctuation = seed % 60001;
    fluctuation -= 30000; // -3% ~ +3%, fixed for the five-minute slot.
    return base + (base / 1000) * fluctuation / 1000;
}

void StockReply(CPlayerDataMngS* player, int gid, int slot, int result)
{
    if (player == NULL) return;
    G_Printf("stock_reply gid=%d slot=%d result=%d\n", gid, slot, result);
    g_pScriptMng->OutputMsg(STOCK_REPLY, 0, SENDMODE_TO_ONEPLAYER, gid,
        slot, StockPrice(1, slot), StockPrice(2, slot), StockPrice(3, slot),
        player->GetDWordFlag(STOCK_FLAG_1), player->GetDWordFlag(STOCK_FLAG_2),
        player->GetDWordFlag(STOCK_FLAG_3), player->GetAtb(PlayerAtb_Money), result);
}

// RunTest 给 Run 传入：场景 ID、玩家 GID、客户端四个整数。
// op: 0 查询，1 买入，2 卖出；stock: 1..3；qty: 1/10/100。
void Run(int scene, int gid, int op, int stock, int qty, int unused)
{
    G_Printf("stock_request gid=%d op=%d stock=%d qty=%d\n", gid, op, stock, qty);
    CPlayerUnitS* unit = G_GetPlayer(gid);
    if (unit == NULL || unit->GetDataMng() == NULL) return;
    CPlayerDataMngS* player = unit->GetDataMng();
    int slot = StockSlot();
    if (op == 0) { StockReply(player, gid, slot, 0); return; }
    int flag = StockFlag(stock);
    if ((op != 1 && op != 2) || flag == 0 ||
        (qty != 1 && qty != 10 && qty != 100)) {
        StockReply(player, gid, slot, -1); return;
    }
    int oldShares = player->GetDWordFlag(flag);
    if (oldShares < 0 || oldShares > STOCK_MAX_SHARES) {
        StockReply(player, gid, slot, -2); return;
    }
    int price = StockPrice(stock, slot);
    if (price <= 0 || qty > STOCK_MAX_MONEY / price) {
        StockReply(player, gid, slot, -3); return;
    }
    int amount = price * qty;
    int balance = player->GetAtb(PlayerAtb_Money);
    if (op == 1) {
        if (oldShares > STOCK_MAX_SHARES - qty || balance < amount ||
            !player->TrySubMoney(-amount, EBOUNDMONEY_TRADE)) {
            StockReply(player, gid, slot, -4); return;
        }
        if (!player->SubMoney(-amount, G_IntToInt64(0), Log_MnyLost_Script,
                              0, NULL, EBOUNDMONEY_TRADE)) {
            StockReply(player, gid, slot, -5); return;
        }
        player->SetDWordFlag(flag, oldShares + qty);
        StockReply(player, gid, slot, 1);
    } else {
        if (oldShares < qty || balance < 0 ||
            balance > STOCK_MAX_MONEY - amount ||
            !player->TryAddMoney(amount, 0, EBOUNDMONEY_TRADE)) {
            StockReply(player, gid, slot, -6); return;
        }
        player->AddMoney(amount, G_IntToInt64(0), 0, 0,
                         "stock_trade", EBOUNDMONEY_TRADE);
        player->SetDWordFlag(flag, oldShares - qty);
        StockReply(player, gid, slot, 2);
    }
}
