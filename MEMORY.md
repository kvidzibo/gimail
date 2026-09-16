# Validated development lessons

- For a stdlib-only project on Debian without `ensurepip`, `python3 -m venv --without-pip .venv` creates a usable local test environment without installing packages.
- IMAP FETCH attributes are unordered: UID/FLAGS can follow a BODY literal. Reassemble each FETCH response's metadata before matching UIDs or reading flags; keep unsolicited FETCH responses separate. Covered by `test_fetch_uid_and_flags_after_literal_with_unsolicited_updates`.
- Validate the SEARCH response container and element types before filtering its bytes; silently discarding malformed elements can turn a protocol error into a false successful empty result. Covered by `test_search_malformed_response_is_not_silent_empty_success`.
- Flush CLI output inside the BrokenPipeError handler's try block; otherwise buffered output may fail only at interpreter shutdown and emit diagnostics. Covered by `test_closed_output_pipe_does_not_emit_a_traceback` on Python 3.13 and 3.14.
