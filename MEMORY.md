# Validated development lessons

- For a stdlib-only project on Debian without `ensurepip`, `python3 -m venv --without-pip .venv` creates a usable local test environment without installing packages.
- IMAP FETCH attributes are unordered: UID/FLAGS can follow a BODY literal. Reassemble each FETCH response's metadata before matching UIDs or reading flags; keep unsolicited FETCH responses separate. Covered by `test_fetch_uid_and_flags_after_literal_with_unsolicited_updates`.
- Validate the SEARCH response container and element types before filtering its bytes; silently discarding malformed elements can turn a protocol error into a false successful empty result. Covered by `test_search_malformed_response_is_not_silent_empty_success`.
- Flush CLI output inside the BrokenPipeError handler's try block; otherwise buffered output may fail only at interpreter shutdown and emit diagnostics. Covered by `test_closed_output_pipe_does_not_emit_a_traceback` on Python 3.13 and 3.14.
- Parse FETCH attributes structurally: keywords such as `UID`, numeric flags, and nested extension strings can fool a whole-response regex. Covered by `test_fetch_keywords_cannot_spoof_uid_or_size_attributes` and wire tests.
- A tagged OK plus the correct UID does not prove STORE applied a flag. Verify the returned flags before reporting success or expunging a copied source. Covered by `test_store_ignored_flags_cannot_report_success_or_expunge`.
- ASCII credentials may still need SASL PLAIN when the server advertises LOGINDISABLED. Respect pre-authentication capabilities; covered by unit and real-imaplib wire tests.
