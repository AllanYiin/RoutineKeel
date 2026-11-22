"""Task-neutral accelerator discovery. Driver probing runs in a bounded subprocess."""
from __future__ import annotations

import csv
import ctypes
import io
import json
import os
import platform
import shutil
import sys
from pathlib import Path


def cuda_driver_probe():
    if platform.system() not in {'Windows', 'Linux'}:
        return {'status': 'unavailable', 'device_count': 0, 'reason': 'platform_without_cuda_probe'}
    visible = os.environ.get('CUDA_VISIBLE_DEVICES')
    if visible is not None and visible.strip() in {'', '-1'}:
        return {'status': 'disabled', 'device_count': 0, 'reason': 'CUDA_VISIBLE_DEVICES'}
    try:
        driver = ctypes.WinDLL('nvcuda.dll') if platform.system() == 'Windows' else ctypes.CDLL('libcuda.so.1')
    except OSError:
        return {'status': 'unavailable', 'device_count': 0, 'reason': 'driver_library_not_found'}
    try:
        driver.cuInit.argtypes = [ctypes.c_uint]
        driver.cuInit.restype = ctypes.c_int
        code = driver.cuInit(0)
        if code:
            return {'status': 'unavailable' if code == 100 else 'unknown', 'device_count': 0,
                    'reason': 'cuInit', 'driver_error_code': code}
        count = ctypes.c_int()
        driver.cuDeviceGetCount.argtypes = [ctypes.POINTER(ctypes.c_int)]
        driver.cuDeviceGetCount.restype = ctypes.c_int
        code = driver.cuDeviceGetCount(ctypes.byref(count))
        if code:
            return {'status': 'unknown', 'device_count': 0, 'reason': 'cuDeviceGetCount', 'driver_error_code': code}
        return {'status': 'available' if count.value > 0 else 'unavailable',
                'device_count': max(0, count.value), 'reason': 'cuda_driver_api'}
    except (OSError, AttributeError) as exc:
        return {'status': 'unknown', 'device_count': 0, 'reason': type(exc).__name__}


def nvidia_smi_path():
    found = shutil.which('nvidia-smi')
    if found:
        return found
    if platform.system() == 'Windows':
        candidates = [Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32/nvidia-smi.exe',
                      Path(os.environ.get('ProgramFiles', r'C:\Program Files'))/'NVIDIA Corporation/NVSMI/nvidia-smi.exe']
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    return None


def parse_devices(text):
    devices = []
    for row in csv.reader(io.StringIO(text), skipinitialspace=True):
        if not row or not any(x.strip() for x in row):
            continue
        if len(row) != 4:
            raise ValueError('unexpected nvidia-smi columns')
        def memory(value):
            try:
                number = float(value.strip())
                return int(number * 1024**2) if number >= 0 else None
            except (ValueError, OverflowError):
                return None
        devices.append({'name': row[0].strip(), 'driver_version': row[1].strip(),
                        'memory_total_bytes': memory(row[2]), 'memory_free_bytes': memory(row[3])})
    return devices


def inspect_gpu(run_process):
    from .observability.recovery import SecondaryOperation

    gpu = {'status': 'not_detected', 'devices': [], 'backend_verified': False, 'diagnostics': []}
    smi = nvidia_smi_path()
    if smi:
        with SecondaryOperation('read NVIDIA device metadata') as device_probe:
            output = run_process([smi, '--query-gpu=name,driver_version,memory.total,memory.free',
                                  '--format=csv,noheader,nounits'], timeout=10)
            gpu['devices'] = parse_devices(output)
            gpu['status'] = 'detected' if gpu['devices'] else 'not_detected'
        if device_probe.error is not None:
            exc = device_probe.error
            gpu['status'] = 'unknown'
            gpu['diagnostics'].append({'probe': 'nvidia-smi', 'error': getattr(exc, 'code', type(exc).__name__)})
    with SecondaryOperation('read CUDA driver status') as driver_probe:
        result = json.loads(run_process([sys.executable, '-I', str(Path(__file__).resolve()), '--cuda-driver'], timeout=10))
        if result['status'] not in {'available', 'unavailable', 'unknown', 'disabled'} or not isinstance(result['device_count'], int):
            raise ValueError('invalid driver probe response')
        gpu['cuda'] = result
        if result['status'] == 'available':
            gpu['status'] = 'detected'
    if driver_probe.error is not None:
        exc = driver_probe.error
        gpu['cuda'] = {'status': 'unknown', 'device_count': 0, 'reason': getattr(exc, 'code', type(exc).__name__)}
    return gpu


if __name__ == '__main__' and sys.argv[1:] == ['--cuda-driver']:
    print(json.dumps(cuda_driver_probe()))
