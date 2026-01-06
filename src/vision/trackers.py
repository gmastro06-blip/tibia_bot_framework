from typing import List, Dict
import numpy as np

class ByteTrack:
    def __init__(self):
        self.tracks: Dict[int, Dict] = {}
        self.track_id = 0

    def update(self, dets: List[np.ndarray]) -> List[Dict]:
        updated_tracks = []
        for det in dets:
            # Simple matching, kalman filter placeholder
            track = {'id': self.track_id, 'bbox': det}
            self.tracks[self.track_id] = track
            updated_tracks.append(track)
            self.track_id += 1
        return updated_tracks