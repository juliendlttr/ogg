# Changelog

## [1.0.4]

### Added
- `if_exists` on create methods accepts three new values besides `'fail'` (default) and `'skip'`. Each acts only when the create returns 409 (resource already exists):
  - `'update'`: PUT endpoints resend the full body. PATCH endpoints read the resource first and send only the fields that changed, plus `$schema` and `type` when present (schemas such as `ogg:encryptionProfile` need them). Returns `{'status': 'updated', ...}`, or `{'status': 'unchanged', ...}` without any request when nothing differs.
  - `'recreate'`: delete, then create again. Returns `{'status': 'recreated', ...}`. An extract's trail files are purged between the two (`delete_extract` leaves them on disk), and extracts and replicats wait `recreate_wait_seconds` (default 20, settable per client) before the create. A user is deleted on its role and on the undocumented `all` role, without which the create keeps returning 409. The checkpoint table is recreated with `operation: delete`, then `add`.
  - `'verify'`: reads the resource and raises `RuntimeError` naming each field that differs from the request; returns `{'status': 'verified', ...}` otherwise. `$schema`, `status` and secrets (`password`, `credential`) are not compared. A requested top-level field missing from the resource counts as a difference. A list of strings (e.g. `config`) passes when every requested line is present.
- `IF_EXISTS_VALUES` class attribute lists the accepted values.
- `get_extract_lag(extract)`, `get_replicat_lag(replicat)`, `get_distribution_path_lag(distpath)`: lag in seconds, `lag` + `sinceLagReported` by default, since the reported lag stops changing once a process stops updating it (an abended extract keeps reporting its last value). `include_since_reported=False` returns the raw `lag`. `None` when no lag is reported.
- `create_managed_process_profile(profile, auto_start=False, auto_start_delay=0, auto_restart=False, restart_on_success=False, restart_delay=0, restart_retries=9, restart_window=60, disable_on_failure=True, description=None, is_default=False, if_exists='fail')`: create a profile from named settings instead of a raw `data` dict (defaults match the server's). Like `ADD PROFILE` in the `adminclient`, it writes both the `ogg:managedProcessSettings` value and its `ogg:configDataDescription` entry. With `is_default=True`, the previous default loses its `isDefault` flag.
- `delete_managed_process_profile(profile)`: delete a profile and its `ogg:configDataDescription` entry.
- `purge_trail(trail, path=None, keep_files=0, keep_hours=None, keep_days=None, use_checkpoints=True)`: purge trail files through `execute_command` (`purge` / `trails`), available on 19c, 23ai and 26ai. Files are purged from the oldest sequence forward. With `use_checkpoints=True` the purge stops before the files still read by a replicat or distribution path, and aborts if a process using the trail has no valid CSN (OGG-30661) or its extract is not running (OGG-30827). `path` is required for a trail in a subdirectory. `use_checkpoints=False` also deletes the file a running extract is writing.
- `managed_path_settings` argument on `create_distribution_path`, `update_distribution_path`, `create_receiver_path` and `update_receiver_path` (GoldenGate 23.26.4.0.0): name of a managed process profile that sets the auto-restart of the path. The server refuses it together with explicit auto-restart `options` (`OGG-30812`). The client is generated with `--ogg-home` of that release, so it also gains `type` on the credential methods, `collector_compact_queue_limit`, `failover_slot`, and `model_path` and `tokenizer_path` on the AI service models.

### Changed
- `if_exists` raises `ValueError` for an unknown value (a typo such as `'Skip'` used to behave as `'fail'`) and for a value the endpoint cannot honor, e.g. `'update'` on `create_config_type` (no PUT or PATCH on `/config/types/{type}`; the server answers 405).
- `create_master_key_version`, `add_cluster_member` and `create_configuration_service_backend` no longer take `if_exists`: their POST names no resource, so it never returns 409 (`/masterkey` adds a new version on every call, `memberAdd` is an action, `backends` is a POST to the collection).
- `if_exists='recreate'` raises `ValueError` on `create_deployment` and `create_cluster`: deleting either removes everything it contains (a deployment's processes, the whole cluster).
- Service Manager AI service methods drop `installation_`, e.g. `create_installation_ai_service_provider` -> `create_ai_service_provider`. Full list: `create_`, `get_`, `update_`, `delete_` for `ai_service_provider` and `ai_service_model`, `list_ai_service_providers`, `list_ai_service_models`, `get_ai_service_health`. The Administration Server's read-only `list_ai_service_models` / `get_ai_service_model` (models available to one deployment) are renamed to `list_deployment_ai_service_models` / `get_deployment_ai_service_model` to free those names. The old names are removed, with no aliases.
- Every error response now raises `RuntimeError` (`ERROR (code NNN) - <url>: ...`). An error whose body has an empty `messages` list, as the AI service endpoints return, used to raise `requests.HTTPError` instead.

### Fixed
- `_extract_main` no longer fails with `TypeError` on an empty response body, e.g. an AI service DELETE.

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
- These new parameters arrive before `raw_response` (and, on a few methods, before `ogg_service`) in the signature, shifting positional arguments in the affected methods - pass by keyword.
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
