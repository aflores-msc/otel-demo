# OpenTelemetry Python Demo

This project demonstrates how to instrument a Python application with OpenTelemetry (OTel) to generate traces, handle sensitive data securely, and export telemetry to a backend.

## Overview

The `otel_demo.py` script simulates a payment processing workflow containing the following steps:
1.  **Payment Request**: The root span initiating the workflow.
2.  **AI Fraud Evaluation**: A child span simulating a call to an AI model to check for fraud.
3.  **Payment Gateway**: A child span simulating an external API call (e.g., Stripe) to authorize funds.

## Features

*   **Manual Instrumentation**: Demonstrates how to use the OpenTelemetry SDK to create tracers, start spans, and manage context manually.
*   **Custom Span Processors**:
    *   **Context Enrichment**: Automatically adds trace and span IDs as attributes to every span (`on_start`).
    *   **Security & Redaction**: Intercepts spans before they are exported to scrub PII (Personally Identifiable Information), specifically redacting the `payment.amount` attribute (`on_end`).
*   **OTLP Exporting**: Configured to send traces via gRPC to a local endpoint (`localhost:4317`), targeting backends like Arize Phoenix.
*   **Nested Spans**: Visualizes the parent-child relationships in distributed systems.
*   **Status & Events**: Shows how to set span status codes (OK/ERROR) and log structured events within a span.

## Prerequisites

*   Python 3.8+
*   A running OTLP backend (e.g., Arize Phoenix, Jaeger, or an OTel Collector) listening on `localhost:4317`.

## Installation

1.  **Create a virtual environment**:
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # On Windows: .venv\Scripts\activate
    ```

2.  **Install dependencies**:
    ```bash
    pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp
    ```

## Usage

1.  Ensure your OTLP backend is running.
2.  Run the demo script:
    ```bash
    python otel_demo.py
    ```

The script will generate 15 simulated transactions. You will see console output indicating success or failure for each transaction, and the traces will be flushed to your backend upon completion.

## Key Concepts

### Span Processor Order

One of the most important lessons in this demo is the registration order of Span Processors.

```python
# 1. Modify/Redact data first
trace_provider.add_span_processor(SecurityAndContextProcessor())

# 2. Export the clean data
trace_provider.add_span_processor(BatchSpanProcessor(otlp_trace_exporter))
```

We register the **Security** processor *before* the **Batch** (Export) processor. If the order were reversed, the `BatchSpanProcessor` would queue the span for export (potentially containing raw PII) before the security processor had a chance to redact it.

### PII Redaction

The `SecurityAndContextProcessor` implements the `on_end` method to sanitize data:

```python
if "payment.amount" in span._attributes:
    span._attributes["payment.amount"] = "[REDACTED]"
```