# Spike: `/v1/systemone` (2026-10-15)

Tested against Ollama 0.40.3 with `strands-decider:2b` and `laya`. Fixtures are in `decider/tests/fixtures/systemone/`.

## Request

`POST /v1/systemone` with `model`, `state` (string, object or array), `questions` (1 to 64 named entries) and optional `keep_alive`.

| Type | `criteria` |
|---|---|
| `choice` | object, option → description or `null`. Ollama enforces 2 to 26 candidates (docs say 255) |
| `noul` | optional object with `"true"` and `"false"` descriptions |
| `score` | array of 2 to 10 strings, lowest to highest |

## Response

`answers.<name>` per question.

- `choice`: `choice`, `probabilities`, `confidence`
- `noul`: `noul` (P(true)), no confidence
- `score`: `score` (expected index), `legend`, `probabilities`, `confidence`

`confidence` is 1 − normalised entropy, not the top probability (tier on Strands: top p 0.65, confidence 0.21). Extra top-level keys appear (`prompt_eval_cached_count`), so parsers must ignore unknown fields.

## Errors

JSON `{"error": "..."}` with 400 (bad question, model lacks the `decision` capability) or 404 (model not pulled).

## Latency (M-series laptop, warm)

Strands Decider 2B is 8 to 80 ms for a five-question request, and Laya is around 110 ms. The first call after load is 1.5 to 3.5 s.

## Design consequences

- The panel decision is one `noul` per reviewer perspective, batched in one request.
- The decider computes confidence itself (1 − normalised entropy, binary entropy for `noul`) so thresholds mean the same across backends.
- llama.cpp 0.6.0 is untested. Ollama stores models as split tensor blobs with no GGUF that llama-server can load, so this needs a separate GGUF from Hugging Face.
- On an OAuth rewrite both models chose `tier=single` with low confidence (0.21 and 0.11), so they would abstain. Rubric wording and the golden set matter.
