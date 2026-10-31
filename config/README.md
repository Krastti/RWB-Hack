# Server-approved architecture policies

Compose mounts this directory at `/app/config` in the API. Set
`PIPELINE_POLICY_FILE=/app/config/architecture-policies.local.json` in `.env`.
The API freezes the selected policies when a Run is created; changing this file
affects new Runs only.

`tickets-policy.example.json` is an explicit policy for the repair-ticket demo:
four FRs (create, read own, dispatch/read and change status, prohibit deletion),
two ordered roles, and Ticket/TicketCreate/TicketStatusUpdate API models mapped
to tickets. Copy it to `architecture-policies.local.json` only for that domain;
other business requirements require their own reviewed policy.

A null `requirements_checksum` binds to the actual accepted requirements bytes.
An exact authorization rule `{{security_requirement:0}}` binds to the text of
accepted security requirement 0. The index must also be in that operation's
`security_requirement_indices`. Arbitrary strings are not expanded, and all
normal API/security/naming validation still runs after binding.
