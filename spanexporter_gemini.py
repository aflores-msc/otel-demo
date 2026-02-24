import os
import json
import re

from google import genai
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider, SpanProcessor
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.trace import format_span_id, format_trace_id, Status, StatusCode

# ==========================================
# 1. MAIN THREAD PROCESSOR (Lightweight)
# ==========================================
class ContextInjectionProcessor(SpanProcessor):
    """Runs instantly on the main thread to grab IDs."""
    def on_start(self, span, parent_context=None):
        ctx = span.get_span_context()
        span.set_attribute("meta.trace_id", format_trace_id(ctx.trace_id))
        span.set_attribute("meta.span_id", format_span_id(ctx.span_id))
        if span.parent:
            span.set_attribute("meta.parent_id", format_span_id(span.parent.span_id))

    def on_end(self, span):
        pass

# ==========================================
# 2. BACKGROUND THREAD EXPORTER (Heavyweight)
# ==========================================
class AsyncRedactingExporter(SpanExporter):
    """Runs completely in the background, protecting your app's performance."""
    def __init__(self, underlying_exporter):
        self.underlying_exporter = underlying_exporter
        self.patterns = [
            re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b'), # Emails
            re.compile(r'\b(?:\d[ -]*?){13,16}\b'),                              # Credit Cards
            re.compile(r'\b\d{3}-\d{2}-\d{4}\b')                                 # SSNs
        ]
        self.skip_keys = {"meta.span_id", "meta.trace_id", "meta.parent_id"}

    def export(self, spans) -> SpanExportResult:
        for span in spans:
            if not getattr(span, "_attributes", None):
                continue
                
            is_scrubbed = False
            for key, value in span._attributes.items():
                if key in self.skip_keys:
                    continue
                
                if isinstance(value, str):
                    original_value = value
                    for pattern in self.patterns:
                        value = pattern.sub("[REDACTED PII]", value)
                    
                    if original_value != value:
                        span._attributes[key] = value
                        is_scrubbed = True

            if is_scrubbed:
                span._attributes["security.async_regex_scrubbed"] = True

        return self.underlying_exporter.export(spans)

    def shutdown(self):
        self.underlying_exporter.shutdown()

# ==========================================
# 3. OTELEMETRY SETUP & MEMORY TUNING
# ==========================================
trace_provider = TracerProvider()

# 1. Add the fast main-thread processor
trace_provider.add_span_processor(ContextInjectionProcessor())

# 2. Setup the real network Exporter
real_otlp_exporter = OTLPSpanExporter(endpoint="http://localhost:4317", insecure=True)

# 3. Wrap it with our background-thread PII scrubber
safe_async_exporter = AsyncRedactingExporter(real_otlp_exporter)

# 4. Configure the Batch processor to control memory
batch_processor = BatchSpanProcessor(
    safe_async_exporter,
    max_queue_size=4096,           # Hold up to 4096 spans in memory
    schedule_delay_millis=2000,    # Export every 2 seconds instead of 5
    max_export_batch_size=512,     # Send up to 512 spans per network request
    export_timeout_millis=10000    # Give up after 10 seconds of network lag
)
trace_provider.add_span_processor(batch_processor)

trace.set_tracer_provider(trace_provider)
tracer = trace.get_tracer("gemini_async_service")

# ==========================================
# 4. LIVE GEMINI API INTEGRATION
# ==========================================
client = genai.Client()

def call_gemini(context_obj):
    with tracer.start_as_current_span("gemini_api_call") as child_span:
        child_span.set_attribute("gen_ai.system", "gemini")
        child_span.set_attribute("gen_ai.request.model", context_obj["model"])
        
        print(f"\n🤖 Prompting Gemini: '{context_obj['message']}'")
        
        try:
            response = client.models.generate_content(
                model=context_obj["model"],
                contents=context_obj["message"]
            )
            
            if response.usage_metadata:
                child_span.set_attribute("gen_ai.usage.prompt_tokens", response.usage_metadata.prompt_token_count)
                child_span.set_attribute("gen_ai.usage.completion_tokens", response.usage_metadata.candidates_token_count)
            
            return response.text
            
        except Exception as e:
            child_span.set_status(Status(StatusCode.ERROR, str(e)))
            print(f"❌ API Error: {e}")
            return "Error: Could not retrieve response."

# ==========================================
# 5. MAIN APPLICATION LOGIC
# ==========================================
def process_chat(user_id, message):
    context_obj = {
        "user_id": user_id,
        "message": message,
        "model": "gemini-3-flash-preview"
    }

    with tracer.start_as_current_span("process_chat_request") as parent_span:
        # Send raw context to telemetry (will be scrubbed asynchronously)
        parent_span.set_attribute("app.context_dump", json.dumps(context_obj))
        
        # Call LLM with pristine context
        response_text = call_gemini(context_obj)
        
        parent_span.set_attribute("gen_ai.response.completion", response_text)
        print(f"✅ Gemini replied: {response_text[:60].replace(chr(10), ' ')}...") 

# ==========================================
# 6. EXECUTION
# ==========================================
if __name__ == "__main__":
    if not os.environ.get("GEMINI_API_KEY"):
        print("⚠️  WARNING: GEMINI_API_KEY environment variable is not set. The API call will fail.")
        
    print("🚀 Running Async Gemini Trace...")
    
    test_messages = [
        "Write a polite rejection to this email address: applicant99@demo.com.",
        "What is the capital of Japan?"
    ]
    
    try:
        for idx, msg in enumerate(test_messages):
            process_chat(user_id=f"user_{idx}", message=msg)
            
        print("\n📤 Forcing background thread to flush traces...")
        trace_provider.shutdown()
        print("Done. All traces scrubbed on the background thread and sent to Phoenix!")
        
    except KeyboardInterrupt:
        pass