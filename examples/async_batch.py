"""python examples/async_batch.py — run several models at once with asyncio."""

import asyncio

from _client import async_client

from tovano import format_usd


async def main() -> None:
    async with async_client() as client:
        video, voice, images = await asyncio.gather(
            client.runs.run(
                model="video/kling-2.1-pro", input={"prompt": "Waves on a beach at sunrise", "duration": 5}
            ),
            client.runs.run(
                model="audio/eleven-multilingual-v2", input={"text": "Fresh coffee, every morning.", "voice": "rachel"}
            ),
            client.runs.run(model="image/flux-1.1-pro", input={"prompt": "Blue mug on oak", "n": 3}),
        )
        print("Video:", video.output.video.url, format_usd(video.charged_micro))
        print("Voice:", voice.output.audio.url, format_usd(voice.charged_micro))
        print("Images:", len(images.output.images), format_usd(images.charged_micro))

        stream = await client.chat.completions.create(
            model="text/gpt-4.1-mini", messages=[{"role": "user", "content": "A tagline, please."}], stream=True
        )
        async for chunk in stream:
            if chunk.choices:
                print(chunk.choices[0].delta.content or "", end="", flush=True)
        print()


asyncio.run(main())
