"""python examples/quickstart.py

In your app:  from tovano import Tovano; client = Tovano()
"""

from _client import client

from tovano import format_usd

print("Balance:", format_usd(client.balance().available_micro))

# Run an image model and wait for the result.
task = client.tasks.run(
    model="image/flux-1.1-pro",
    input={"prompt": "Product photo of a blue ceramic mug", "n": 2},
    customer="client-0482",  # optional: your own end-customer id
)
print(f"{task.status}: {task.units:g} {task.unit}(s), charged {format_usd(task.charged_micro)}")
for image in task.output.images:
    print(" ", image.url)

print("Balance:", format_usd(client.balance().available_micro))
