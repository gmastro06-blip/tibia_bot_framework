import sys
import json
import re
from pathlib import Path

# Usage: python tools/import_route_from_in.py scripts-master/bonelord_liberty_bay/waypoints.in configs/route.json

def parse_waypoint_line(line):
    # Remove comments and whitespace
        line = line.strip()
        if not line or line.startswith('#'):
            return None
        # Label
        m = re.match(r'label (.+)', line)
        if m:
            return {'type': 'label', 'label': m.group(1), 'raw_line': line}
        # Stand/node/rope/shovel
        m = re.match(r'(stand|node|rope|shovel) \((\d+), ?(\d+), ?(\d+)\)', line)
        if m:
            return {
                'type': m.group(1),
                'x': int(m.group(2)),
                'y': int(m.group(3)),
                'z': int(m.group(4)),
                'raw_line': line
            }
        # Action
        m = re.match(r'action (.+)', line)
        if m:
            return {'type': 'action', 'action': m.group(1), 'raw_line': line}
        # Call/load (with conditional_jump support)
        m = re.match(r'(call|load) (.+)', line)
        if m:
            # Special: conditional_jump_script_options
            cond = re.match(r'conditional_jump_script_options\((.*)\)', m.group(2))
            if cond:
                # Parse args like "var_name":"hunt_down", "label_jump":"hunt_down", ...
                args = {}
                for kv in re.findall(r'"([^"]+)":"([^"]+)"', cond.group(1)):
                    args[kv[0]] = kv[1]
                return {'type': 'conditional_jump', 'args': args, 'raw_line': line}
            return {'type': m.group(1), 'target': m.group(2), 'raw_line': line}
        # Fallback: unknown line
        return {'type': 'unknown', 'raw_line': line}

def parse_in_file(path):
    waypoints = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            wp = parse_waypoint_line(line)
            if wp:
                waypoints.append(wp)
    return waypoints

def convert_to_bot_route(waypoints):
    # Convert to bot's expected format: list of dicts with x, y, z, action, label, etc.
        route = []
        for wp in waypoints:
            entry = {'raw_line': wp.get('raw_line', '')}
            if 'x' in wp and 'y' in wp and 'z' in wp:
                entry['x'] = wp['x']
                entry['y'] = wp['y']
                entry['z'] = wp['z']
            if wp['type'] in {'stand', 'node', 'rope', 'shovel'}:
                entry['type'] = wp['type']
            if wp['type'] == 'action':
                entry['action'] = wp['action']
            if wp['type'] == 'label':
                entry['label'] = wp['label']
            if wp['type'] == 'call':
                entry['call'] = wp['target']
            if wp['type'] == 'load':
                entry['load'] = wp['target']
            if wp['type'] == 'conditional_jump':
                entry['conditional_jump'] = wp['args']
            if wp['type'] == 'unknown':
                entry['comment'] = 'Unrecognized line from .in file'
            route.append(entry)
        return route

def main():
    if len(sys.argv) < 3:
        print('Usage: python tools/import_route_from_in.py <input.in> <output.json>')
        sys.exit(1)
    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    waypoints = parse_in_file(in_path)
    route = convert_to_bot_route(waypoints)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(route, f, indent=2, ensure_ascii=False)
    print(f'Converted {len(route)} waypoints to {out_path}')

if __name__ == '__main__':
    main()
