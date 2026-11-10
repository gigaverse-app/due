# Runtime contracts

- Preserve direct claim ownership checks, including wrong-model short-circuiting.
- Retain malformed-field rejection without coercion.
- Exercise PostgreSQL callback ordering, rollback, receipts, and fallback.
- Compare retry delays against exact capped arithmetic.
- Require all 68 native and 155 generated cases, including smoke exploration.
- Detect three deliberate runtime faults, then restore and execute the baseline.
- Validate dependency selection and the aggregate CI gate.
