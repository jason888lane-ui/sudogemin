import math

import pytest

from quantum_engine import quantum_status, sample_feature_circuit


def test_cudaq_status_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CUDA_Q_ENABLED", raising=False)
    monkeypatch.delenv("CUDA_Q_GPU_ENABLED", raising=False)
    state = quantum_status(cudaq_module=object())
    assert state["status"] == "disabled"
    assert state["ready"] is False


def test_gpu_target_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("CUDA_Q_ENABLED", "true")
    monkeypatch.setenv("CUDA_Q_TARGET", "nvidia")
    monkeypatch.setenv("CUDA_Q_GPU_ENABLED", "false")
    state = quantum_status(cudaq_module=object())
    assert state["status"] == "target_not_authorized"
    assert state["ready"] is False


@pytest.mark.parametrize(
    "buyer,dataset",
    [
        ([], []),
        ([0.1], []),
        ([math.nan], [0.1]),
        ([math.inf], [0.1]),
        ([-0.1], [0.1]),
        ([math.pi + 0.01], [0.1]),
        ([True], [0.1]),
        ([0.1] * 17, [0.1] * 17),
    ],
)
def test_feature_vectors_rejected_before_runtime_access(buyer, dataset):
    with pytest.raises(ValueError):
        sample_feature_circuit(buyer, dataset, cudaq_module=None)


def test_shots_are_bounded_before_runtime_access():
    with pytest.raises(ValueError):
        sample_feature_circuit([0.1], [0.2], shots=99, cudaq_module=None)
