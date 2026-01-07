from typing import List, Tuple, Dict
import numpy as np
import heapq
import random


class Navigator:
    def __init__(self, grid_size: Tuple[int, int] = (50, 50)):
        self.grid_size = grid_size
        self.current_path: List[Tuple[int, int]] = []
        self.waypoints: List[Tuple[int, int]] = []
        self.stuck_history: List[Tuple[int, int]] = []
        self.stuck_threshold = 10

    def load_waypoints(self, route_json: str):
        import json
        with open(route_json, 'r') as f:
            data = json.load(f)
        self.waypoints = [(p['x'], p['y']) for p in data if 'x' in p and 'y' in p]

    def a_star(self, start: Tuple[int, int], goal: Tuple[int, int]) -> List[Tuple[int, int]]:
        def heuristic(a: Tuple[int, int], b: Tuple[int, int]) -> int:
            return abs(a[0] - b[0]) + abs(a[1] - b[1])
        open_set = [(0, start)]
        came_from: Dict[Tuple[int, int], Tuple[int, int]] = {}
        g_score = {start: 0}
        f_score = {start: heuristic(start, goal)}
        while open_set:
            _, current = heapq.heappop(open_set)
            if current == goal:
                path = []
                while current in came_from:
                    path.append(current)
                    current = came_from[current]
                path.append(start)
                return path[::-1]
            for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                neighbor = (current[0] + dx, current[1] + dy)
                if 0 <= neighbor[0] < self.grid_size[0] and 0 <= neighbor[1] < self.grid_size[1]:
                    tent_g = g_score[current] + 1
                    if tent_g < g_score.get(neighbor, float('inf')):
                        came_from[neighbor] = current
                        g_score[neighbor] = tent_g
                        f_score[neighbor] = tent_g + heuristic(neighbor, goal)
                        heapq.heappush(open_set, (f_score[neighbor], neighbor))
        return []

    def update(self, player_pos: Tuple[int, int], minimap_crop: np.ndarray):
        self.stuck_history.append(player_pos)
        if len(self.stuck_history) > self.stuck_threshold:
            self.stuck_history.pop(0)
        if len(self.stuck_history) >= self.stuck_threshold:
            max_x = max(self.stuck_history, key=lambda p: p[0])[0]
            min_x = min(self.stuck_history, key=lambda p: p[0])[0]
            if max_x - min_x < 5:
                # Stuck recovery
                player_pos = (
                    player_pos[0] + random.randint(-5, 5),
                    player_pos[1] + random.randint(-5, 5)
                )
        if self.waypoints:
            goal = self.waypoints[0]
            if np.linalg.norm(np.array(player_pos) - np.array(goal)) < 5:
                self.waypoints.pop(0)
            else:
                self.current_path = self.a_star(player_pos, goal)

    def get_next_move(self) -> str:
        if self.current_path and len(self.current_path) > 1:
            dx = self.current_path[1][0] - self.current_path[0][0]
            dy = self.current_path[1][1] - self.current_path[0][1]
            if dx > 0:
                return 'right'
            if dx < 0:
                return 'left'
            if dy > 0:
                return 'down'
            if dy < 0:
                return 'up'
        return 'wait'
