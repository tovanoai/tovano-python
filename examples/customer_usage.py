"""python examples/customer_usage.py — tag calls with your own customer ids, then see spend per customer."""

from _client import client

from tovano import format_usd

# Per call:
client.tasks.run(model="image/flux-1.1-pro", input={"prompt": "Logo sketch"}, customer="acme")
client.tasks.run(model="image/flux-1.1-pro", input={"prompt": "Banner", "n": 2}, customer="globex")
# Per client: every call from this client is tagged.
acme = client.with_options(customer="acme")
acme.chat.completions.create(model="text/gpt-4.1-mini", messages=[{"role": "user", "content": "Hi"}])

usage = client.usage(group_by="customer")
for row in usage.data:
    print(f"{row.key or '(untagged)':<10} {row.tasks} tasks  {format_usd(row.charged_micro)}")
print("Total this month:", format_usd(usage.total_micro))
