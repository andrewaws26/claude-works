# Search Angles - sample lenses for the demo data set

Each angle is a reusable search lens: a trigger (what the operator might say), a
definition, and the titles to target. The parser in `discovery.py` reads the
numbered sections below. These are sanitized samples; define your own lanes here.

---
## 1. FDE / Converting-Profile  (PRIMARY / default lane)
- **Trigger:** "find jobs", "the usual", "FDE search", "converting roles". Default if no angle specified.
- **Definition:** builder + translator profile; applied-LLM/agents not ML-research; customer/stakeholder-facing; high-agency/first-hire; eval discipline; mid-level.
- **Target titles:** Forward Deployed Engineer, Applied AI Engineer, AI Product Engineer, Solutions Engineer, Demo Engineer, Implementation Engineer, Founding Engineer (LLM/agents).

## 2. IoT / Connected-Ops  (differentiator lane)
- **Trigger:** "IoT jobs", "telematics", "connected-ops", "hardware-AI".
- **Definition:** fleet telematics, IoT, industrial automation, connected-vehicle, edge-AI; roles where a hardware/CAN/edge background is a rare differentiator, not noise.
- **Target titles:** Solutions Engineer (IoT), Forward Deployed Engineer, Implementation Engineer, Field Applications Engineer.

## 3. Fallback-First / Broaden-When-Thin
- **Trigger:** "broaden", "faster offers", "anything I can do".
- **Definition:** prioritize roles the candidate is slightly over-qualified for (SE I/II, junior cloud/AI, implementation) for faster offers; when the converting pool thins, broaden to any honest mid-level engineering role.
- **Target titles:** Software Engineer II, Implementation Engineer, Support Engineer, Customer Engineer.
