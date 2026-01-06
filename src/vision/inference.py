import onnxruntime as ort
import cv2
from typing import List

class VisionInference:
    def __init__(self, model_path: str, classes: List[str]):
        self.sess = ort.InferenceSession(model_path, providers=['CUDAExecutionProvider'])
        self.classes = classes

    def classify(self, img: cv2.Mat) -> str:
        pre = cv2.resize(img, (224,224)) / 255.0
        input = pre.astype(np.float32)[np.newaxis, ...].transpose(0,3,1,2)
        output = self.sess.run(None, {'input': input})[0]
        return self.classes[np.argmax(output)]

    def detect(self, frame: cv2.Mat) -> List:
        # YOLO inference
        pre = cv2.resize(frame, (640,640)) / 255.0
        input = pre.transpose(2,0,1)[np.newaxis,...]
        dets = self.sess.run(None, {'input': input})[0]
        return dets  # Postprocess boxes

    def track(self, dets: List) -> List:
        # ByteTrack o OC-SORT impl
        pass