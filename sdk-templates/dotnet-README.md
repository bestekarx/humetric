# HuMetric .NET SDK v0.1.0

## Installation

```bash
dotnet add package Humetric.Sdk
```

## Quickstart

```csharp
using Humetric.Sdk;

var client = new HuMetricClient("your-api-key");

var metrics = await client.Metrics.ListAsync();
Console.WriteLine(metrics);

var result = await client.EvaluateAsync(
    metricPackId: "mp_abc123",
    subjectId: "user_456"
);
Console.WriteLine(result);
```

## Documentation

Full API reference and guides: [docs.humetric.io](https://docs.humetric.io)
