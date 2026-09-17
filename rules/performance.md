# Performance

> Guidance for optimization and changes with plausible performance impact.

## When this applies

Use for optimization or when a change may materially affect latency, throughput,
resource use, or tail behavior.

## Rules

DO define the representative workload, correctness guard, baseline, target, and
resource budget before optimizing.

DO measure after suitable warmup and enough runs to discuss variance and tail
behavior.

DO report throughput or latency together with relevant resource and correctness
trade-offs.

DO scope microbenchmarks explicitly when they do not represent end-to-end use.

DON'T optimize an unmeasured hot path.

DON'T present a mean-only result as proof that tail behavior or capacity is safe.

## Red flags

- "This is obviously faster." Measure representative work and variance.
- "The benchmark improved." Check correctness and the displaced resource cost.

## Done when

- [ ] Workload, baseline, warmup, variance, and tail evidence are recorded.
- [ ] Correctness and resource trade-offs are verified.
- [ ] The result supports the decision being made.
