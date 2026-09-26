# Demo acceptance checklist (NOT production acceptance)

| ID | Verification | Pass rule |
|---|---|---|
| D-01 | Start `python -m app.server` | Browser serves UI at localhost; no external dependency is required. |
| D-02 | September sales question | Returns 44,000 AFN, 4 records, source file name, CSV link. |
| D-03 | Project + budget question | Returns 487,500 AFN spent and 1,250,000 AFN budget; attributes data to the *simulated client API*. |
| D-04 | Expense question | Returns 12,600 AFN and spreadsheet source. |
| D-05 | Unauthorized demo mutation | Request to change/delete/post is denied; no fixture file changes. |
| D-06 | Unsupported scope | No arbitrary SQL, no live network integration, no implication that real auth exists. |
| D-07 | Audit page | Shows read and denied capability invocations during the current run. |
| D-08 | Export | CSV includes header/rows and is labeled as CSV, not native XLSX. |
| D-09 | Responsiveness | Chat, connectors and audit remain usable on narrow browser widths. Requires human visual review. |
| D-10 | Presenter disclosure | Synthetic/mock status visible in UI, README and final handoff. |

### Demo gate
Automated checks and a browser walkthrough plus product owner approval are required. Demo acceptance does not imply production security, correctness or launch readiness. After showing demo, record decisions in `DEMO_FEEDBACK.md` and update the production specification before production implementation.
