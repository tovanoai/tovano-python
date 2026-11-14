"""python examples/chat.py — OpenAI-compatible chat, one reply."""

from _client import client

from tovano import format_usd

res = client.chat.completions.create(
    model="text/gpt-4.1-mini",
    messages=[
        {"role": "system", "content": "You write short ad copy."},
        {"role": "user", "content": "Three captions for a blue ceramic mug."},
    ],
    max_tokens=200,
)
print(res.choices[0].message.content)
print(
    f"\n{res.usage.prompt_tokens} tokens in, {res.usage.completion_tokens} out, "
    f"charged {format_usd(res.tovano.charged_micro)}"
)
