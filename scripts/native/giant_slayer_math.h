#ifndef GMTOOL_GIANT_SLAYER_MATH_H
#define GMTOOL_GIANT_SLAYER_MATH_H

/* Integer-only helpers; the effect is not active until a verified hook calls them. */
static int gm_giant_scale_damage(int damage, int attacker_max_hp, int target_max_hp) {
    if (damage <= 0 || attacker_max_hp <= 0 || target_max_hp <= 0) return damage;
    unsigned int multiple = (unsigned int)target_max_hp / (unsigned int)attacker_max_hp;
    if (multiple <= 1) return damage;
    unsigned long long value = (unsigned int)damage;
    value += ((unsigned long long)(unsigned int)damage * (multiple - 1)) / 20;
    return value > 2147483647ULL ? 2147483647 : (int)value;
}

static int gm_giant_effective_defense(int defense) {
    if (defense <= 0) return defense;
    return (int)(((unsigned long long)(unsigned int)defense * 7) / 10);
}
/* Apply 30% of original defense as additional ignore for this calculation. */
static int gm_giant_attack_ignore(int defense, int original_ignore) {
    if (defense <= 0) return original_ignore;
    long long result = (long long)original_ignore + ((long long)defense * 3 / 10);
    if (result > 2147483647LL) return 2147483647;
    if (result < (-2147483647LL - 1)) return (-2147483647 - 1);
    return (int)result;
}
#endif
