# HuMetric Python SDK v0.1.0

## Installation

```bash
pip install humetric-sdk
```

## Quickstart

```python
from humetric_sdk import HuMetricClient

client = HuMetricClient(api_key="your-api-key")

metrics = client.metrics.list()
print(metrics)

result = client.evaluate(
    metric_pack_id="mp_abc123",
    subject_id="user_456"
)
print(result)
```

## Documentation

Full API reference and guides: [docs.humetric.io](https://docs.humetric.io)
