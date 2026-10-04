# Contributing

Thanks for helping. Before opening a pull request:

1. Set up the backend as described in the README and run `pytest` from `backend/`.
2. Add or update tests for any behaviour you change. Solver changes should be checked
   against `validate.find_conflicts`, which is intentionally independent of the solver.
3. Use only fictional data in tests, docs and examples.
4. Keep pull requests focused: one feature or fix each.

## Ideas that need help

- Excel import templates and validation messages
- Additional constraints (gap minimisation, faculty preferences, spread across the week)
- Regional or university-specific rules (credit-to-hours mappings, session patterns)
- UI work once the API lands
