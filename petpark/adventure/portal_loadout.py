"""Read-only web presentation of the same loadout used by cultivator cards."""
from . import content as c
from .card import ASSETS, GEAR_ASSET_PREFIX, portrait_name


def asset_url(name):
    extension = 'webp' if (ASSETS / f'{name}.webp').is_file() else 'png'
    return f'/cultivator_assets/{name}.{extension}'


def loadout_summary(adventure):
    profession = adventure.get('profession', '剑修')
    prefix = GEAR_ASSET_PREFIX.get(profession, 'sword')
    equipment = adventure.get('equipment') or {}
    tiers = adventure.get('equip_tier') or {}
    affixes = adventure.get('equip_affix') or {}
    slots = []
    for slot, attribute in c.GEAR.values():
        tier = tiers.get(slot, 0)
        affix = affixes.get(slot)
        bonus = c.affix_bonus(affix)
        slots.append({
            'slot': slot, 'name': c.gear_name(profession, slot, tier),
            'level': equipment.get(slot, 0), 'attribute': attribute,
            'affix': f'{affix} {bonus}' if bonus else '未觉醒词条',
            'image_url': asset_url(f'{prefix}-{slot}'),
        })
    return {'portrait_url': asset_url(portrait_name(adventure)), 'equipment': slots}
