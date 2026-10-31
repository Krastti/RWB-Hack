# A-35: conservative semantic impact

Run `python -m backend.app.agents.impact.example` from the repository root.
Dependencies: A-29 #143 / A-30 #145 registration format and A-31 #147 model.
Compiler maps from A-33/A-34 are supported but their absence is explicit unknown.
No graph database, LLM, automatic regeneration, artifact reuse or merge is added.

`build_graph(project_id, snapshot_id, profile, plan, model, requirements,
contexts, outputs, source_maps)` consumes owner-authorized **accepted immutable
snapshots**, not filesystem guesses. Contexts are exact A-30 manifests indexed by
task_id. Outputs are accepted C-02 artifacts with verified inline content; maps
are accepted compiler maps. Actual A-19/B-14 adapters must resolve those snapshots
and authorize project ownership before this call. This module validates content,
checksums, task/stage/owner, plan grants and map output checksums; it cannot grant
storage access or prove a supplied dictionary was accepted upstream.

Nodes retain stable FR/rule/entity/operation/screen/file identities and concrete
schema/content versions. Edges include actual registered artifact inputs, profile
and rules, prompt, template, compiler/model configurations, contract version,
plan dependencies and rule→symbol mappings. Task IDs/stage IDs are validated by
the accepted C-02/C-05 plan; agent name is not a readiness key. Physical bindings
and finite role/identity domain changes are inputs too.

`compare(old, new, profile, accepted_plan)` compares snapshots of the same project.
It walks the union of old/new edges (including removed rules/entities) and retains
explanatory chains. Changed dependency edges are inputs themselves. Missing
history, incomplete maps, stale model maps, unknown schema or dangling edges
conservatively expand to all accepted tasks. `unchanged_recorded_inputs` only
means no difference in the recorded dependencies, never safe automatic reuse.
The impact plan contains snapshot IDs/hashes/schema versions and required input IDs.

Examples explicitly use synthetic accepted manifests/outputs, then immutable graph
revisions: ownership change affects frontend and backend; theme-only input changes
frontend; removed entity exposes dangling bindings; missing/incompatible history
expands both. Unchanged snapshot yields no tasks. These examples verify dependency
calculation, not actual generation or business extraction.

`save_plan` publishes `contracts/impact-plan.json` using the existing worker-fenced
ArtifactStore atomic receipt API; conflicting replay is rejected. Architecture owns
this path under the current contracts grant. `save_graph` likewise persists `contracts/dependency-graph.json` with its own
StageAttempt receipt. Persist it with the snapshot; never reconstruct old graphs
from current defaults. The new schemas are additive and require owner review.

Scope limits: A-09/A-19/B-14 actual storage/registration integration is not in main;
that wiring and real project snapshots remain acceptance work. The PR is draft.
Schema compatibility unknowns are conservative; the first version intentionally
does not claim minimal regeneration scope when history is incomplete. Default
pipeline behavior is unchanged; omit this opt-in planner to roll back.
