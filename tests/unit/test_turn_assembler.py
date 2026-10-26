from services.agent_gateway_v3.app.services.turn_assembler import TurnAssembler


def test_turn_assembler_collects_text_and_usage() -> None:
    assembler = TurnAssembler(model_name="gpt-6-luna")
    event = {
        "usage_metadata": {
            "prompt_token_count": 10,
            "candidates_token_count": 8,
            "thoughts_token_count": 3,
            "total_token_count": 18,
        }
    }
    assembler.add_event(event)
    assembler.add_text("hello")
    assembler.add_text(" world")

    assert assembler.reply_text() == "hello world"
    assert assembler.usage["token_counts"]["total_token_count"] == 18
    assert assembler.usage["billable_tokens"]["output_including_thinking"] == 8
    assert assembler.usage["estimated_cost_usd"] == 0.000005
    assert assembler.usage["cache_write_reporting_complete"] is True
    assert assembler.usage["usage_event_count"] == 1
    assert assembler.raw_events == [event]


def test_turn_assembler_sums_each_adk_model_call_and_deduplicates_event_ids() -> None:
    assembler = TurnAssembler(model_name="gpt-6-luna")
    event = {
        "id": "llm-response-1",
        "model_version": "openai/gpt-6-luna",
        "usage_metadata": {
            "prompt_token_count": 100,
            "candidates_token_count": 10,
            "total_token_count": 110,
            "cached_content_token_count": 20,
            "cache_write_tokens": 30,
        },
    }
    assembler.add_event(event)
    assembler.add_event(event)  # same ADK event must not be charged twice
    assembler.add_event(
        {
            "id": "llm-response-2",
            "model_version": "gpt-6-luna",
            "usage_metadata": {
                "prompt_token_count": 100,
                "candidates_token_count": 10,
                "total_token_count": 110,
                "cached_content_token_count": 20,
                "cache_write_tokens": 30,
            },
        }
    )

    assert assembler.usage["usage_event_count"] == 2
    assert assembler.usage["token_counts"]["prompt_token_count"] == 200
    assert assembler.usage["token_counts"]["total_token_count"] == 220
    assert assembler.usage["billable_tokens"] == {
        "input_text_image_video": 200,
        "input_uncached": 100,
        "input_cached": 40,
        "input_cache_write": 60,
        "output_including_thinking": 20,
    }
    assert assembler.usage["estimated_cost_usd"] == 0.0000279
    assert assembler.usage["estimated_cost_complete"] is True
