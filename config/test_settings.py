from config.settings import SENTRY_TRACES_SAMPLE_RATE, _sentry_traces_sampler


def test_sentry_traces_sampler_drops_invalid_request_host() -> None:
    """Reject tracing when the request host is not allowed by Django.
    Prevents direct-IP scanner requests from creating performance issues."""
    sampling_context = {
        "parent_sampled": True,
        "wsgi_environ": {"HTTP_HOST": "46.225.3.98"},
    }

    assert _sentry_traces_sampler(sampling_context) is False


def test_sentry_traces_sampler_preserves_valid_request_sampling() -> None:
    """Keep the configured sampling rate for a valid request host.
    Ensures legitimate request failures remain eligible for tracing."""
    sampling_context = {
        "parent_sampled": None,
        "wsgi_environ": {"HTTP_HOST": "localhost:8000"},
    }

    assert _sentry_traces_sampler(sampling_context) == SENTRY_TRACES_SAMPLE_RATE


def test_sentry_traces_sampler_preserves_parent_sampling() -> None:
    """Honor an upstream sampling decision for non-request work.
    Keeps the SDK's existing distributed-tracing behavior unchanged."""
    sampling_context = {"parent_sampled": True}

    assert _sentry_traces_sampler(sampling_context) is True

