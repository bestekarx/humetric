# HuMetric PHP SDK v0.1.0

## Installation

```bash
composer require humetric/sdk
```

## Quickstart

```php
<?php

require_once __DIR__ . '/vendor/autoload.php';

$client = new \Humetric\HuMetricClient('your-api-key');

$metrics = $client->metrics()->list();
print_r($metrics);

$result = $client->evaluate(
    metricPackId: 'mp_abc123',
    subjectId: 'user_456'
);
print_r($result);
```

## Documentation

Full API reference and guides: [docs.humetric.io](https://docs.humetric.io)
