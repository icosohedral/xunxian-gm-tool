/* Version-pinned TTYGZS_d.so merchant cache refresh (32-bit i386 only).
 * The caller must run this on the stopped game thread after tables 50/95 reload.
 * No game process is modified merely by compiling this file.
 */
typedef unsigned int u32;
typedef unsigned char u8;
typedef struct Link { struct Link *next, *prev; } Link;
typedef struct Node { Link link; void *value; } Node;
typedef struct Vec { int *begin, *end, *capacity; } Vec;
typedef struct Entry { Node *link; void *product; int restore; } Entry;
typedef struct Plan { u32 id; void *merchant; unsigned count; Entry entries[1024]; } Plan;

extern void *malloc(unsigned);
extern void free(void *);

static const u32 merchant_ids[5] = {238, 239, 283, 284, 285};
static Plan plans[5];
static int prepare_only;

static void *find_merchant(u8 *manager, u32 id) {
    u8 *node = *(u8 **)(manager + 12);
    unsigned limit = 0;
    while (node && limit++ < 100) {
        u32 key = *(u32 *)(node + 16);
        if (key == id) return *(void **)(node + 20);
        node = *(u8 **)(node + (id < key ? 8 : 12));
    }
    return 0;
}

static int valid_list(Link *head, unsigned limit) {
    Link *p = head->next, *previous = head;
    unsigned count = 0;
    if (!p || !head->prev) return 0;
    while (p != head) {
        if (!p || p->prev != previous || ++count > limit) return 0;
        previous = p;
        p = p->next;
    }
    return head->prev == previous;
}

static void unlink_node(Link *node) {
    node->prev->next = node->next;
    node->next->prev = node->prev;
}

static void append_node(Link *head, Link *node) {
    node->next = head;
    node->prev = head->prev;
    head->prev->next = node;
    head->prev = node;
}

static void destroy_plan(Plan *plan, void (*destroy_product)(void *)) {
    unsigned j;
    for (j = 0; j < plan->count; ++j) {
        if (plan->entries[j].product) {
            destroy_product(plan->entries[j].product);
            free(plan->entries[j].product);
        }
        free(plan->entries[j].link);
        plan->entries[j].product = 0;
        plan->entries[j].link = 0;
    }
    plan->count = 0;
}

/* Read-only table/manager probe; returns desired product references. */
__attribute__((visibility("default"))) int gmtool_probe(unsigned base) {
    u8 *image = (u8 *)base;
    u8 *manager = image + 0x1198620;
    void *table_manager = image + 0x11bb664;
    void *(*get_table)(void *, int) = (void *(*)(void *, int))(image + 0xc58eec);
    void *(*get_line)(void *, int) = (void *(*)(void *, int))(image + 0xc5935a);
    void (*get_array)(void *, int, Vec *, int) = (void (*)(void *, int, Vec *, int))(image + 0xc5c75a);
    void *merchants, *products;
    unsigned i, j, total = 0;
    if (!base || *(u32 *)(manager + 24) < 5 || *(u32 *)(manager + 24) > 4096)
        return -1;
    merchants = get_table(table_manager, 95);
    products = get_table(table_manager, 50);
    if (!merchants || !products) return -2;
    for (i = 0; i < 5; ++i) {
        Vec ids = {0, 0, 0};
        void *merchant = find_merchant(manager, merchant_ids[i]);
        void *line = get_line(merchants, merchant_ids[i]);
        if (!merchant || !line || !valid_list((Link *)((u8 *)merchant + 8), 4096))
            return -3;
        get_array(line, 2, &ids, 0);
        if (!ids.begin || ids.end < ids.begin ||
                ids.end - ids.begin > 1024) {
            free(ids.begin);
            return -4;
        }
        for (j = 0; j < (unsigned)(ids.end - ids.begin); ++j) {
            if (!get_line(products, ids.begin[j])) { free(ids.begin); return -5; }
            ++total;
        }
        free(ids.begin);
    }
    return (int)total;
}

