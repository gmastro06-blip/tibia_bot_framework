from typing import List, Tuple
import heapq
import cv2
import numpy as np

class Navigator:
    def __init__(self, grid_size: Tuple[int, int] = (50, 50)):  # Minimap grid
        self.grid_size = grid_size

    def a_star(self, start: Tuple[int, int], goal: Tuple[int, int], cost_map: np.ndarray) -> List[Tuple[int, int]]:
        def heuristic(a: Tuple[int, int], b: Tuple[int, int]) -> int:
            return abs(a[0] - b[0]) + abs(a[1] - b[1])
        open_set = [(0, start)]
        came_from: dict[Tuple[int, int], Tuple[int, int]] = {}  # Hint
        g_score = {start: 0}
        f_score = {start: heuristic(start, goal)}
        while open_set:
            _, current = heapq.heappop(open_set)
            if current == goal:
                path = []
                while current in came_from:
                    path.append(current)
                    current = came_from[current]
                return path[::-1]
            for dx, dy in [(-1,0), (1,0), (0,-1), (0,1)]:
                neighbor = (current[0] + dx, current[1] + dy)
                if 0 <= neighbor[0] < self.grid_size[0] and 0 <= neighbor[1] < self.grid_size[1]:
                    tent_g = g_score[current] + cost_map[neighbor]
                    if tent_g < g_score.get(neighbor, float('inf')):
                        came_from[neighbor] = current
                        g_score[neighbor] = tent_g
                        f_score[neighbor] = tent_g + heuristic(neighbor, goal)
                        heapq.heappush(open_set, (f_score[neighbor], neighbor))
        return []

    def replan(self, current_pos: Tuple[int, int], waypoint: Tuple[int, int], minimap_crop: cv2.Mat | None) -> List[Tuple[int, int]]:  # Noneable
        # Segment walkable si viable (e.g., color != black blocked)
        cost_map = np.ones(self.grid_size)  # Placeholder, 1 walkable
        return self.a_star(current_pos, waypoint, cost_map)

    def detect_stuck(self, pos_history: List[Tuple[int, int]], threshold: int = 5) -> bool:
        if len(pos_history) < threshold:
            return False
        dists = [np.linalg.norm(np.array(pos_history[i]) - np.array(pos_history[i+1])) for i in range(threshold-1)]
        return bool(sum(dists) < 1)  # Explícito bool

    def recover_stuck(self, current_path: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
        # Micro-moves, backtrack
        return current_path[:-2] + [(current_path[-1][0]+1, current_path[-1][1])]  # Ejemplo