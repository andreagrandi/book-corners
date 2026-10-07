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


def test_sentry_traces_sampler_drops_secret_file_probe_paths() -> None:
    """Reject tracing for blocked secret-file probe requests.
    Stops scanner traffic from creating Sentry security findings."""
    sampling_context = {
        "parent_sampled": True,
        "wsgi_environ": {"HTTP_HOST": "bookcorners.org", "PATH_INFO": "/customer/.env"},
    }

    assert _sentry_traces_sampler(sampling_context) is False


def test_sentry_traces_sampler_keeps_real_paths_sampled() -> None:
    """Keep the configured sampling rate for real application paths.
    Ensures genuine request failures stay visible in tracing."""
    sampling_context = {
        "parent_sampled": None,
        "wsgi_environ": {"HTTP_HOST": "localhost:8000", "PATH_INFO": "/api/v1/libraries/"},
    }

    assert _sentry_traces_sampler(sampling_context) == SENTRY_TRACES_SAMPLE_RATE
