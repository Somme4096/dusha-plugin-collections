from __future__ import annotations

import asyncio
import os
import sys
import types
from types import SimpleNamespace

from conftest import client, make_config


def _fake_nvml(card: dict) -> types.ModuleType:
    nvml = types.ModuleType("pynvml")
    nvml.NVMLError = type("NVMLError", (Exception,), {})
    nvml.NVML_TEMPERATURE_GPU = 0
    nvml.nvmlInit = lambda: None
    nvml.nvmlShutdown = lambda: None
    nvml.nvmlDeviceGetCount = lambda: 1
    nvml.nvmlDeviceGetHandleByIndex = lambda index: index
    nvml.nvmlDeviceGetUtilizationRates = lambda handle: SimpleNamespace(gpu=97)
    nvml.nvmlDeviceGetMemoryInfo = lambda handle: SimpleNamespace(used=30, total=32)
    nvml.nvmlDeviceGetTemperature = lambda handle, sensor: card["temp"]

    def power(handle):
        raise nvml.NVMLError("not supported")

    nvml.nvmlDeviceGetPowerUsage = power
    nvml.nvmlDeviceGetComputeRunningProcesses = lambda handle: [
        SimpleNamespace(pid=os.getpid(), usedGpuMemory=28)
    ]
    nvml.nvmlDeviceGetGraphicsRunningProcesses = lambda handle: []
    return nvml


async def _wait(http, kind: str, prefix: str, timeout: float = 10.0) -> list[dict]:
    for _ in range(int(timeout / 0.2)):
        rows = (await http.get("/state/v1/ambient", params={"kind": kind})).json()["observations"]
        rows = [row for row in rows if row["text"].startswith(prefix)]
        if rows:
            return rows
        await asyncio.sleep(0.2)
    raise AssertionError(f"no {kind} in time")


async def test_gpu_readings_are_sampled_and_only_sustained_heat_alerts(tmp_path, plugins_dir, monkeypatch):
    card = {"temp": 91}
    monkeypatch.setitem(sys.modules, "pynvml", _fake_nvml(card))
    options = {"device": "desktop", "sample_seconds": 1, "alerts": {"gpu_minutes": 0.06}}
    config = make_config(tmp_path / "data", plugins_dir, {"ambient": options})
    async with client(config) as http:
        [alert] = await _wait(http, "alert", "GPU")
        metrics = (await http.get("/state/v1/ambient/metrics", params={"device": "desktop"})).json()
        card["temp"] = 40
        [resolved] = await _wait(http, "resolved", "gpu:")
    readings = metrics["metrics"]["desktop"].items()
    gpu = {name: summary["last"] for name, summary in readings if name.startswith("gpu")}
    assert gpu == {"gpu.util.percent:0": 97.0, "gpu.mem.percent:0": 93.8, "gpu.temp.c:0": 91.0}
    assert alert["text"].startswith("GPU 0 at 91°C for 0.06+ min, mostly `")
    assert resolved["text"] == "gpu:0 back to normal"
