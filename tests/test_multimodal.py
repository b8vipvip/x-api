from x_api import (
    TASK_AUDIO,
    TASK_IMAGE,
    TASK_TEXT,
    TASK_VISION,
    build_chat_messages,
    detect_task,
    infer_audio_protocol,
)


def test_actual_media_wins_auto_detection() -> None:
    assert detect_task("分析一下", has_images=True)[0] == TASK_VISION
    assert detect_task("听听", has_audio=True)[0] == TASK_AUDIO


def test_specialized_prompt_detection() -> None:
    assert detect_task("帮我生成一张蓝天白云图片")[0] == TASK_IMAGE
    assert detect_task("用语音回复我")[0] == TASK_AUDIO
    assert detect_task("如何开发一个生成图片 API")[0] == TASK_TEXT


def test_explicit_task_override() -> None:
    task, explicit = detect_task("生成图片", requested_task="text")
    assert task == TASK_TEXT
    assert explicit is True


def test_multimodal_message_builder() -> None:
    messages = build_chat_messages(
        "system",
        "describe",
        images=[{"url": "data:image/png;base64,AA==", "detail": "low"}],
        audio={"data": "ZmFrZQ==", "format": "wav"},
    )
    parts = messages[-1]["content"]
    assert any(part.get("type") == "image_url" for part in parts)
    assert any(part.get("type") == "input_audio" for part in parts)


def test_audio_protocol_inference() -> None:
    assert infer_audio_protocol({"audio_protocol": "auto"}, "gpt-4o-mini-tts") == "speech"
    assert infer_audio_protocol({"audio_protocol": "auto"}, "gpt-audio") == "chat_audio"
    assert infer_audio_protocol({"audio_protocol": "auto"}, "gpt-realtime") == "realtime"
