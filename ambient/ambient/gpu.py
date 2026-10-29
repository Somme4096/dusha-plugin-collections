"""NVIDIA GPU readings through NVML, merged into ambient's metrics sample."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

try:
    import pynvml
except ImportError:  # no GPU metrics, everything else still works
    pynvml = None

READINGS: dict[str, Callable[[Any], float]] = {
    "gpu.util.percent": lambda handle: pynvml.nvmlDeviceGetUtilizationRates(handle).gpu,
    "gpu.mem.percent": lambda handle: _percent(pynvml.nvmlDeviceGetMemoryInfo(handle)),
    "gpu.temp.c": lambda handle: pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU),
    "gpu.power.w": lambda handle: pynvml.nvmlDeviceGetPowerUsage(handle) / 1000,
}


def _percent(memory: Any) -> float:
    return memory.used / memory.total * 100 if memory.total else 0.0


class Gpus:
    def __init__(self) -> None:
        self.count = 0
        # Shutting NVML down while a sample thread is inside it segfaults.
        self.lock = threading.Lock()
        if pynvml is None:
            return
        try:
            pynvml.nvmlInit()
            self.count = pynvml.nvmlDeviceGetCount()
        except pynvml.NVMLError:
            self.count = 0

    def read(self) -> dict[str, float]:
        values: dict[str, float] = {}
        with self.lock:
            for index in range(self.count):
                try:
                    handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                except pynvml.NVMLError:
                    continue
                # Each reading on its own, since consumer cards report some as not supported.
                for name, read in READINGS.items():
                    try:
                        values[f"{name}:{index}"] = round(float(read(handle)), 1)
                    except pynvml.NVMLError:
                        pass
        return values

    def busiest(self, index: int) -> int | None:
        """The pid holding the most VRAM on this GPU, if NVML can see one."""
        with self.lock:
            if index >= self.count:
                return None
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                processes = [
                    *pynvml.nvmlDeviceGetComputeRunningProcesses(handle),
                    *pynvml.nvmlDeviceGetGraphicsRunningProcesses(handle),
                ]
            except pynvml.NVMLError:
                return None
        if not processes:
            return None
        return max(processes, key=lambda process: process.usedGpuMemory or 0).pid

    def close(self) -> None:
        with self.lock:
            if self.count:
                try:
                    pynvml.nvmlShutdown()
                except pynvml.NVMLError:
                    pass
                self.count = 0
