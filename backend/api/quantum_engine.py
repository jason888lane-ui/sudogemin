"""Optional, bounded CUDA-Q feature sampler for Sudogemini.

CUDA-Q is a specialized optimization/research compute path, not the AI agent
runtime. Routine SaaS and marketplace workflows remain classical/Bedrock-backed.
The API only permits local CPU/GPU simulators; paid QPU execution belongs in
separately governed Amazon Braket Hybrid Jobs.
"""
from __future__ import annotations

import math
import os
import threading
from typing import Any

try:
    import cudaq as _cudaq
except ImportError:  # CUDA-Q is an optional extra, not an API boot dependency.
    _cudaq = None

_TARGET_LOCK = threading.Lock()
_ALLOWED_LOCAL_TARGETS = {"qpp-cpu", "nvidia"}


def quantum_status(cudaq_module: Any = _cudaq) -> dict:
    enabled = os.getenv("CUDA_Q_ENABLED", "false").lower() == "true"
    target = os.getenv("CUDA_Q_TARGET", "qpp-cpu").strip()
    installed = cudaq_module is not None
    gpu_opt_in = os.getenv("CUDA_Q_GPU_ENABLED", "false").lower() == "true"
    target_allowed = target == "qpp-cpu" or (target == "nvidia" and gpu_opt_in)
    ready = bool(enabled and installed and target_allowed)
    if not enabled:
        state = "disabled"
    elif not installed:
        state = "package_missing"
    elif not target_allowed:
        state = "target_not_authorized"
    else:
        state = "ready"
    return {
        "status": state,
        "enabled": enabled,
        "runtime": "CUDA-Q" if installed else "not_installed",
        "backend": target,
        "execution": "GPU simulator" if target == "nvidia" else "CPU simulator",
        "ready": ready,
        "qpu_execution": "not_configured",
        "note": (
            "CUDA-Q is an optional hybrid quantum-classical compute path. "
            "CPU/GPU simulation does not mean a physical QPU was used. "
            "Amazon Braket Hybrid Jobs must be configured separately for QPU jobs."
        ),
    }


def _validate_vectors(buyer_weights: list[float], dataset_weights: list[float]) -> None:
    if not isinstance(buyer_weights, list) or not isinstance(dataset_weights, list):
        raise ValueError("Feature vectors must be lists.")
    if not buyer_weights or len(buyer_weights) != len(dataset_weights):
        raise ValueError("Buyer and dataset vectors must have the same non-zero length.")
    if len(buyer_weights) > 16:
        raise ValueError("CUDA-Q feature sampling is limited to 16 features per circuit.")
    for value in [*buyer_weights, *dataset_weights]:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Feature values must be numeric.")
        if not math.isfinite(float(value)) or not 0.0 <= float(value) <= math.pi:
            raise ValueError("Feature values must be finite radians between 0 and pi.")


def sample_feature_circuit(
    buyer_weights: list[float],
    dataset_weights: list[float],
    *,
    shots: int = 1000,
    cudaq_module: Any = _cudaq,
) -> dict:
    """Run an experimental angle-encoded circuit; never present it as a match score."""
    _validate_vectors(buyer_weights, dataset_weights)
    if isinstance(shots, bool) or not isinstance(shots, int) or not 100 <= shots <= 5000:
        raise ValueError("shots must be an integer between 100 and 5000.")
    state = quantum_status(cudaq_module)
    if not state["ready"]:
        raise RuntimeError(f"CUDA-Q is not ready: {state['status']}")
    target = state["backend"]
    if target not in _ALLOWED_LOCAL_TARGETS:
        raise RuntimeError("Only approved local simulator targets are allowed by this API.")
    if target == "nvidia" and os.getenv("CUDA_Q_GPU_ENABLED", "false").lower() != "true":
        raise RuntimeError("GPU execution requires CUDA_Q_GPU_ENABLED=true.")
    buyer = [float(x) for x in buyer_weights]
    dataset = [float(x) for x in dataset_weights]

    # CUDA-Q's target is process-global. Serialize target selection and execution
    # so concurrent API requests cannot switch the backend underneath one another.
    with _TARGET_LOCK:
        cudaq_module.set_target(target)

        @cudaq_module.kernel
        def feature_kernel(buyer_angles: list[float], dataset_angles: list[float]):
            qubits = cudaq_module.qvector(len(buyer_angles))
            for index in range(len(buyer_angles)):
                ry(buyer_angles[index], qubits[index])
                rz(dataset_angles[index], qubits[index])
            for index in range(len(buyer_angles) - 1):
                x.ctrl(qubits[index], qubits[index + 1])
            mz(qubits)

        counts = cudaq_module.sample(
            feature_kernel, buyer, dataset, shots_count=shots
        )
    histogram = {str(key): int(value) for key, value in counts.items()}
    return {
        "engine": "CUDA-Q",
        "target": target,
        "execution": state["execution"],
        "shots": shots,
        "measurement_histogram": histogram,
        "business_match_score": None,
        "note": (
            "Experimental quantum feature-circuit measurements only. "
            "This is not a validated buyer-dataset relevance score and does not "
            "publish listings, approve licenses, or execute transactions."
        ),
    }
