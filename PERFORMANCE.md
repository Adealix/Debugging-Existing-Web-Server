# Crayfish IoT Detection System - Performance Optimization Guide

## Overview

This system has been optimized for low-latency detection and streaming on Raspberry Pi hardware. The optimization strategy focuses on:

1. **Frame Downsampling** - Reduce detection resolution from 1280x720 to 416x416 (6-8x faster)
2. **Frame Skipping** - Process every Nth frame for detection while streaming all frames
3. **Compression Tuning** - Lower JPEG quality for streaming and detection encoding
4. **FPS Limiting** - Cap streaming at 12 FPS instead of 15 to reduce bandwidth
5. **Thread Optimization** - Efficient frame buffering with minimal lock contention

## Performance Improvements

### Expected Latency Reduction

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Detection latency | ~2-3s | ~300-500ms | 80-85% ⬇️ |
| Camera stream lag | High (15 FPS, full res) | Low (12 FPS, optimized) | 20% ⬇️ |
| Network bandwidth | ~45 Mbps | ~8-12 Mbps | 75% ⬇️ |
| Frame encode time | ~50-80ms | ~15-25ms | 65% ⬇️ |

### Key Optimizations

#### 1. Detection Frame Downsampling

```python
DETECTION_FRAME_WIDTH = 416  # Down from 1280
DETECTION_FRAME_HEIGHT = 416 # Down from 720
```

- Reduces API payload size by ~82%
- Faster inference due to smaller model input
- Coordinates automatically scaled back to original size
- Minimal accuracy loss for object detection

#### 2. Frame Skipping

```python
FRAME_SKIP = 1  # Set to 2+ on slower hardware
```

- `1` = Process every frame (default, best detection)
- `2` = Process every other frame
- `3+` = Process less frequently on slow hardware
- Camera streaming continues unaffected (all frames shown)

#### 3. JPEG Quality Reduction

```python
DETECTION_JPEG_QUALITY = 65  # Quality for API submission
STREAM_JPEG_QUALITY = 70     # Quality for browser display
```

- Detection JPEG: 65% (smaller upload, good enough for API)
- Streaming JPEG: 70% (balanced quality for viewing)
- Reduced from hardcoded 85% everywhere
- Saves ~20-30% on file sizes

#### 4. FPS Limiting

```python
MAX_STREAMING_FPS = 12  # Down from 15
```

- Reduces network bandwidth
- Decreases frontend rendering CPU
- Still provides smooth video (12 FPS is perceptible)
- Configurable per deployment

## Configuration

Edit `.env` file (copy from `.env.example`):

```bash
# Performance tuning
DETECTION_FRAME_WIDTH=416
DETECTION_FRAME_HEIGHT=416
DETECTION_JPEG_QUALITY=65
STREAM_JPEG_QUALITY=70
FRAME_SKIP=1
MAX_STREAMING_FPS=12

# Detection interval (don't run faster than needed)
ROBOFLOW_DETECTION_INTERVAL=1.0
```

### Recommended Settings by Hardware

**Raspberry Pi 4B (8GB, Recommended)**
```
FRAME_SKIP=1
MAX_STREAMING_FPS=12
DETECTION_FRAME_WIDTH=416
```

**Raspberry Pi 4B (4GB)**
```
FRAME_SKIP=2
MAX_STREAMING_FPS=10
DETECTION_FRAME_WIDTH=416
```

**Raspberry Pi Zero 2W (Slower hardware)**
```
FRAME_SKIP=3
MAX_STREAMING_FPS=8
DETECTION_FRAME_WIDTH=320
STREAM_JPEG_QUALITY=50
```

## Performance Benchmarking

Run the included benchmark script to measure latency:

```bash
python3 benchmark.py http://192.168.100.207:5002
```

Output shows:
- **API Response Latency**: Time for `/api/data` endpoint
- **Detection Latency**: Time for Roboflow inference (ms)
- **Camera Stream FPS**: Actual frames per second delivered
- **Frame sizes**: JPEG compression effectiveness

### What Good Performance Looks Like

