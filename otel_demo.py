import time
import random

# --- TRACING IMPORTS ---
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.trace import Status, StatusCode

# === CONFIGURATION ===
# Arize Phoenix uses port 4317 for its gRPC OTLP receiver
PHOENIX_GRPC_ENDPOINT = "http://localhost:4317" 

# === SETUP TRACING ===
trace_provider = TracerProvider()
# insecure=True is required because local Phoenix does not use SSL/TLS
otlp_trace_exporter = OTLPSpanExporter(endpoint=PHOENIX_GRPC_ENDPOINT, insecure=True)
trace_provider.add_span_processor(BatchSpanProcessor(otlp_trace_exporter))
trace.set_tracer_provider(trace_provider)
tracer = trace.get_tracer("payment_service")

# === CUSTOM EVALUATION LOGIC ===
def run_ai_eval(amount):
    """
    Simulates an 'LLM-as-a-Judge' evaluation.
    In a real app, this might check for toxicity, hallucinations, or correct formatting.
    """
    score = random.uniform(0.5, 1.0)
    # Give it a strict rule so we see some failures in the dashboard
    label = "Pass" if (amount < 400 and score > 0.65) else "Fail"
    return score, label

# === APPLICATION LOGIC ===
def process_payment(amount, user_id):
    # Start the trace
    with tracer.start_as_current_span("process_payment_request") as span:
        
        # 1. ADD ATTRIBUTES
        # Phoenix uses these to calculate your 'Metrics' (like average payment amount)
        span.set_attribute("payment.amount", amount)
        span.set_attribute("user.id", user_id)
        
        # 2. SIMULATE LOGGING (Span Events)
        # Because Phoenix is trace-centric, we use events instead of traditional logs.
        # These will appear as chronological markers inside your trace waterfall.
        span.add_event("Validating user credentials", {"user.id": user_id})
        time.sleep(random.uniform(0.1, 0.3))
        
        success = random.choice([True, True, False])
        
        # 3. RUN AI EVALUATION
        eval_score, eval_label = run_ai_eval(amount)
        
        # Phoenix automatically discovers attributes starting with 'eval.' 
        # and populates them in the 'Evaluations' tab in the UI.
        span.set_attribute("eval.correctness.score", eval_score)
        span.set_attribute("eval.correctness.label", eval_label)

        if success:
            span.add_event("Payment authorized by gateway")
            print(f"✅ Success: {user_id} - ${amount}")
        else:
            # 4. ERROR HANDLING
            # Setting this status triggers the 'Error Rate' metric to go up in Phoenix
            span.set_status(Status(StatusCode.ERROR, "Bank Gateway Timeout"))
            span.add_event("Payment rejected", {"reason": "Gateway Timeout"})
            print(f"❌ Failed: {user_id} - ${amount}")

# === EXECUTION ===
if __name__ == "__main__":
    print(f"🚀 OTel Demo Active. Sending Traces to Arize Phoenix via gRPC (4317)...")
    
    try:
        # Generate 10 sample traces to give us some good data to look at
        for i in range(10):
            process_payment(amount=random.randint(50, 500), user_id=f"user_{i+100}")
            time.sleep(0.5)
            
        print("📤 All traces sent. Flushing buffers...")
        # Shutting down the provider ensures all batches are sent immediately
        trace_provider.shutdown()
        print("Done. Visit http://localhost:6006 to see your results!")
        
    except KeyboardInterrupt:
        print("Stopped by user.")