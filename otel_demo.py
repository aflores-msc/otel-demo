"""
OpenTelemetry Demo Script

This script demonstrates a simulated payment processing workflow instrumented with OpenTelemetry.
It showcases:
1.  Custom Span Processors for security (PII redaction) and metadata enrichment.
2.  OTLP Exporting to a backend (e.g., Phoenix) via gRPC.
3.  Manual instrumentation using the OpenTelemetry SDK.
4.  Nested spans representing different parts of a distributed system (AI evaluation, Payment Gateway).
5.  Attribute setting, event logging, and status handling on spans.
"""
import time
import random

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider, SpanProcessor
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.trace import Status, StatusCode, format_span_id, format_trace_id

# === 1. SPAN PROCESSOR ===
class SecurityAndContextProcessor(SpanProcessor):
    """
    A custom SpanProcessor that intercepts span start and end events.

    It serves two purposes:
    1.  Enrichment: Adds trace and span IDs as attributes for easier correlation in logs/backends.
    2.  Security: Redacts sensitive PII (Personally Identifiable Information) like payment amounts
        before the span is exported.
    """
    def on_start(self, span, parent_context=None):
        """
        Called when a span is started. Adds trace context metadata as attributes.
        """
        ctx = span.get_span_context()
        span.set_attribute("meta.trace_id", format_trace_id(ctx.trace_id))
        span.set_attribute("meta.span_id", format_span_id(ctx.span_id))
        if span.parent:
            span.set_attribute("meta.parent_id", format_span_id(span.parent.span_id))

    def on_end(self, span):
        """
        Called when a span is ended. Redacts 'payment.amount' if present.
        """
        if getattr(span, "_attributes", None) and "payment.amount" in span._attributes:
            span._attributes["payment.amount"] = "[REDACTED]"
            span._attributes["security.pii_scrubbed"] = True

    def force_flush(self, timeout_millis=30000):
        return True

    def shutdown(self):
        pass

# === 2. SETUP ===
PHOENIX_GRPC_ENDPOINT = "http://localhost:4317" 

trace_provider = TracerProvider()
"""
IMPORTANT: SpanProcessor Registration Order

The order is critical because it dictates the pipeline of operations performed on a span before it leaves your application.
- The SecurityAndContextProcessor is responsible for modifying the span data.
- The BatchSpanProcessor is responsible for exporting the span data to the backend.
- You want these modifications to happen before the span is handed off to the exporter.

1. SecurityAndContextProcessor (First):
   - Modifies the span (adds metadata).
   - Redacts PII (scrubs 'payment.amount').
   - Must run BEFORE export to ensure sensitive data is removed.

2. BatchSpanProcessor (Last):
   - Exports the span to the backend.
   - If this ran first, the unredacted span would be queued for export, causing a data leak.
"""
trace_provider.add_span_processor(SecurityAndContextProcessor())
otlp_trace_exporter = OTLPSpanExporter(endpoint=PHOENIX_GRPC_ENDPOINT, insecure=True)
trace_provider.add_span_processor(BatchSpanProcessor(otlp_trace_exporter))

trace.set_tracer_provider(trace_provider)
tracer = trace.get_tracer("payment_service")

# === 3. SUB-PROCESS 1: AI EVALUATION ===
def run_ai_eval(amount):
    """
    Simulates a call to an AI Fraud Detection service.

    Args:
        amount (int): The transaction amount.

    Returns:
        tuple: (score, label) where label is 'Pass' or 'Fail'.
    """
    with tracer.start_as_current_span("ai_fraud_evaluation") as child_span:
        time.sleep(random.uniform(0.05, 0.15)) 
        
        # Boosted AI pass rate: mostly passes unless amount is very high
        score = random.uniform(0.7, 1.0)
        label = "Pass" if (amount < 450 and score > 0.75) else "Fail"
        
        child_span.set_attribute("eval.model", "gpt-4-turbo")
        child_span.set_attribute("eval.score", score)
        
        return score, label

# === 4. SUB-PROCESS 2: GATEWAY ===
def call_payment_gateway(amount):
    """
    Simulates a call to an external Payment Gateway (e.g., Stripe).

    Args:
        amount (int): The transaction amount.

    Returns:
        bool: True if payment succeeded, False otherwise.
    """
    with tracer.start_as_current_span("stripe_gateway_auth") as child_span:
        ctx = child_span.get_span_context()
        child_span.add_event("Connecting to API", {
            "log.span_id": format_span_id(ctx.span_id)
        })
        time.sleep(random.uniform(0.1, 0.2)) 
        
        # Boosted Gateway pass rate to ~80%
        success = random.choice([True, True, True, True, False])
        if not success:
            child_span.set_status(Status(StatusCode.ERROR, "Gateway Timeout"))
            
        return success

# === 5. PARENT SPAN ===
def process_payment(amount, user_id):
    """
    Orchestrates the payment workflow.

    Creates the root span and calls sub-processes for AI evaluation and Gateway authorization.

    Args:
        amount (int): The transaction amount.
        user_id (str): The ID of the user making the payment.
    """
    with tracer.start_as_current_span("process_payment_request") as parent_span:
        parent_span.set_attribute("payment.amount", amount)
        parent_span.set_attribute("user.id", user_id)
        parent_span.add_event("Starting payment workflow")
        
        eval_score, eval_label = run_ai_eval(amount)
        
        parent_span.set_attribute("eval.correctness.score", eval_score)
        parent_span.set_attribute("eval.correctness.label", eval_label)

        if eval_label == "Fail":
            parent_span.set_status(Status(StatusCode.ERROR, "Blocked by AI Fraud Check"))
            print(f"⚠️  Blocked by AI: {user_id} - ${amount}")
            return 

        gateway_success = call_payment_gateway(amount)

        if gateway_success:
            parent_span.add_event("Payment authorized")
            print(f"✅ Success: {user_id} - ${amount}")
        else:
            parent_span.set_status(Status(StatusCode.ERROR, "Bank Rejected"))
            print(f"❌ Gateway Failed: {user_id} - ${amount}")

# === 6. EXECUTION ===
if __name__ == "__main__":
    print(f"🚀 OTel Demo Active. Generating 15 transactions...")
    
    try:
        # Increased to 15 iterations to generate a solid batch of successes and failures
        for i in range(15):
            process_payment(amount=random.randint(50, 480), user_id=f"user_{i+100}")
            time.sleep(0.2)
            
        print("📤 Flushing buffers to Phoenix...")
        trace_provider.shutdown()
        print("Done. Check the Phoenix UI!")
        
    except KeyboardInterrupt:
        pass