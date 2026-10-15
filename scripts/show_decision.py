"""Formats `orchestra decide` JSON from stdin as one readable line, for the demo."""

import json
import sys

result = json.load(sys.stdin)
if result["status"] == "decided":
    outcome = result["choice"]
else:
    outcome = f"abstain ({result['reason']}), suggested {result['suggested']}"
print(f"  {result['decision']:8} -> {outcome}   confidence {result['confidence'] or 0:.2f}, {result['latency_ms']} ms")
