"""python examples/agent_job.py — an agent job chains models; each step has its own charge."""

from _client import client

from tovano import format_usd

job = client.runs.run(
    model="agent/ad-kit",
    input={"product": "A hand-thrown blue ceramic mug", "audience": "coffee lovers", "tone": "premium"},
)
print("Headline:", job.output.headline)
for step in job.steps or []:
    print(f"  step {step.name or step.step}: {step.model}, {format_usd(step.charged_micro)}")
print("Total:", format_usd(job.charged_micro))
