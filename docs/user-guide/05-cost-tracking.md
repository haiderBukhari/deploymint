# Cost tracking

Every successful deploy gets a cost estimate, shown on the run page's Cost
card — no setup required for this part.

## The default: deterministic estimates

FinOps reads the actual CPU/memory requests and limits from the generated
Kubernetes manifest and computes a monthly cost estimate from a built-in rate
card. This is plain arithmetic, not an LLM guess, and it comes with concrete
recommendations, e.g.:

- "CPU limit is 5x the request — likely over-provisioned or under-requested."
- "Single replica — no high availability. A second replica costs ~$X/mo more."
- "No HorizontalPodAutoscaler configured — scaling to zero off-peak could cut
  this substantially."

## Live AWS Cost Explorer data

If you set real AWS credentials as environment variables:

```bash
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

FinOps automatically switches from the deterministic estimate to your actual
AWS Cost Explorer spend, broken down by service. No code change, no rebuild
— setting the credentials is the entire activation step. Check `/api/doctor`
(or the dashboard's system status) to confirm which source is active; the
Cost card also shows whether a given run's number came from `estimate` or
real AWS data.

Without AWS credentials set, FinOps falls back to sample cost data
automatically — you always get a working Cost card, never an error.

## Asking questions about cost

The Costs page has a natural-language box — ask things like "which service
costs the most?" and get a direct answer computed from the same cost data,
not a canned response.
