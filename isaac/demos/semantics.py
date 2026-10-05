"""Public issue-defined meanings and provisional engineering destinations."""
from copy import deepcopy

from isaac.commands.protocol import LEGAL_PAIRS
from isaac.workcell.layout import preconditions


ACTIONS = ('ADD_ONE', 'REMOVE_ONE', 'FLIP_CARD', 'ALIGN_ARROW', 'SCAN', 'TAG', 'CLOSE', 'QUARANTINE')
ORIENTATION_PAIRS = tuple((action, 'tray_A' if i < 4 else 'container_E') for i, action in enumerate(ACTIONS))


def consequence(layout, neutral, action, target):
    if (action, target) not in LEGAL_PAIRS:
        raise ValueError('Illegal action/target pair')
    if not next(row['possible'] for row in preconditions(layout, neutral)['pairs']
                if row['action'] == action and row['target'] == target):
        raise ValueError('Action precondition does not hold')
    expected = deepcopy(neutral)
    definitions = {item['id']: item for item in layout['objects']}
    group, primary, private_result = [], None, {}
    if action in ('ADD_ONE', 'REMOVE_ONE'):
        source = 'supply_cup' if action == 'ADD_ONE' else target
        candidates = [key for key, value in neutral.items()
                      if definitions[key]['kind'] == 'washer' and value['state']['location'] == source]
        # Pick a topmost washer first; stable name resolves ties.
        primary = sorted(candidates, key=lambda key: (-neutral[key]['position_m'][2], key))[0]
        group = [primary]
        destination = target if action == 'ADD_ONE' else 'return_cup'
        if action == 'ADD_ONE':
            existing = sorted(key for key in neutral if definitions[key]['kind'] == 'washer'
                              and neutral[key]['state']['location'] == target)
            p = list(neutral[existing[0]]['position_m'])
            p[2] += definitions[primary]['dimensions_m'][2]
        else:
            p = list(layout['anchors']['return_cup']['position_m'])
        expected[primary]['position_m'] = p
        expected[primary]['state']['location'] = destination
    elif action == 'FLIP_CARD':
        primary = target+'/card'
        expected[primary]['state']['card_face'] = 1-neutral[primary]['state']['card_face']
    elif action == 'ALIGN_ARROW':
        primary = target+'/arrow'
        expected[primary]['state']['arrow_angle_rad'] = 0.
    elif action == 'SCAN':
        primary = target+'/code'
        private_result = {'scanned_code': definitions[primary]['label']}
    elif action == 'TAG':
        primary, group = target+'/tag', [target+'/tag']
        center = neutral[target]['position_m']
        expected[primary]['position_m'] = [center[0], center[1]-.048, center[2]+.018]
        expected[primary]['state'] = dict(tag_attached=True, location=target+'/home')
    elif action == 'CLOSE':
        primary = target+'/lid'
        expected[primary]['state']['lid_open_fraction'] = 0.
    elif action == 'QUARANTINE':
        primary = target
        group = [target, target+'/lid', target+'/code']
        if neutral[target+'/tag']['state']['tag_attached']:
            group.append(target+'/tag')
        destination = 'quarantine_'+target[-1]
        delta = [b-a for a, b in zip(neutral[target]['position_m'], layout['anchors'][destination]['position_m'])]
        for key in group:
            expected[key]['position_m'] = [a+d for a, d in zip(neutral[key]['position_m'], delta)]
        expected[target]['state']['location'] = destination
        if target+'/tag' in group:
            expected[target+'/tag']['state']['location'] = destination
    return dict(action=action, target=target, primary=primary, carried_group=group,
                expected=expected, private_result=private_result)
