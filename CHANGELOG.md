# Changelog

## [1.0.3]

### Added
- `get_current_extract_report(extract)` / `get_current_replicat_report(replicat)`: fetch the current `<name>.rpt` report (falls back to the first `.rpt` listed; returns `None` if no report exists).
- `get_extract_status_detail(extract)` / `get_replicat_status_detail(replicat)`: send the `STATUS` command through `execute_command_extract`/`execute_command_replicat` and return the detailed status string (e.g. `"At EOF"`, `"Suspended"`); the full response is returned when `raw_response=True`.
- `is_extract_suspended(extract)` / `is_replicat_suspended(replicat)`: `True` when the detailed status contains "suspended".
- `is_replicat_at_eof(replicat)`: `True` when the replicat's detailed status is "At EOF".
- `get_extract_logend(extract)` / `is_extract_logend(extract)` / `is_replicat_logend(replicat)`: send the `LOGEND` command and return `replyData.allRecordsProcessed` as a `bool`.
- `start_replicat_aftercsn(replicat, csn)`: start a Replicat AFTERCSN via `POST /commands/execute` (`{"name": "start", "processType": "replicat", "processName": ..., "after": csn}`). This is the only way the REST API can start a Replicat from a CSN.
- `get_service_port(deployment, service)`: return a service's listening port as reported by the Service Manager; raises `RuntimeError` if no port is found.
- New optional query parameters. These endpoints previously accepted no query parameters; each new parameter is sent only when it is not `None`:
  - `delete_configuration_service_backend`: `delete_data` (`deleteData`)
  - `delete_trail_sequence`: `force`, `path`
  - `delete_trail_sequence_collection`: `force`, `first`, `last`, `path`
  - `exchange_auth_code_for_token`: `code`
  - `get_data_stream`: `begin`
  - `get_extract_checkpoint` / `get_replicat_checkpoint`: `history`
  - `get_extract_diagnostic` / `get_replicat_diagnostic`: `started`
  - `get_heartbeat_data`: `q`, `limit`, `offset`
  - `get_monitoring_messages`, `list_status_changes`, `list_process_messages`, `list_process_status_changes`: `from_id` (`fromID`), `to_id` (`toID`), `offset`, `limit`
  - `get_trail`, `update_trail`, `delete_trail`: `path_query` (`path`; named `path_query` so it does not clash with the `path` body field of `update_trail`)
  - `get_trail_sequence`: `key_name` (`keyName`), `download`, `path`
  - `list_database_names`, `list_database_schemas`, `list_database_tables`: `name`
  - `list_extracts` / `list_replicats`: `threads`
  - `list_installation_deployments`: `deployment`
  - `list_installation_services`: `deployment`, `service`
  - `list_installation_plugins`: `function`
  - `list_receiver_paths`: `target_initiated` (`targetInitiated`)
  - `list_trail_sequences`: `path`
  - `list_trails`: `details`
- These new parameters land before `raw_response` (and, on a few methods, before `ogg_service`) in the signature, shifting positional arguments in the affected methods - pass by keyword.
- Internal `_call()` accepts `query_params`, which is merged into `params` with `None` values dropped.

### Changed
- 37 methods drop the `process_` prefix, e.g. `get_process_info` -> `get_info`, `get_process_statistics_extract` -> `get_statistics_extract`. Full list: `batch_sql_statistics`, `br_extant_object_ages`, `br_extant_object_sizes`, `br_object_ages`, `br_object_sizes`, `br_pools_info`, `br_status`, `cache_statistics`, `coordination_replicat`, `database_in_out`, `dependency_stats`, `distsrvr_chunk_stats`, `distsrvr_network_stats`, `distsrvr_path_stats`, `distsrvr_table_stats`, `heartbeat`, `info`, `network_statistics`, `parallel_replicat`, `performance`, `pmsrvr_proc_stats`, `pmsrvr_stats`, `pmsrvr_worker_stats`, `position_er`, `queue_bucket_statistics`, `queue_statistics`, `recvsrvr_stats`, `statistics_extract`, `statistics_procedure_extract`, `statistics_procedure_replicat`, `statistics_replicat`, `statistics_table_extract`, `statistics_table_replicat`, `superpool_statistics`, `thread_performance`, `trail_input`, `trail_output` (each was `get_process_<name>` and is now `get_<name>`). The old names are removed, with no aliases. `get_process_service_health` and `get_process_heartbeat_records` are unchanged: the former would otherwise collide with the existing, unrelated `get_service_health` (`GET /config/health`, overall deployment health, not a single process's); the latter takes a real `process` argument (`GET /connections/{connection}/tables/heartbeat/{process}`), so "process" there names an actual parameter, not a leftover prefix.
- `encrypt_data` parameter `data_1` renamed to `data_to_encrypt`.
- Output now goes through `logging` instead of `print()`. Progress messages, including `verbose=True`'s connection message, log at `INFO`, hidden unless you configure logging.
- `patch_deployment`: listing failures now only skip on `RuntimeError`/`RequestException`, not any exception.

### Fixed
- `_service_base_url` / `auto_discovery` port parsing handles `serviceListeningPort` returned as a `dict` (`{"port": N}`) as well as an `int` or a `list`.
- `start_replicat` docstring no longer says `begin` accepts `{"at": csn}` / `{"after": csn}`. Replicat's `begin` has no CSN form, and the docstring now points to `start_replicat_aftercsn()`.
- `list_messages` docstring now states that it retrieves `ggserr.log`.
- `create_deployment_certificate` docstring example: PEM strings now use literal `\n` line endings.
