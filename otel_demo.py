import time
import random

# --- TRACING IMPORTS ---
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider, SpanProcessor
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.trace import Status, StatusCode

# === 1. CUSTOM SPAN PROCESSOR ===
class PIIMaskingProcessor(SpanProcessor):
    """
    Intercepts spans on their way out to redact sensitive attributes.
    Because spans are strictly read-only by the time 'on_end' is called, 
    we must safely modify the internal '_attributes' dictionary.
    """
    def on_start(self, span, parent_context=None):
        pass

    def on_end(self, span):
        # Ensure the span actually has attributes before checking
        if not getattr(span, "_attributes", None):
            return
            
        # Check for our specific sensitive key
        if "payment.amount" in span._attributes:
            # Mask the value directly in the underlying dictionary
            span._attributes["payment.amount"] = "[REDACTED]"
            
            # Optional: Add an audit flag to show the processor did its job
            span._attributes["security.pii_scrubbed"] = True

    def force_flush(self, timeout_millis=30000):
        return True

    def shutdown(self):
        pass


# === 2. CONFIGURATION & SETUP ===
PHOENIX_GRPC_ENDPOINT = "http://localhost:4317" 

trace_provider = TracerProvider()

# WARNING: Processor order matters! 
# We must add the masking processor FIRST so it scrubs the data 
# before the BatchSpanProcessor packages it for export.
masking_processor = PIIMaskingProcessor()
trace_provider.add_span_processor(masking_processor)

# Add the Exporter SECOND
otlp_trace_exporter = OTLPSpanExporter(endpoint=PHOENIX_GRPC_ENDPOINT, insecure=True)
trace_provider.add_span_processor(BatchSpanProcessor(otlp_trace_exporter))

trace.set_tracer_provider(trace_provider)
tracer = trace.get_tracer("payment_service")


# === 3. CUSTOM EVALUATION LOGIC ===
def run_ai_eval(amount):
    """Simulates an AI checking the transaction."""
    score = random.uniform(0.5, 1.0)
    label = "Pass" if (amount < 400 and score > 0.65) else "Fail"
    return score, label


# === 4. APPLICATION LOGIC ===
def process_payment(amount, user_id):
    with tracer.start_as_current_span("process_payment_request") as span:
        
        # We add the sensitive amount here. The app "thinks" it's recording it normally.
        span.set_attribute("payment.amount", amount)
        span.set_attribute("user.id", user_id)
        
        span.add_event("Validating user credentials", {"user.id": user_id})
        time.sleep(random.uniform(0.1, 0.3))
        
        success = random.choice([True, True, False])
        
        eval_score, eval_label = run_ai_eval(amount)
        span.set_attribute("eval.correctness.score", eval_score)
        span.set_attribute("eval.correctness.label", eval_label)

        if success:
            span.add_event("Payment authorized by gateway")
            print(f"✅ Success: {user_id} - ${amount}")
        else:
            span.set_status(Status(StatusCode.ERROR, "Bank Gateway Timeout"))
            span.add_event("Payment rejected", {"reason": "Gateway Timeout"})
            print(f"❌ Failed: {user_id} - ${amount}")


# === 5. EXECUTION ===
if __name__ == "__main__":
    print(f"🚀 OTel Demo Active. Sending PII-Masked Traces to Phoenix via gRPC ({PHOENIX_GRPC_ENDPOINT})...")
    
    try:
        for i in range(10):
            process_payment(amount=random.randint(50, 500), user_id=f"user_{i+100}")
            time.sleep(0.5)
            
        print("📤 All traces sent. Flushing buffers...")
        trace_provider.shutdown()
        print("Done. Visit http://localhost:6006 to verify your payment amounts are [REDACTED]!")
        
    except KeyboardInterrupt:
        print("Stopped by user.")