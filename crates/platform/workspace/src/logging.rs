//! Process logging shared by all long-lived Rust components.
//!
//! Logs are emitted as JSONL to stderr. The system supervisor redirects that
//! stream to the component log file, while direct invocations remain visible
//! in a terminal. `RUST_LOG` controls the level/filter (default: `info`).

use tracing_subscriber::{fmt, layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};

const DEFAULT_OTLP_BASE_ENDPOINT: &str = "http://127.0.0.1:4318";

#[cfg(feature = "otel")]
static TRACER_PROVIDER: std::sync::OnceLock<opentelemetry_sdk::trace::SdkTracerProvider> =
    std::sync::OnceLock::new();
#[cfg(feature = "otel")]
static METER_PROVIDER: std::sync::OnceLock<opentelemetry_sdk::metrics::SdkMeterProvider> =
    std::sync::OnceLock::new();

/// Flush pending telemetry before a long-lived process exits.
pub fn shutdown() {
    #[cfg(feature = "otel")]
    if let Some(provider) = TRACER_PROVIDER.get() {
        if let Err(error) = provider.force_flush() {
            eprintln!("OpenTelemetry flush failed: {error}");
        }
        if let Err(error) = provider.shutdown() {
            eprintln!("OpenTelemetry shutdown failed: {error}");
        }
    }
    #[cfg(feature = "otel")]
    if let Some(provider) = METER_PROVIDER.get() {
        if let Err(error) = provider.shutdown() {
            eprintln!("OpenTelemetry metrics shutdown failed: {error}");
        }
    }
}

/// Record a monotonic process metric. With telemetry disabled this is a cheap
/// no-op, so callers can instrument business boundaries without feature gates.
pub fn record_counter(name: &'static str, value: u64) {
    #[cfg(feature = "otel")]
    {
        use opentelemetry::global;
        global::meter("kairos")
            .u64_counter(name)
            .build()
            .add(value, &[]);
    }
    #[cfg(not(feature = "otel"))]
    let _ = (name, value);
}

/// Record a duration in milliseconds at an application or transport boundary.
pub fn record_duration_ms(name: &'static str, value: f64) {
    #[cfg(feature = "otel")]
    {
        use opentelemetry::global;
        global::meter("kairos")
            .f64_histogram(name)
            .build()
            .record(value, &[]);
    }
    #[cfg(not(feature = "otel"))]
    let _ = (name, value);
}

/// Record a point-in-time gauge without making process state depend on metrics.
pub fn record_gauge(name: &'static str, value: u64) {
    #[cfg(feature = "otel")]
    {
        use opentelemetry::global;
        global::meter("kairos")
            .u64_gauge(name)
            .build()
            .record(value, &[]);
    }
    #[cfg(not(feature = "otel"))]
    let _ = (name, value);
}

/// Attach a W3C trace context received by an HTTP control endpoint to the
/// current tracing span. It is a no-op in builds without OTLP support.
pub fn set_remote_parent(span: &tracing::Span, headers: &axum::http::HeaderMap) {
    #[cfg(feature = "otel")]
    {
        use opentelemetry::global;
        use opentelemetry::propagation::Extractor;
        use opentelemetry::trace::TraceContextExt;
        use tracing_opentelemetry::OpenTelemetrySpanExt;

        struct HeaderExtractor<'a>(&'a axum::http::HeaderMap);

        impl Extractor for HeaderExtractor<'_> {
            fn get(&self, key: &str) -> Option<&str> {
                self.0.get(key).and_then(|value| value.to_str().ok())
            }

            fn keys(&self) -> Vec<&str> {
                self.0
                    .keys()
                    .map(axum::http::header::HeaderName::as_str)
                    .collect()
            }
        }

        global::get_text_map_propagator(|propagator| {
            let _ = span.set_parent(propagator.extract(&HeaderExtractor(headers)));
        });
        let span_context = span.context().span().span_context().clone();
        if span_context.is_valid() {
            span.record("trace_id", tracing::field::display(span_context.trace_id()));
            span.record("span_id", tracing::field::display(span_context.span_id()));
        }
    }
    #[cfg(not(feature = "otel"))]
    let _ = (span, headers);
}

