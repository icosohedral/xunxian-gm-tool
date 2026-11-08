
int phone_delivery_offset = 0;
int phone_delivery_total = 0;

ui_control* phone_delivery_panel()
{
    ui_control* talk = ui.find_control(L"group:quest:talk");
    if (talk == NULL) return NULL;
    return talk->deep_find_child(L"phone_delivery");
}

void phone_delivery_render()
{
    ui_control* panel = phone_delivery_panel();
    if (panel == NULL) return;
    ui_rich_box* search = <ui_rich_box*>(panel->deep_find_child(L"phone_search"));
    if (search == NULL) return;
    ui_string query = search->get_text();
    ui_string rows[6];
    int count = 0;
    ug_excel_table items = ug.excel(item_list);
    for (int i = 0, n = items.size(); i < n; ++i)
    {
        ug_excel_line item = items.get(i);
        if (!item.valid()) continue;
        ui_string name = item.get_string(ItemList_Name);
        if (name.empty() || (!query.empty() && name.find(query) == -1)) continue;
        if (count >= phone_delivery_offset && count < phone_delivery_offset + 6)
            rows[count - phone_delivery_offset].format(L"%d  %s", item.get_int(0), name.c_str());
        ++count;
    }
    phone_delivery_total = count;
    for (int i = 0; i < 6; ++i)
    {
        ui_string control_name;
        control_name.format(L"phone_item_%d", i);
        ui_label* row = <ui_label*>(panel->deep_find_child(control_name));
        if (row != NULL) row->set_text(rows[i]);
    }
    ui_string page;
    if (count == 0) page = L"无匹配物品";
    else page.format(L"%d / %d", phone_delivery_offset / 6 + 1, (count + 5) / 6);
    <ui_label*>(panel->deep_find_child(L"phone_page"))->set_text(page);
}

void phone_delivery_open()
{
    ui_control* panel = phone_delivery_panel();
    if (panel == NULL) return;
    phone_delivery_offset = 0;
    <ui_rich_box*>(panel->deep_find_child(L"phone_search"))->set_text(L"");
    panel->visible(true);
    phone_delivery_render();
}

void phone_delivery_hide()
{
    ui_control* panel = phone_delivery_panel();
    if (panel != NULL) panel->visible(false);
}

void phone_delivery_search(ui_control*)
{
    phone_delivery_offset = 0;
    phone_delivery_render();
}

void phone_delivery_key(ui_control*, short key)
{
    if (key == VK_RETURN) phone_delivery_search(NULL);
}

void phone_delivery_prev(ui_control*)
{
    if (phone_delivery_offset >= 6) phone_delivery_offset -= 6;
    phone_delivery_render();
}

void phone_delivery_next(ui_control*)
{
    if (phone_delivery_offset + 6 < phone_delivery_total)
        phone_delivery_offset += 6;
    phone_delivery_render();
}
