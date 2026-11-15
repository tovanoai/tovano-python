"""python examples/quickstart.py

In your app:  from tovano import Tovano; client = Tovano()
"""

from _client import client

from tovano import format_usd

print("Balance:", format_usd(client.balance().available_micro))

# Run an image model and wait for the result.
run = client.runs.run(
    model="image/flux-1.1-pro",
    input={"prompt": "Product photo of a blue ceramic mug", "n": 2},
    customer="client-0482",  # optional: your own end-customer id
)
print(f"{run.status}: {run.units:g} {run.unit}(s), charged {format_usd(run.charged_micro)}")
for image in run.output.images:
    print(" ", image.url)

print("Balance:", format_usd(client.balance().available_micro))