/// Inject the current W3C trace context into an outbound HTTP request. This is
/// intentionally owned by the workspace transport boundary so Rust-to-Rust
/// control calls follow the same propagation contract as Python Unix HTTP.
pub fn inject_current_context(headers: &mut axum::http::HeaderMap) {
    #[cfg(feature = "otel")]
    {
        use opentelemetry::global;
        use opentelemetry::propagation::Injector;

        struct HeaderInjector<'a>(&'a mut axum::http::HeaderMap);

        impl Injector for HeaderInjector<'_> {
            fn set(&mut self, key: &str, value: String) {
                let Ok(name) = axum::http::header::HeaderName::from_bytes(key.as_bytes()) else {
                    return;
                };
                let Ok(value) = axum::http::HeaderValue::from_str(&value) else {
                    return;
                };
                self.0.insert(name, value);
            }
        }

        global::get_text_map_propagator(|propagator| {
            propagator.inject_context(
                &opentelemetry::Context::current(),
                &mut HeaderInjector(headers),
            );
        });
    }
    #[cfg(not(feature = "otel"))]
    let _ = headers;
}

/// Mark a completed server span as an operational failure without propagating
/// telemetry concerns into the business result path.
pub fn mark_span_error(span: &tracing::Span, code: &'static str, retryable: bool) {
    span.record("error_code", code);
    span.record("retryable", retryable);
    #[cfg(feature = "otel")]
    {
        use opentelemetry::trace::Status;
        use tracing_opentelemetry::OpenTelemetrySpanExt;

        span.set_status(Status::error(code));
    }
}

/// Install the process-wide structured logger. Repeated calls are harmless so
/// tests and embedded callers can initialize logging without coordination.
pub fn init(component: &'static str) {
    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));
    let registry = tracing_subscriber::registry().with(filter);
    #[cfg(feature = "otel")]
    let _ = build_meter_provider(component);
    #[cfg(feature = "otel")]
    let registry = registry.with(build_otel_layer(component));
    let result = registry
        .with(
            fmt::layer()
                .json()
                .flatten_event(true)
                .with_target(true)
                .with_thread_ids(true)
                .with_thread_names(true)
                .with_current_span(true)
                .with_ansi(false)
                .with_writer(std::io::stderr),
        )
        .try_init();
    if result.is_ok() {
        record_counter("kairos.process.start", 1);
        tracing::info!(
            component,
            event = "logger_initialized",
            "structured logging enabled"
        );
    } else if let Err(error) = result {
        eprintln!("structured logging disabled: {error}");
    }
}

#[cfg(feature = "otel")]
fn build_otel_layer<S>(
    component: &'static str,
) -> Option<tracing_opentelemetry::OpenTelemetryLayer<S, opentelemetry_sdk::trace::SdkTracer>>
where
    S: tracing::Subscriber + for<'span> tracing_subscriber::registry::LookupSpan<'span>,
{
    use opentelemetry::global;
    use opentelemetry::trace::TracerProvider as _;
    use opentelemetry_otlp::{SpanExporter, WithExportConfig};
    use opentelemetry_sdk::{
        propagation::TraceContextPropagator,
        trace::{Sampler, SdkTracerProvider},
        Resource,
    };

    let endpoint = trace_endpoint_from_environment()?;
    let exporter = SpanExporter::builder()
        .with_http()
        .with_endpoint(endpoint.clone())
        .build();
    let exporter = match exporter {
        Ok(exporter) => exporter,
        Err(error) => {
            eprintln!("OpenTelemetry exporter disabled: {error}");
            return None;
        }
    };
    let provider = SdkTracerProvider::builder()
        .with_batch_exporter(exporter)
        // Preserve explicitly sampled remote parents while bounding root-span
        // cost on high-frequency local control paths. Production may override
        // this ratio; a zero ratio still keeps sampled inbound traces linked.
        .with_sampler(Sampler::ParentBased(Box::new(Sampler::TraceIdRatioBased(
            trace_sample_ratio(),
        ))))
        .with_resource(
            Resource::builder_empty()
                .with_attributes(resource_attributes(component))
                .build(),
        )
        .build();
    global::set_text_map_propagator(TraceContextPropagator::new());
    let tracer = provider.tracer("kairos");
    let _ = TRACER_PROVIDER.set(provider.clone());
    global::set_tracer_provider(provider);
    Some(tracing_opentelemetry::layer::<S>().with_tracer(tracer))
}

#[cfg(feature = "otel")]
fn trace_sample_ratio() -> f64 {
    std::env::var("KAIROS_OTEL_TRACE_SAMPLE_RATIO")
        .ok()
        .and_then(|value| value.parse::<f64>().ok())
        .filter(|value| (0.0..=1.0).contains(value))
        .unwrap_or(0.1)
}