```
API Response Latency:
  Average: 45.2ms      ← Should be < 100ms
  Min:     32.1ms
  Max:     156.3ms

Detection Latency:
  Average: 420ms       ← Should be < 800ms (6-8x reduction from ~2-3s)
  Min:     380ms
  Max:     520ms

Frames captured: 87
FPS: 11.8            ← Should be close to MAX_STREAMING_FPS (12)
Avg frame size: 18432 bytes
Throughput: 1.65 Mbps ← Should be < 10 Mbps
```

## Monitoring Performance

### Dashboard Metrics

The web dashboard shows:
- Real-time detection latency in milliseconds
- Current streaming frame rate
- Health score updated every 1 second
- Recent detection events and errors

### API Endpoint

```bash
curl http://192.168.100.207:5002/api/data | jq '.detection_latency_ms'
```

## Advanced Tuning

### For Faster Detection (Trade: Accuracy)

1. Increase `FRAME_SKIP` (2-3) to skip frames
2. Reduce `DETECTION_FRAME_WIDTH/HEIGHT` to 320x320
3. Lower `ROBOFLOW_CONFIDENCE` threshold (0.25-0.30)

### For Better Accuracy (Trade: Latency)

1. Set `FRAME_SKIP=1` (process every frame)
2. Increase `DETECTION_FRAME_WIDTH/HEIGHT` to 512x512
3. Increase `ROBOFLOW_CONFIDENCE` threshold (0.50+)

### For Better Streaming Quality (Trade: Bandwidth)

1. Increase `STREAM_JPEG_QUALITY` to 80-85
2. Increase `MAX_STREAMING_FPS` to 15
3. Use larger `DETECTION_FRAME_WIDTH/HEIGHT` for rendering

## Troubleshooting

**High detection latency (> 1 second)**
- Check network connectivity to Roboflow
- Increase `FRAME_SKIP` to skip frames
- Reduce `DETECTION_FRAME_WIDTH/HEIGHT` further
- Check Roboflow API rate limits

**Lag in camera stream**
- Reduce `MAX_STREAMING_FPS` further
- Reduce `STREAM_JPEG_QUALITY`
- Check network bandwidth (wifi signal strength)
- Reduce dashboard update frequency in browser dev console

**Missed detections**
- Set `FRAME_SKIP=1` to process all frames
- Increase `DETECTION_FRAME_WIDTH/HEIGHT` (trades latency)
- Increase `ROBOFLOW_CONFIDENCE` lower (if needed)

**High CPU usage on Raspberry Pi**
- Increase `FRAME_SKIP` (2-3)
- Reduce `MAX_STREAMING_FPS`
- Enable hardware video encoding if available
- Reduce dashboard polling frequency

## Architecture

```
Camera Capture Loop (camera_worker)
    └─ Capture frame at 15 FPS (Picamera2)
    └─ Store in latest_raw_frame (shared)
    └─ Apply detections (from detection_worker)
    └─ JPEG encode at STREAM_JPEG_QUALITY
    └─ Rate-limited to MAX_STREAMING_FPS
    └─ Yield for MJPEG streaming

Detection Loop (detection_worker)
    ├─ Read latest_raw_frame every FRAME_SKIP frames
    ├─ Downsample to DETECTION_FRAME_WIDTH x DETECTION_FRAME_HEIGHT
    ├─ JPEG encode at DETECTION_JPEG_QUALITY
    ├─ POST to Roboflow API
    ├─ Scale detections back to full resolution
    └─ Update detection_state (latency_ms, count, bboxes)

MJPEG Stream (generate_camera_stream)
    └─ Yield latest_camera_frame at max FPS rate
    └─ Boundary markers for MJPEG format
    └─ Frame sizes included in HTTP headers
```

## System Requirements

- **Raspberry Pi 4B or later** (recommended)
- **2GB+ RAM** (4GB+ recommended)
- **Fast network** (5+ Mbps for streaming)
- **Python 3.7+**
- **Linux (Raspberry Pi OS)**

## Dependencies

See `requirements.txt` - all optimized for Raspberry Pi:
- `picamera2` - Hardware camera interface
- `opencv-python` - Image processing
- `flask` - Web server
- `requests` - HTTP client
- `pyserial` - Serial communication
- `supervision` - Detection annotations

## License

See LICENSE file
