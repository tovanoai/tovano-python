"""python examples/chat_stream.py — stream tokens as they arrive."""

from _client import client

from tovano import format_usd

with client.chat.completions.create(
    model="text/gpt-4.1-mini",
    messages=[{"role": "user", "content": "Write a one-line tagline for a coffee shop."}],
    stream=True,
) as stream:
    for chunk in stream:
        if chunk.choices:
            print(chunk.choices[0].delta.content or "", end="", flush=True)
    result = stream.final_result()

print(
    f"\n\n{result.usage.total_tokens} tokens, charged {format_usd(result.tovano.charged_micro)} (task {stream.task_id})"
)
