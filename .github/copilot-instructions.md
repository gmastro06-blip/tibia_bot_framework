# Tibia Bot Framework - AI Coding Assistant Guide

## Project Overview
A real-time computer vision bot framework for Tibia-like MMORPGs using threaded pipeline architecture: capture → vision → decision → action.

## Architecture & Data Flow
**Three-thread pipeline** ([src/main.py](../src/main.py)):
1. **Capture Thread**: `DXGICapture` → `frame_queue` (Win32 BitBlt with MSS fallback)
2. **Vision Thread**: ROI calibration → OCR/detection → `GameStateBuilder` → `gs_queue`
3. **Decision Thread**: `BehaviorTree.tick()` → `ActionExecutor` → `Navigator.get_next_move()`

**Queue pattern**: Use `Queue(maxsize=N)` with `.get_nowait()` to drop old frames when full - maintains real-time responsiveness.

## Critical Development Commands
```bash
# Run bot (requires Poetry environment)
poetry run python -m src.main

# Install dependencies
poetry install

# Run tests
poetry run pytest tests/

# Type checking (mypy configured with disable_error_code=["misc"])
poetry run mypy src/
```

## Screen Capture Implementation
**Fallback Capture Strategy**: When specific Tibia window not found, automatically captures full screen using MSS library. Prevents black/dummy frames and ensures real image data for all ROIs.

**Thread-Safe MSS Usage**: Creates new MSS instance per capture call to avoid multi-threading issues. Uses context manager (`with mss() as sct:`) for proper resource cleanup.

**Capture Validation**: Validates captured frames for content quality - checks for black images, uniform colors, color variation, and minimum resolution before processing.

**Multi-Monitor Detection**: Automatically detects and captures from individual monitors (1, 2, 3...) before falling back to combined screen (0). Prioritizes monitor 1 (primary) for optimal performance.

## Key Conventions

### Import Patterns
- Use `TYPE_CHECKING` imports to avoid circular dependencies (see [action/executor.py](../src/action/executor.py), [telemetry/replay.py](../src/telemetry/replay.py))
- Type hints use `Union[cv2.Mat, np.ndarray]` for OpenCV compatibility

### GameState Construction
- `GameStateBuilder` fuses OCR text with HSV bar analysis via median smoothing over 5-frame history
- Example: HP fusion checks if `|ocr_ratio - bar_ratio| >= 0.1` to prefer bar over OCR (src/gamestate/builder.py#L40-L43)

### Computer Vision Patterns
**ROI Calibration**: All regions-of-interest stored as normalized `{x, y, w, h}` floats (0.0-1.0) in [configs/rois_guess.json](../configs/rois_guess.json), converted to pixels by `UICalibrator.normalize_to_px()`

**HSV Segmentation**: Red HP bars require dual-range masking due to hue wraparound:
```python
# Pattern used in battlelist/extractor.py and calibration/ui_calibrator.py
mask_low = cv2.inRange(hsv, [0, 70, 50], [10, 255, 255])
mask_high = cv2.inRange(hsv, [170, 70, 50], [180, 255, 255])
mask_red = cv2.bitwise_or(mask_low, mask_high)
```

**OCR Preprocessing Pipeline** (src/vision/ocr.py#L13-L20):
1. Adaptive threshold → 2x upscale → denoise → dilate
2. EasyOCR with GPU + optional ONNX CRNN fallback
3. Regex strip non-whitelisted chars: `re.sub(r'[^0-9/]', '', text)`

### Bestiary Matcher
- Supports both dict `{"orc": {...}}` and list `[{"name_key": "orc"}]` registry formats (src/bestiary/matcher.py#L10-L38)
- Uses `rapidfuzz` with bucketing (first-char index) + `WRatio` for fuzzy matching
- Apply OCR corrections from [configs/ocr_corrections.json](../configs/ocr_corrections.json) before matching

### Behavior Trees
- Use `py_trees` with `Selector` root for priority-based decisions (decision/behavior_tree.py#L43-L51)
- Nodes access state via `bt.blackboard.Blackboard().get("gamestate")`
- Return `SUCCESS`/`FAILURE`/`RUNNING` to control tree traversal

### Action Execution
- `ActionExecutor` queues `(callable, confirm_signals, timeout, retries)` tuples
- Cooldowns stored per `action.__name__` to prevent spam (src/action/executor.py#L23)
- Confirmation signals are lambdas: `lambda old_gs, new_gs: new_gs.hp_cur > old_gs.hp_cur`

### Safety & Telemetry
- `SafetyManager.watchdog(threads)` blocks main thread, kills process on critical failure
- `Replay.save_roi()` logs ROI crops + GameState JSON to `logs/replay/` for debugging
- Replay files timestamped: `{timestamp}_{roi_name}.png` + `{timestamp}.json`

## Configuration Files
- **configs/route.json**: Navigator waypoints `[{"x": 10, "y": 20}, ...]`
- **configs/bestiary_match.yaml**: Creature priority rules (YAML schema TBD)
- **data/creatures_registry.json**: Canonical creature metadata
- **configs/ocr_corrections.json**: Map misread text → correct names

## Common Pitfalls
1. **Window capture fails**: `DXGICapture` requires exact window title match (default: `"Tibia -"`), falls back to full screen capture via MSS
2. **Type errors with cv2.Mat**: Always accept `Union[cv2.Mat, np.ndarray]`, cast to `uint8` before OCR
3. **Circular imports**: Use `TYPE_CHECKING` and runtime imports inside functions when needed
4. **Queue blocking**: Never use `.get()` without timeout in real-time threads - use `.get_nowait()` + exception handling
5. **ROI coordinates**: Never hardcode pixel values - always use normalized coordinates and `UICalibrator.normalize_to_px()`
6. **Black ROI images**: Fixed by implementing MSS fallback capture with multi-monitor detection - ensures real screen content instead of dummy frames

## Testing & Debugging
- Add replay logging: enable `REPLAY_ENABLED=1` (writes JSON + ROI crops into `logs/replay/`)
- View annotated frames: enable `OVERLAY_ENABLED=1` (writes PNGs into `logs/debug_overlay/`)
- Verify ROIs: Check `data/ROIs_resueltos.json` for calibration output
- Debug capture issues: Bot now captures real screen content via MSS fallback with multi-monitor detection, preventing black/dummy ROI images
- Validate captures: System automatically validates frame quality (not black, has color variation, minimum resolution) before processing