/* Return 0 on success; negative values identify preparation failures. */
__attribute__((visibility("default"))) int gmtool_refresh(unsigned base) {
    u8 *image = (u8 *)base;
    u8 *manager = image + 0x1198620;
    void *table_manager = image + 0x11bb664;
    void *(*get_table)(void *, int) = (void *(*)(void *, int))(image + 0xc58eec);
    void *(*get_line)(void *, int) = (void *(*)(void *, int))(image + 0xc5935a);
    void (*get_array)(void *, int, Vec *, int) = (void (*)(void *, int, Vec *, int))(image + 0xc5c75a);
    int (*get_int)(void *, int, int) = (int (*)(void *, int, int))(image + 0xc5ca14);
    void (*construct_product)(void *) = (void (*)(void *))(image + 0x747e00);
    void (*destroy_product)(void *) = (void (*)(void *))(image + 0x747d28);
    u8 (*initialize_product)(void *, int, void *, int) =
        (u8 (*)(void *, int, void *, int))(image + 0x74ea22);
    void (*add_restore)(void *, void *) = (void (*)(void *, void *))(image + 0x754e1a);
    void *merchant_table, *product_table;
    Link *restore = (Link *)(manager + 0x1c);
    unsigned i, j, k, built = 0;
    int status = -1;

    if (!base || *(u32 *)(manager + 24) < 5 || *(u32 *)(manager + 24) > 4096)
        return -1;
    merchant_table = get_table(table_manager, 95);
    product_table = get_table(table_manager, 50);
    if (!merchant_table || !product_table || !valid_list(restore, 100000))
        return -2;
    for (i = 0; i < 5; ++i) {
        Plan *plan = &plans[i];
        void *line;
        Vec ids = {0, 0, 0};
        unsigned desired_count;
        plan->id = merchant_ids[i];
        plan->count = 0;
        plan->merchant = find_merchant(manager, plan->id);
        if (!plan->merchant || *(u32 *)plan->merchant != plan->id ||
                !valid_list((Link *)((u8 *)plan->merchant + 8), 4096)) {
            status = -3;
            goto fail;
        }
        line = get_line(merchant_table, plan->id);
        if (!line) { status = -4; goto fail; }
        get_array(line, 2, &ids, 0);
        if (!ids.begin || ids.end < ids.begin || ids.end - ids.begin > 1024) {
            free(ids.begin);
            status = -4;
            goto fail;
        }
        desired_count = (unsigned)(ids.end - ids.begin);
        for (j = 0; j < desired_count; ++j) {
            int id = ids.begin[j];
            void *product_line, *product;
            Node *link, *merchant_link;
            if (id <= 0) continue;
            for (k = 0; k < j; ++k)
                if (ids.begin[k] == id) { status = -6; break; }
            if (k != j) break;
            product_line = get_line(product_table, id);
            if (!product_line) continue;
            product = malloc(0xdc);
            link = malloc(sizeof(Node));
            if (!product || !link) {
                free(product);
                free(link);
                status = -8;
                break;
            }
            construct_product(product);
            if (!initialize_product(product, id, product_line, 0)) {
                destroy_product(product);
                free(product);
                free(link);
                continue;
            }
            merchant_link = malloc(sizeof(Node));
            if (!merchant_link) {
                destroy_product(product);
                free(product);
                free(link);
                status = -10;
                break;
            }
            merchant_link->value = (void *)plan->id;
            append_node((Link *)((u8 *)product + 0xd4), &merchant_link->link);
            link->value = product;
            plan->entries[plan->count].link = link;
            plan->entries[plan->count].product = product;
            plan->entries[plan->count].restore = get_int(product_line, 4, 1) != 0;
            ++plan->count;
            ++built;
        }
        free(ids.begin);
        if (j != desired_count) goto fail;
    }

    if (prepare_only) {
        for (i = 0; i < 5; ++i) destroy_plan(&plans[i], destroy_product);
        return (int)built;
    }

    /* All new products exist before the first live list is changed. */
    for (i = 0; i < 5; ++i) {
        Plan *plan = &plans[i];
        Link *head = (Link *)((u8 *)plan->merchant + 8);
        Link *p = head->next;
        while (p != head) {
            Link *next = p->next;
            Link *r = restore->next;
            while (r != restore) {
                Link *following = r->next;
                if (((Node *)r)->value == ((Node *)p)->value) {
                    unlink_node(r);
                    free(r);
                }
                r = following;
            }
            destroy_product(((Node *)p)->value);
            free(((Node *)p)->value);
            free(p);
            p = next;
        }
        head->next = head->prev = head;
        for (j = 0; j < plan->count; ++j) {
            append_node(head, &plan->entries[j].link->link);
            if (plan->entries[j].restore)
                add_restore(manager, plan->entries[j].product);
            plan->entries[j].link = 0;
            plan->entries[j].product = 0;
        }
        plan->count = 0;
    }
    return 0;

fail:
    for (i = 0; i < 5; ++i) destroy_plan(&plans[i], destroy_product);
    return status;
}

__attribute__((visibility("default"))) int gmtool_prepare(unsigned base) {
    int result;
    prepare_only = 1;
    result = gmtool_refresh(base);
    prepare_only = 0;
    return result;
}
