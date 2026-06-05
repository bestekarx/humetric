# HuMetric Java SDK v0.1.0

## Installation

### Maven

```xml
<dependency>
  <groupId>com.humetric</groupId>
  <artifactId>humetric-sdk</artifactId>
  <version>0.1.0</version>
</dependency>
```

### Gradle

```groovy
implementation 'com.humetric:humetric-sdk:0.1.0'
```

## Quickstart

```java
import com.humetric.HuMetricClient;
import com.humetric.models.*;

HuMetricClient client = new HuMetricClient("your-api-key");

List<Metric> metrics = client.metrics().list();
System.out.println(metrics);

EvaluationResult result = client.evaluate(
    "mp_abc123",
    "user_456"
);
System.out.println(result);
```

## Documentation

Full API reference and guides: [docs.humetric.io](https://docs.humetric.io)
