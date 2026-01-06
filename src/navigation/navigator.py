from typing import List, Tuple
import heapq
import cv2
import numpy as np
import random

class Navigator:
    def __init__(self, grid_size: Tuple[int, int]):
        self.grid_size = grid_size

    def a_star(self, start: Tuple[int, int], goal: Tuple[int, int], cost_map: np.ndarray) -> List[Tuple[int, int]]:
        open_set = []
        heapq.heappush(open_set, (0, start))
        came_from = {}
        g_score = {start: 0}
        f_score = {start: self.heuristic(start, goal)}
        while open_set:
            _, current = heapq.heappop(open_set)
            if current == goal:
                return self.reconstruct_path(came_from, current)
            for neighbor in self.get_neighbors(current):
                tentative_g = g_score[current] + cost_map[neighbor]
                if tentative_g < g_score.get(neighbor, float('inf')):
                    came_from[neighbor] = current
                    g_score[neighbor] = tentative_g
                    f_score[neighbor] = tentative_g + self.heuristic(neighbor, goal)
                    heapq.heappush(open_set, (f_score[neighbor], neighbor))
        return []

    def heuristic(self, a: Tuple, b: Tuple) -> float:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def get_neighbors(self, pos: Tuple) -> List[Tuple]:
        dirs = [(-1,0), (1,0), (0,-1), (0,1)]
        neighbors = []
        for dx, dy in dirs:
            nx, ny = pos[0] + dx, pos[1] + dy
            if 0 <= nx < self.grid_size[0] and 0 <= ny < self.grid_size[1]:
                neighbors.append((nx, ny))
        return neighbors

    def reconstruct_path(self, came_from: Dict, current: Tuple) -> List[Tuple]:
        path = [current]
        while current in came_from:
            current = came_from[current]
            path.append(current)
        return path[::-1]

    def detect_stuck(self, history_pos: List[Tuple], waypoint: Tuple, ticks: int) -> bool:
        if len(history_pos) < 10:
            return False
        dists = [np.linalg.norm(np.array(history_pos[i]) - np.array(waypoint)) for i in range(-10,0)]
        if all(d > dists[0] - 5 for d in dists) and ticks > 20:
            return True
        return False

    def recovery(self, current: Tuple) -> Tuple:
        return (current[0] + random.randint(-5,5), current[1] + random.randint(-5,5))