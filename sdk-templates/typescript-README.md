# HuMetric TypeScript SDK v0.1.0

## Installation

```bash
npm install @humetric/sdk
```

## Quickstart

```typescript
import { HuMetricClient } from "@humetric/sdk";

const client = new HuMetricClient({ apiKey: "your-api-key" });

const metrics = await client.metrics.list();
console.log(metrics);

const result = await client.evaluate({
  metricPackId: "mp_abc123",
  subjectId: "user_456",
});
console.log(result);
```

## Documentation

Full API reference and guides: [docs.humetric.io](https://docs.humetric.io)
