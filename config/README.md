# Source-bound architecture policies

The web-fastapi-angular MVP no longer requires a per-domain policy file. After
analysis, the model proposes routes and API/storage names. Server code derives
bearer permissions from accepted role capabilities with matching FR evidence,
preserves every security requirement, and validates coverage and name bindings.
The model cannot supply grants or authorization rules. Unsupported/ambiguous
permissions become a required question through the existing answers/resume path.
The first implementation recognizes explicit Russian/English CRUD capabilities;
it does not claim arbitrary-language semantic verification. Unknown business
actions require clarification or a reviewed server policy.

The accepted policy is bound to the exact requirements checksum and stored in an
immutable receipt before A-09. Retries use that receipt and the frozen attempt
input, not a newly edited file. Calls use the shared ledger and budget. Other
profiles retain their explicit policy requirement.

## Optional reviewed override

Compose mounts this directory at `/app/config` in the API. Set
`PIPELINE_POLICY_FILE=/app/config/architecture-policies.local.json` in `.env`.
The API freezes the selected policies when a Run is created; changing this file
affects new Runs only.

`tickets-policy.example.json` is an explicit policy for the repair-ticket demo:
four FRs (create, read own, dispatch/read and change status, prohibit deletion),
two ordered roles, and Ticket/TicketCreate/TicketStatusUpdate API models mapped
to tickets. Copy it to `architecture-policies.local.json` only for that domain;
do not use that example for unrelated business requirements. An explicit reviewed
override disables automatic preparation for that Run; it remains frozen at create.

A null `requirements_checksum` binds to the actual accepted requirements bytes.
An exact authorization rule `{{security_requirement:0}}` binds to the text of
accepted security requirement 0. The index must also be in that operation's
`security_requirement_indices`. Arbitrary strings are not expanded, and all
normal API/security/naming validation still runs after binding.
