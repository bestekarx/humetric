# Quickstart

Get started with HuMetric in under 5 minutes.

## 1. Get an API Key

Create an API key via the API or dashboard:

::: code-group

```bash [cURL]
curl -X POST https://api.humetric.io/v1/api-keys \
  -H "Authorization: Bearer YOUR_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"prefix": "myapp", "scopes": ["signals:write", "entities:read", "query"]}'
```

:::

Save the returned `full_key` securely — it will not be shown again.

## 2. Install SDK

::: code-group

```bash [Python]
pip install humetric-sdk
```

```bash [TypeScript]
npm install @humetric/sdk
```

```bash [.NET]
dotnet add package Humetric.Sdk
```

```bash [PHP]
composer require humetric/sdk
```

```bash [Java]
# Add to pom.xml:
# <dependency>
#   <groupId>com.humetric</groupId>
#   <artifactId>humetric-sdk</artifactId>
#   <version>0.1.0</version>
# </dependency>
```

:::

## 3. Send Your First Signal

::: code-group

```python [Python]
from humetric_sdk import HumetricClient

client = HumetricClient(api_key="your-api-key")
result = client.signals.send(
    entity_id="customer-42",
    entity_type="customer",
    text="Customer upgraded to premium plan"
)
print(f"Signal submitted: {result.signal_id}")
```

```typescript [TypeScript]
import { HumetricClient } from '@humetric/sdk';

const client = new HumetricClient({ apiKey: 'your-api-key' });
const result = await client.signals.send({
  entityId: 'customer-42',
  entityType: 'customer',
  text: 'Customer upgraded to premium plan'
});
console.log(`Signal submitted: ${result.signalId}`);
```

```csharp [.NET]
using Humetric.Sdk;

var client = new HumetricClient("your-api-key");
var result = await client.Signals.SendAsync(new SignalRequest {
    EntityId = "customer-42",
    EntityType = "customer",
    Text = "Customer upgraded to premium plan"
});
Console.WriteLine($"Signal submitted: {result.SignalId}");
```

```php [PHP]
use Humetric\Client\HumetricClient;

$client = new HumetricClient('your-api-key');
$result = $client->signals()->send(
    'customer-42',
    'customer',
    'Customer upgraded to premium plan'
);
echo "Signal submitted: " . $result->getSignalId();
```

```java [Java]
import com.humetric.sdk.HumetricClient;
import com.humetric.sdk.models.SignalRequest;

HumetricClient client = new HumetricClient("your-api-key");
SignalRequest req = new SignalRequest()
    .entityId("customer-42")
    .entityType("customer")
    .text("Customer upgraded to premium plan");
SignalResult result = client.signals().send(req);
System.out.println("Signal submitted: " + result.getSignalId());
```

```bash [cURL]
curl -X POST https://api.humetric.io/v1/signals \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: sig-001" \
  -d '{"entity_id": "customer-42", "entity_type": "customer", "text": "Customer upgraded to premium plan"}'
```

:::

## 4. Query Entity Metrics

::: code-group

```python [Python]
metrics = client.entities.get("customer-42")
for m in metrics.metrics:
    print(f"{m.metric_key}: {m.value} (confidence: {m.confidence})")
```

```typescript [TypeScript]
const entity = await client.entities.get('customer-42');
entity.metrics.forEach(m => {
  console.log(`${m.metricKey}: ${m.value} (confidence: ${m.confidence})`);
});
```

:::

<a href="https://app.getpostman.com/run-collection/humetric" target="_blank">
  <img src="https://run.pstmn.io/button.svg" alt="Run in Postman" />
</a>
