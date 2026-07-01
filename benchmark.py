#!/usr/bin/env python3
"""
Performance benchmark script for crayfish detection system.
Measures detection latency, throughput, and resource usage.
"""

import time
import requests
import json
from datetime import datetime


def benchmark_api_latency(base_url="http://localhost:5002", duration_seconds=30):
    """Measure API response times and detection latency."""
    
    print("\n" + "="*60)
    print("📊 PERFORMANCE BENCHMARK")
    print("="*60)
    print(f"Base URL: {base_url}")
    print(f"Duration: {duration_seconds}s")
    print("="*60 + "\n")

    latencies = []
    detection_latencies = []
    errors = 0
    start_time = time.time()

    while time.time() - start_time < duration_seconds:
        try:
            # Test API endpoint latency
            req_start = time.time()
            response = requests.get(f"{base_url}/api/data", timeout=5)
            req_latency = (time.time() - req_start) * 1000  # ms
            latencies.append(req_latency)

            if response.status_code == 200:
                data = response.json()
                
                # Extract detection latency
                if "detection_latency_ms" in data:
                    detection_latencies.append(data["detection_latency_ms"])
                    
                # Print current status
                detection_count = data.get("detection_count", 0)
                health = data.get("health", {}).get("score", 0)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] "
                      f"API: {req_latency:6.1f}ms | "
                      f"Detections: {detection_count} | "
                      f"Health: {health:.0f}% | "
                      f"Latency: {detection_latencies[-1] if detection_latencies else 0:4.0f}ms")
            else:
                errors += 1
                print(f"❌ HTTP {response.status_code}")

        except requests.exceptions.RequestException as e:
            errors += 1
            print(f"❌ Request error: {e}")

        time.sleep(1)

    # Calculate statistics
    print("\n" + "="*60)
    print("RESULTS")
    print("="*60)
    
    if latencies:
        avg_api_latency = sum(latencies) / len(latencies)
        min_api_latency = min(latencies)
        max_api_latency = max(latencies)
        print(f"API Response Latency:")
        print(f"  Average: {avg_api_latency:.1f}ms")
        print(f"  Min:     {min_api_latency:.1f}ms")
        print(f"  Max:     {max_api_latency:.1f}ms")

    if detection_latencies:
        avg_det_latency = sum(detection_latencies) / len(detection_latencies)
        min_det_latency = min(detection_latencies)
        max_det_latency = max(detection_latencies)
        print(f"\nDetection Latency:")
        print(f"  Average: {avg_det_latency:.0f}ms")
        print(f"  Min:     {min_det_latency:.0f}ms")
        print(f"  Max:     {max_det_latency:.0f}ms")

    print(f"\nRequests: {len(latencies)} sent, {errors} errors")
    print("="*60 + "\n")


def benchmark_camera_stream(base_url="http://localhost:5002", num_frames=100):
    """Measure camera streaming performance."""
    
    print("\n" + "="*60)
    print("📹 CAMERA STREAM BENCHMARK")
    print("="*60)
    
    try:
        response = requests.get(f"{base_url}/camera/feed", stream=True, timeout=10)
        
        if response.status_code != 200:
            print(f"❌ Failed to connect to stream (HTTP {response.status_code})")
            return
        
        print(f"Connected to camera stream...")
        frame_count = 0
        frame_sizes = []
        start_time = time.time()
        
        for chunk in response.iter_content(chunk_size=8192):
            if frame_count >= num_frames:
                break
            
            if b"--frame" in chunk:
                # Extract frame size
                try:
                    lines = chunk.split(b'\r\n')
                    for i, line in enumerate(lines):
                        if b"Content-Length" in line:
                            size = int(line.split(b": ")[1])
                            frame_sizes.append(size)
                            frame_count += 1
                            break
                except:
                    pass
        
        elapsed = time.time() - start_time
        
        if frame_sizes:
            avg_frame_size = sum(frame_sizes) / len(frame_sizes)
            fps = frame_count / elapsed if elapsed > 0 else 0
            throughput_mbps = (sum(frame_sizes) * 8) / (elapsed * 1_000_000) if elapsed > 0 else 0
            
            print(f"Frames captured: {frame_count}")
            print(f"Time elapsed: {elapsed:.1f}s")
            print(f"FPS: {fps:.1f}")
            print(f"Avg frame size: {avg_frame_size:.0f} bytes")
            print(f"Throughput: {throughput_mbps:.2f} Mbps")
        
        print("="*60 + "\n")
        
    except Exception as e:
        print(f"❌ Stream benchmark failed: {e}")


if __name__ == "__main__":
    import sys
    
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5002"
    
    try:
        benchmark_api_latency(base_url, duration_seconds=30)
        benchmark_camera_stream(base_url, num_frames=100)
    except KeyboardInterrupt:
        print("\n⏸ Benchmark interrupted by user")