#[cfg(feature = "otel")]
fn build_meter_provider(component: &'static str) -> Option<()> {
    use opentelemetry::global;
    use opentelemetry_otlp::{MetricExporter, WithExportConfig};
    use opentelemetry_sdk::{metrics::SdkMeterProvider, Resource};

    let endpoint = metrics_endpoint_from_environment()?;
    let exporter = MetricExporter::builder()
        .with_http()
        .with_endpoint(endpoint)
        .build()
        .ok()?;
    let provider = SdkMeterProvider::builder()
        .with_periodic_exporter(exporter)
        .with_resource(
            Resource::builder_empty()
                .with_attributes(resource_attributes(component))
                .build(),
        )
        .build();
    global::set_meter_provider(provider.clone());
    let _ = METER_PROVIDER.set(provider);
    Some(())
}

#[cfg(feature = "otel")]
fn trace_endpoint_from_environment() -> Option<String> {
    endpoint_from_environment("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "/v1/traces")
}

#[cfg(feature = "otel")]
fn metrics_endpoint_from_environment() -> Option<String> {
    endpoint_from_environment("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "/v1/metrics")
}

#[cfg(feature = "otel")]
fn endpoint_from_environment(signal_variable: &str, signal_path: &str) -> Option<String> {
    resolve_otlp_endpoint(
        std::env::var(signal_variable).ok().as_deref(),
        std::env::var("OTEL_EXPORTER_OTLP_ENDPOINT").ok().as_deref(),
        std::env::var("KAIROS_OTEL_ENABLED").as_deref() == Ok("1"),
        signal_path,
    )
}

fn resolve_otlp_endpoint(
    signal_endpoint: Option<&str>,
    generic_endpoint: Option<&str>,
    enabled: bool,
    signal_path: &str,
) -> Option<String> {
    signal_endpoint
        .filter(|endpoint| !endpoint.trim().is_empty())
        .map(str::to_owned)
        .or_else(|| {
            generic_endpoint
                .filter(|endpoint| !endpoint.trim().is_empty())
                .map(|endpoint| format!("{}{}", endpoint.trim_end_matches('/'), signal_path))
        })
        .or_else(|| enabled.then(|| format!("{DEFAULT_OTLP_BASE_ENDPOINT}{signal_path}")))
}

#[cfg(feature = "otel")]
fn resource_attributes(component: &'static str) -> Vec<opentelemetry::KeyValue> {
    use opentelemetry::KeyValue;

    let mut attributes = vec![
        KeyValue::new("service.name", component),
        KeyValue::new("kairos.component", component),
    ];
    for (environment, attribute) in [
        ("KAIROS_INSTANCE_ID", "service.instance.id"),
        ("KAIROS_INSTANCE_ID", "kairos.instance_id"),
        ("KAIROS_WORKSPACE_ID", "kairos.workspace_id"),
        ("KAIROS_LAUNCH_ID", "kairos.launch_id"),
        ("KAIROS_LAUNCH_MODE", "kairos.launch_mode"),
        ("OTEL_DEPLOYMENT_ENVIRONMENT", "deployment.environment"),
    ] {
        if let Ok(value) = std::env::var(environment) {
            if !value.is_empty() {
                attributes.push(KeyValue::new(attribute, value));
            }
        }
    }
    attributes
}

#[cfg(test)]
mod tests {
    use super::resolve_otlp_endpoint;

    #[test]
    fn signal_specific_endpoint_takes_precedence() {
        assert_eq!(
            resolve_otlp_endpoint(
                Some("http://collector:4318/custom/traces"),
                Some("http://collector:4318"),
                true,
                "/v1/traces",
            )
            .as_deref(),
            Some("http://collector:4318/custom/traces")
        );
    }

    #[test]
    fn generic_endpoint_is_a_base_url() {
        assert_eq!(
            resolve_otlp_endpoint(None, Some("http://collector:4318/"), false, "/v1/metrics")
                .as_deref(),
            Some("http://collector:4318/v1/metrics")
        );
    }

    #[test]
    fn enabled_uses_the_local_default() {
        assert_eq!(
            resolve_otlp_endpoint(None, None, true, "/v1/traces").as_deref(),
            Some("http://127.0.0.1:4318/v1/traces")
        );
    }

    #[test]
    fn disabled_without_an_endpoint_has_no_exporter() {
        assert_eq!(resolve_otlp_endpoint(None, None, false, "/v1/traces"), None);
    }
}
