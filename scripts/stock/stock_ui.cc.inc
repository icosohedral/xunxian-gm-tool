
int stock_selected_qty = 1;

void on_stock_qty_click(ui_control* btn)
{
    int qty = btn->name().to_int();
    if (qty != 1 && qty != 10 && qty != 100) return;
    stock_selected_qty = qty;
    ui_string label;
    label.format(L"当前选择：%d 股", qty);
    <ui_label*>(ui.find_control(L"group:shop:stock")->deep_find_child(L"stock_qty_display"))->set_text(label);
}

void on_stock_refresh_click(ui_control*)
{
    ui_log().format(L"stock_diag refresh clicked; sending script packet 1224");
    G_GetGameMng()->GetNet()->SendScriptPacket(1224, 0, 0, 0, 91827364);
    ui_log().format(L"stock_diag script packet call returned");
}

void on_stock_buy_click(ui_control* btn)
{
    int stock = btn->parent()->name().to_int();
    if (stock < 1 || stock > 3) return;
    G_GetGameMng()->GetNet()->SendScriptPacket(1224, 1, stock, stock_selected_qty, 91827364);
}

void on_stock_sell_click(ui_control* btn)
{
    int stock = btn->parent()->name().to_int();
    if (stock < 1 || stock > 3) return;
    G_GetGameMng()->GetNet()->SendScriptPacket(1224, 2, stock, stock_selected_qty, 91827364);
}

ui_string stock_money_string(int copper)
{
    ui_string result;
    result.format(L"%d砖%d金%d银%d铜", copper / 1000000,
                  copper / 10000 % 100, copper / 100 % 100, copper % 100);
    return result;
}

void stock_recv(const wchar_t* message)
{
    ui_log().format(L"stock_diag reply received: %s", message);
    variant values = variant(ui_string(message)).split_to_array(L'|');
    if (values.size() != 10 || values.get(0).to_string() != L"@@STOCK") return;
    ui_control* page = ui.find_control(L"group:shop:stock");
    if (page == NULL) return;
    for (int stock = 1; stock <= 3; ++stock)
    {
        ui_string price_name, shares_name, price_text, shares_text;
        price_name.format(L"price_%d", stock);
        shares_name.format(L"shares_%d", stock);
        int price = values.get(stock + 1).to_int();
        int shares = values.get(stock + 4).to_int();
        price_text.format(L"报价：%s / 股", stock_money_string(price).c_str());
        shares_text.format(L"持有：%d 股", shares);
        <ui_label*>(page->deep_find_child(price_name))->set_text(price_text);
        <ui_label*>(page->deep_find_child(shares_name))->set_text(shares_text);
    }
    ui_string balance;
    balance.format(L"流通金：%s", stock_money_string(values.get(8).to_int()).c_str());
    <ui_label*>(page->deep_find_child(L"stock_balance"))->set_text(balance);
    int result = values.get(9).to_int();
    ui_string status = L"报价已刷新";
    if (result == 1) status = L"买入成功";
    else if (result == 2) status = L"卖出成功";
    else if (result == -1) status = L"无效的交易请求";
    else if (result == -2) status = L"持仓数据异常，交易已拒绝";
    else if (result == -3) status = L"交易金额超出上限";
    else if (result == -4) status = L"流通金不足或持仓已达上限";
    else if (result == -5) status = L"扣款未成功，交易已取消";
    else if (result == -6) status = L"持仓不足或流通金即将超出上限";
    <ui_label*>(page->deep_find_child(L"stock_status"))->set_text(status);
}
