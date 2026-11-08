import cudaq

cudaq.set_target("qpp-cpu")

@cudaq.kernel
def bell_pair():
    q = cudaq.qvector(2)
    h(q[0])
    x.ctrl(q[0], q[1])
    mz(q)

counts = cudaq.sample(bell_pair, shots_count=100)
assert sum(counts.values()) == 100, counts
print(f"CUDA-Q smoke test passed: version={cudaq.__version__}, target=qpp-cpu, shots=100, counts={counts}")
