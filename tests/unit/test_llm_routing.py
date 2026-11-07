from __future__ import annotations

from dataclasses import replace

from app.core.config import Settings
from app.llm.models import Route, build_routes, validate_routes


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "bot_token": "123456:secret-token",
        "llm_api_key": "sk-test",
        "llm_model_fast": "mistralai/mistral-nemo",
        "llm_model_smart": "mistralai/mistral-small-24b-instruct-2501",
        "llm_model_fallback": "qwen/qwen3.7-flash",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_routes_use_task_specific_models_and_limits() -> None:
    settings = make_settings(llm_max_output_tokens=700)
    routes = build_routes(settings)

    assert routes[Route.CHAT].model == "mistralai/mistral-nemo"
    assert routes[Route.CORRECTION].model == "mistralai/mistral-small-24b-instruct-2501"
    assert routes[Route.ROLEPLAY].model == "mistralai/mistral-nemo"
    assert routes[Route.SUMMARY].model == "mistralai/mistral-nemo"
    assert routes[Route.JUDGE].model == "mistralai/mistral-small-24b-instruct-2501"
    assert routes[Route.CHAT].max_tokens == 600
    assert routes[Route.CORRECTION].max_tokens == 700
    assert routes[Route.CHAT].fallback == "qwen/qwen3.7-flash"
    assert routes[Route.CHAT].requires_json is True
    assert routes[Route.ROLEPLAY].requires_json is True


def test_route_overrides_are_honored() -> None:
    settings = make_settings(
        llm_model_summary="google/gemini-2.5-flash-lite",
        llm_model_judge="qwen/qwen3-32b",
        llm_model_roleplay="openai/gpt-oss-20b",
        llm_temperature_chat=0.5,
        llm_max_tokens_summary=123,
    )
    routes = build_routes(settings)

    assert routes[Route.SUMMARY].model == "google/gemini-2.5-flash-lite"
    assert routes[Route.JUDGE].model == "qwen/qwen3-32b"
    assert routes[Route.ROLEPLAY].model == "openai/gpt-oss-20b"
    assert routes[Route.CHAT].temperature == 0.5
    assert routes[Route.SUMMARY].max_tokens == 123


def test_validation_accepts_known_cheap_models() -> None:
    result = validate_routes(build_routes(make_settings()))

    assert result.ok
    assert result.errors == ()


def test_validation_warns_about_unknown_models_but_allows_them() -> None:
    result = validate_routes(build_routes(make_settings(llm_model_fast="vendor/unknown")))

    assert result.ok
    assert any("vendor/unknown" in warning for warning in result.warnings)


def test_validation_rejects_duplicate_fallback() -> None:
    routes = build_routes(make_settings())
    routes[Route.CHAT] = replace(routes[Route.CHAT], fallback=routes[Route.CHAT].model)

    result = validate_routes(routes)

    assert not result.ok
    assert any("fallback equals primary" in error for error in result.errors)


def test_global_fallback_equal_to_fast_is_not_used_for_fast_routes() -> None:
    routes = build_routes(
        make_settings(
            llm_model_fast="mistralai/mistral-nemo",
            llm_model_fallback="mistralai/mistral-nemo",
        )
    )

    assert routes[Route.CHAT].fallback is None
    assert routes[Route.ROLEPLAY].fallback is None
    assert routes[Route.CORRECTION].fallback == "mistralai/mistral-nemo"
    assert validate_routes(routes).ok
