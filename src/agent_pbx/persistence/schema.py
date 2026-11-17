from __future__ import annotations

import sqlite3
from typing import Protocol


class SchemaStore(Protocol):
    """Compatibility surface required by schema bootstrap orchestration."""

    def connect(self) -> sqlite3.Connection: ...

    def _schema_version(self, conn: sqlite3.Connection) -> int: ...

    def _backfill_latest_report_seen(self, conn: sqlite3.Connection) -> None: ...

    def _backfill_agent_type(self, conn: sqlite3.Connection) -> None: ...

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None: ...

    def _rebuild_operator_kb_fts(self, conn: sqlite3.Connection) -> None: ...

    def _rebuild_operator_kb_semantic_chunks(
        self,
        conn: sqlite3.Connection,
    ) -> None: ...


def initialize_schema(store: SchemaStore, *, schema_version: int) -> None:
    """Create or upgrade the database schema through the Store compatibility facade."""

    with store.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tokens (
                token_hash TEXT PRIMARY KEY,
                token_id TEXT,
                kind TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'controller',
                label TEXT,
                audience TEXT,
                client_id TEXT,
                scopes_json TEXT NOT NULL DEFAULT '[]',
                allowed_agent_ids_json TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL,
                expires_at REAL,
                revoked_at REAL,
                last_used_at REAL
            );

            CREATE TABLE IF NOT EXISTS remote_client_sessions (
                client_id TEXT PRIMARY KEY,
                token_id TEXT,
                role TEXT NOT NULL,
                audience TEXT,
                allowed_agent_ids_json TEXT NOT NULL DEFAULT '[]',
                view_state_json TEXT NOT NULL DEFAULT '{}',
                cursor INTEGER NOT NULL DEFAULT 0,
                connected_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                disconnected_at REAL
            );

            CREATE TABLE IF NOT EXISTS remote_audit_events (
                audit_id TEXT PRIMARY KEY,
                client_id TEXT,
                token_id TEXT,
                role TEXT NOT NULL,
                action TEXT NOT NULL,
                target_agent_id TEXT,
                outcome TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pairing_codes (
                code_hash TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                used_at REAL
            );

            CREATE TABLE IF NOT EXISTS agents (
                agent_id TEXT PRIMARY KEY,
                agent_type TEXT NOT NULL DEFAULT 'caller',
                project TEXT NOT NULL,
                name TEXT,
                status TEXT NOT NULL,
                pbx_active INTEGER NOT NULL DEFAULT 1,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                last_poll_at REAL,
                latest_report_seen_at REAL,
                starred_at REAL,
                dismissed_at REAL
            );

            CREATE TABLE IF NOT EXISTS reports (
                report_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                project TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT NOT NULL,
                detail TEXT NOT NULL,
                needs_input INTEGER NOT NULL DEFAULT 0,
                plan_options_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
            );

            CREATE TABLE IF NOT EXISTS commands (
                command_id TEXT PRIMARY KEY,
                agent_id TEXT,
                type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                claimed_at REAL,
                acked_at REAL,
                result_json TEXT,
                FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
            );

            CREATE TABLE IF NOT EXISTS poll_events (
                poll_id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                delivered_count INTEGER NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
            );

            CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                subject_id TEXT,
                payload_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS model_elevation_leases (
                lease_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                requested_profile TEXT NOT NULL,
                prior_profile TEXT,
                justification TEXT NOT NULL,
                scope TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                requested_at REAL NOT NULL,
                approved_at REAL,
                activated_at REAL,
                expires_at REAL,
                finished_at REAL,
                approved_by TEXT,
                transition_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
            );

            CREATE INDEX IF NOT EXISTS idx_model_elevation_agent_status
                ON model_elevation_leases(agent_id, status, requested_at DESC);

            -- The TUI refreshes these per-agent summaries frequently. Without
            -- these indexes, list_agents() scans the complete reports,
            -- commands, and poll_events tables once for every returned agent.
            CREATE INDEX IF NOT EXISTS idx_reports_agent_created
                ON reports(agent_id, created_at DESC, report_id DESC);
            CREATE INDEX IF NOT EXISTS idx_commands_status_agent_created
                ON commands(status, agent_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_commands_agent_created
                ON commands(agent_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_poll_events_agent_created
                ON poll_events(agent_id, created_at);

            CREATE TABLE IF NOT EXISTS tmux_runtime_mappings (
                entity_id TEXT PRIMARY KEY,
                server_mode TEXT NOT NULL,
                server_id TEXT NOT NULL,
                socket_path TEXT NOT NULL,
                session_name TEXT NOT NULL,
                window_id TEXT,
                window_name TEXT,
                pane_id TEXT NOT NULL,
                pane_pid INTEGER,
                process_start_ticks INTEGER,
                codex_session_id TEXT,
                cwd TEXT,
                origin_client_tty TEXT,
                origin_session_name TEXT,
                state TEXT NOT NULL DEFAULT 'registered',
                writer_client_id TEXT,
                writer_lease_expires_at REAL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(entity_id) REFERENCES agents(agent_id)
            );

            CREATE INDEX IF NOT EXISTS idx_tmux_runtime_server_pane
                ON tmux_runtime_mappings(server_id, pane_id);

            CREATE TABLE IF NOT EXISTS terminal_editor_runtimes (
                runtime_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL UNIQUE,
                project TEXT NOT NULL,
                access_mode TEXT NOT NULL DEFAULT 'edit',
                profile_id TEXT NOT NULL,
                path TEXT NOT NULL,
                root TEXT NOT NULL,
                socket_path TEXT NOT NULL,
                rpc_socket_path TEXT,
                rpc_enabled INTEGER NOT NULL DEFAULT 0,
                server_id TEXT NOT NULL,
                session_name TEXT NOT NULL,
                window_id TEXT,
                pane_id TEXT NOT NULL,
                pane_pid INTEGER,
                process_start_ticks INTEGER,
                tmux_bin TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'ready',
                last_error TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                last_verified_at REAL,
                FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_terminal_editor_root_state
                ON terminal_editor_runtimes(root, state, updated_at DESC);

            CREATE TABLE IF NOT EXISTS runtime_migration_batches (
                batch_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'previewed',
                target_cli_version TEXT,
                retention_until REAL NOT NULL,
                snapshot_json TEXT NOT NULL DEFAULT '{}',
                results_json TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL,
                applied_at REAL,
                rolled_back_at REAL
            );

            CREATE INDEX IF NOT EXISTS idx_runtime_migration_retention
                ON runtime_migration_batches(retention_until, status);

            CREATE TABLE IF NOT EXISTS workerbee_workspace_policies (
                scope TEXT PRIMARY KEY,
                policy TEXT NOT NULL,
                request_mode TEXT NOT NULL,
                allowed_roots_json TEXT NOT NULL DEFAULT '[]',
                managed_scratch_root TEXT,
                profile_label TEXT,
                updated_by TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workerbee_snapshot_policies (
                scope TEXT NOT NULL,
                source_root TEXT NOT NULL,
                policy TEXT NOT NULL,
                include_paths_json TEXT NOT NULL DEFAULT '[]',
                max_bytes INTEGER NOT NULL,
                max_files INTEGER NOT NULL,
                max_file_bytes INTEGER NOT NULL,
                profile_label TEXT,
                updated_by TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(scope, source_root)
            );

            CREATE INDEX IF NOT EXISTS idx_workerbee_snapshot_policy_root
                ON workerbee_snapshot_policies(source_root, scope);

            CREATE TABLE IF NOT EXISTS workerbee_workspace_transitions (
                transition_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                lease_id TEXT NOT NULL,
                from_binding_id TEXT NOT NULL,
                to_binding_id TEXT,
                source_root TEXT NOT NULL,
                working_subpath TEXT NOT NULL DEFAULT '.',
                base_project TEXT NOT NULL,
                runtime_project TEXT NOT NULL,
                scenario_label TEXT,
                policy TEXT NOT NULL,
                request_mode TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                requested_by TEXT,
                approved_by TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                approved_at REAL,
                applied_at REAL,
                reverted_at REAL,
                failed_at REAL
            );

            CREATE INDEX IF NOT EXISTS idx_workerbee_workspace_transition_agent
                ON workerbee_workspace_transitions(agent_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_workerbee_workspace_transition_lease
                ON workerbee_workspace_transitions(lease_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_workerbee_workspace_transition_status
                ON workerbee_workspace_transitions(status, created_at DESC);

            CREATE TABLE IF NOT EXISTS operator_workspace_set_versions (
                set_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                label TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                collections_json TEXT NOT NULL DEFAULT '[]',
                members_json TEXT NOT NULL DEFAULT '[]',
                member_digest TEXT NOT NULL,
                updated_by TEXT,
                created_at REAL NOT NULL,
                PRIMARY KEY(set_id, version)
            );

            CREATE INDEX IF NOT EXISTS idx_operator_workspace_set_latest
                ON operator_workspace_set_versions(set_id, version DESC);

            CREATE TABLE IF NOT EXISTS operator_authority_assignments (
                agent_id TEXT PRIMARY KEY,
                profile TEXT NOT NULL,
                profile_label TEXT NOT NULL,
                workspace_set_id TEXT,
                workspace_set_version INTEGER,
                workspace_set_digest TEXT,
                updated_by TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_operator_authority_profile
                ON operator_authority_assignments(profile, updated_at DESC);

            CREATE TABLE IF NOT EXISTS joplin_logs (
                log_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                project TEXT NOT NULL,
                session_id TEXT NOT NULL,
                note_id TEXT NOT NULL,
                title TEXT NOT NULL,
                active INTEGER NOT NULL,
                started_at REAL NOT NULL,
                stopped_at REAL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
            );

            CREATE TABLE IF NOT EXISTS joplin_sync_jobs (
                sync_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                reason TEXT NOT NULL,
                agent_id TEXT,
                note_id TEXT,
                error TEXT,
                created_at REAL NOT NULL,
                started_at REAL,
                finished_at REAL,
                attempts INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
            );

            CREATE TABLE IF NOT EXISTS joplin_edit_sessions (
                edit_id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                agent_id TEXT,
                project TEXT,
                note_id TEXT NOT NULL,
                base_revision TEXT NOT NULL,
                base_updated_time REAL,
                base_user_updated_time REAL,
                base_title_hash TEXT NOT NULL,
                base_body_hash TEXT NOT NULL,
                base_title TEXT NOT NULL,
                base_body TEXT NOT NULL,
                draft_title TEXT NOT NULL,
                draft_body TEXT NOT NULL,
                dirty INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'editing',
                sync_generation INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                resolved_at REAL,
                FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_joplin_edit_active_client_note
                ON joplin_edit_sessions(client_id, scope_key, note_id)
                WHERE status IN ('editing', 'conflict');
            CREATE INDEX IF NOT EXISTS idx_joplin_edit_note_status
                ON joplin_edit_sessions(note_id, status, updated_at DESC);

            CREATE TABLE IF NOT EXISTS joplin_note_conflicts (
                conflict_id TEXT PRIMARY KEY,
                edit_id TEXT NOT NULL,
                note_id TEXT NOT NULL,
                base_revision TEXT NOT NULL,
                current_revision TEXT NOT NULL,
                base_title TEXT NOT NULL,
                base_body TEXT NOT NULL,
                local_title TEXT NOT NULL,
                local_body TEXT NOT NULL,
                current_title TEXT NOT NULL,
                current_body TEXT NOT NULL,
                merged_title TEXT NOT NULL,
                merged_body TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                resolution TEXT,
                created_at REAL NOT NULL,
                resolved_at REAL,
                FOREIGN KEY(edit_id) REFERENCES joplin_edit_sessions(edit_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_joplin_conflict_edit_status
                ON joplin_note_conflicts(edit_id, status, created_at DESC);

            CREATE TABLE IF NOT EXISTS file_trash_entries (
                trash_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                project TEXT NOT NULL,
                root_path TEXT NOT NULL,
                original_path TEXT NOT NULL,
                trash_path TEXT,
                kind TEXT NOT NULL,
                git_state TEXT NOT NULL,
                sha256 TEXT,
                size INTEGER,
                item_count INTEGER,
                status TEXT NOT NULL,
                actor TEXT NOT NULL,
                manifest_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                expires_at REAL,
                restored_at REAL,
                pruned_at REAL,
                FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
            );

            CREATE INDEX IF NOT EXISTS idx_file_trash_agent_status
                ON file_trash_entries(agent_id, status, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_file_trash_expiry
                ON file_trash_entries(status, expires_at);

            CREATE TABLE IF NOT EXISTS file_trash_prune_batches (
                batch_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                status TEXT NOT NULL,
                preview_token TEXT NOT NULL,
                snapshot_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                applied_at REAL,
                undone_at REAL,
                FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
            );

            CREATE INDEX IF NOT EXISTS idx_file_trash_prune_agent
                ON file_trash_prune_batches(agent_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS operator_campaigns (
                campaign_id TEXT PRIMARY KEY,
                predecessor_campaign_id TEXT,
                operator_agent_id TEXT NOT NULL,
                title TEXT NOT NULL,
                objective TEXT NOT NULL,
                criteria_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'running',
                summary TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                completed_at REAL,
                FOREIGN KEY(operator_agent_id) REFERENCES agents(agent_id)
            );

            CREATE TABLE IF NOT EXISTS operator_campaign_assignments (
                assignment_id TEXT PRIMARY KEY,
                campaign_id TEXT NOT NULL,
                target_agent_id TEXT NOT NULL,
                operator_fork_id TEXT,
                fork_track_id TEXT,
                delivery_topology TEXT NOT NULL DEFAULT 'operator_fork',
                title TEXT NOT NULL,
                prompt TEXT NOT NULL,
                criteria_json TEXT NOT NULL DEFAULT '[]',
                state TEXT NOT NULL DEFAULT 'pending',
                last_report_id TEXT,
                last_command_id TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                completed_at REAL,
                FOREIGN KEY(campaign_id) REFERENCES operator_campaigns(campaign_id)
                    ON DELETE CASCADE,
                FOREIGN KEY(target_agent_id) REFERENCES agents(agent_id)
            );

            CREATE TABLE IF NOT EXISTS operator_campaign_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL,
                assignment_id TEXT,
                operator_agent_id TEXT NOT NULL,
                target_agent_id TEXT,
                event_type TEXT NOT NULL,
                summary TEXT NOT NULL,
                detail_json TEXT NOT NULL DEFAULT '{}',
                report_id TEXT,
                command_id TEXT,
                created_at REAL NOT NULL,
                FOREIGN KEY(campaign_id) REFERENCES operator_campaigns(campaign_id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS operator_forks (
                operator_fork_id TEXT PRIMARY KEY,
                logical_operator_agent_id TEXT NOT NULL,
                fork_agent_id TEXT NOT NULL UNIQUE,
                source_caller_agent_id TEXT NOT NULL,
                source_codex_session_id TEXT NOT NULL,
                fork_track_id TEXT NOT NULL DEFAULT 'default',
                fork_purpose TEXT NOT NULL DEFAULT 'edit',
                access_mode TEXT NOT NULL DEFAULT 'edit',
                source_cwd TEXT,
                work_root TEXT,
                fork_codex_session_id TEXT,
                campaign_id TEXT,
                cwd TEXT NOT NULL,
                codex_home TEXT,
                codex_host_id TEXT,
                tmux_pane_id TEXT,
                status TEXT NOT NULL DEFAULT 'starting',
                summary TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                last_used_at REAL NOT NULL,
                completed_at REAL,
                FOREIGN KEY(logical_operator_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(fork_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(source_caller_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(campaign_id) REFERENCES operator_campaigns(campaign_id)
                    ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS operator_project_spawn_requests (
                spawn_request_id TEXT PRIMARY KEY,
                logical_operator_agent_id TEXT NOT NULL,
                operator_agent_id TEXT NOT NULL,
                review_fork_id TEXT NOT NULL,
                review_fork_agent_id TEXT NOT NULL,
                source_caller_agent_id TEXT NOT NULL,
                source_cwd TEXT NOT NULL,
                target_parent TEXT NOT NULL,
                target_slug TEXT NOT NULL,
                target_path TEXT NOT NULL,
                project_name TEXT NOT NULL,
                mode TEXT NOT NULL,
                instructions TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                launched_agent_id TEXT,
                tmux_pane_id TEXT,
                error TEXT,
                campaign_id TEXT,
                assignment_id TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                completed_at REAL,
                FOREIGN KEY(logical_operator_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(operator_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(review_fork_id) REFERENCES operator_forks(operator_fork_id)
                    ON DELETE CASCADE,
                FOREIGN KEY(review_fork_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(source_caller_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(launched_agent_id) REFERENCES agents(agent_id)
                    ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS operator_fork_edges (
                edge_id TEXT PRIMARY KEY,
                from_fork_id TEXT NOT NULL,
                to_fork_id TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                summary TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                FOREIGN KEY(from_fork_id) REFERENCES operator_forks(operator_fork_id)
                    ON DELETE CASCADE,
                FOREIGN KEY(to_fork_id) REFERENCES operator_forks(operator_fork_id)
                    ON DELETE CASCADE,
                UNIQUE(from_fork_id, to_fork_id, edge_type)
            );

            CREATE TABLE IF NOT EXISTS operator_knowledge_links (
                link_id TEXT PRIMARY KEY,
                logical_operator_agent_id TEXT NOT NULL,
                operator_agent_id TEXT NOT NULL,
                source_agent_id TEXT NOT NULL,
                target_agent_id TEXT NOT NULL,
                source_operator_fork_id TEXT,
                link_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                summary TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                closed_at REAL,
                FOREIGN KEY(logical_operator_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(operator_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(source_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(target_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(source_operator_fork_id) REFERENCES operator_forks(operator_fork_id)
                    ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS operator_knowledge_turns (
                turn_id TEXT PRIMARY KEY,
                link_id TEXT NOT NULL,
                sender_agent_id TEXT NOT NULL,
                recipient_agent_id TEXT NOT NULL,
                turn_type TEXT NOT NULL,
                message TEXT NOT NULL,
                delivery_status TEXT NOT NULL DEFAULT 'recorded',
                command_id TEXT,
                tmux_pane_id TEXT,
                error TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                FOREIGN KEY(link_id) REFERENCES operator_knowledge_links(link_id)
                    ON DELETE CASCADE,
                FOREIGN KEY(sender_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(recipient_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(command_id) REFERENCES commands(command_id)
                    ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS operator_handoffs (
                handoff_id TEXT PRIMARY KEY,
                logical_operator_agent_id TEXT NOT NULL,
                source_operator_agent_id TEXT NOT NULL,
                target_operator_agent_id TEXT NOT NULL,
                source_agent_id TEXT NOT NULL,
                source_operator_fork_id TEXT,
                target_caller_agent_id TEXT,
                target_operator_fork_id TEXT,
                knowledge_link_id TEXT,
                knowledge_turn_id TEXT,
                objective TEXT NOT NULL,
                message TEXT NOT NULL,
                allowed_mutation_scope TEXT,
                execution_scope_json TEXT NOT NULL DEFAULT '{}',
                required_artifacts_json TEXT NOT NULL DEFAULT '[]',
                artifact_bundle_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'proposed',
                needs_ack INTEGER NOT NULL DEFAULT 1,
                expires_at REAL,
                acknowledged_at REAL,
                started_at REAL,
                completed_at REAL,
                command_id TEXT,
                tmux_pane_id TEXT,
                delivery_status TEXT,
                delivery_evidence_json TEXT NOT NULL DEFAULT '{}',
                error TEXT,
                summary TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(logical_operator_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(source_operator_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(target_operator_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(source_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(source_operator_fork_id) REFERENCES operator_forks(operator_fork_id)
                    ON DELETE SET NULL,
                FOREIGN KEY(target_caller_agent_id) REFERENCES agents(agent_id)
                    ON DELETE SET NULL,
                FOREIGN KEY(target_operator_fork_id) REFERENCES operator_forks(operator_fork_id)
                    ON DELETE SET NULL,
                FOREIGN KEY(knowledge_link_id) REFERENCES operator_knowledge_links(link_id)
                    ON DELETE SET NULL,
                FOREIGN KEY(knowledge_turn_id) REFERENCES operator_knowledge_turns(turn_id)
                    ON DELETE SET NULL,
                FOREIGN KEY(command_id) REFERENCES commands(command_id)
                    ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS operator_handoff_completion_deliveries (
                completion_delivery_id TEXT PRIMARY KEY,
                handoff_id TEXT NOT NULL,
                handoff_version TEXT NOT NULL,
                source_operator_agent_id TEXT NOT NULL,
                source_operator_fork_id TEXT,
                recipient_agent_id TEXT NOT NULL,
                target_operator_agent_id TEXT NOT NULL,
                terminal_status TEXT NOT NULL,
                summary TEXT NOT NULL,
                artifact_kinds_json TEXT NOT NULL DEFAULT '[]',
                artifact_bundle_digest TEXT NOT NULL,
                delivery_mode TEXT,
                delivery_status TEXT NOT NULL DEFAULT 'pending',
                command_id TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_attempt_at REAL,
                last_error TEXT,
                acknowledged_at REAL,
                acknowledged_by TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(handoff_id, handoff_version),
                FOREIGN KEY(handoff_id) REFERENCES operator_handoffs(handoff_id)
                    ON DELETE CASCADE,
                FOREIGN KEY(source_operator_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(source_operator_fork_id) REFERENCES operator_forks(operator_fork_id)
                    ON DELETE SET NULL,
                FOREIGN KEY(recipient_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(target_operator_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(command_id) REFERENCES commands(command_id)
                    ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_handoff_completion_recipient_status
                ON operator_handoff_completion_deliveries(
                    recipient_agent_id, delivery_status, created_at ASC
                );
            CREATE INDEX IF NOT EXISTS idx_handoff_completion_source_status
                ON operator_handoff_completion_deliveries(
                    source_operator_agent_id, delivery_status, created_at ASC
                );

            CREATE TABLE IF NOT EXISTS operator_delegation_leases (
                lease_id TEXT PRIMARY KEY,
                source_operator_agent_id TEXT NOT NULL,
                target_operator_agent_ids_json TEXT NOT NULL DEFAULT '[]',
                campaign_ids_json TEXT NOT NULL DEFAULT '[]',
                workspace_set_digest TEXT NOT NULL,
                allowed_action_classes_json TEXT NOT NULL DEFAULT '[]',
                allowed_roots_json TEXT NOT NULL DEFAULT '[]',
                exact_paths_json TEXT NOT NULL DEFAULT '[]',
                denied_actions_json TEXT NOT NULL DEFAULT '[]',
                max_uses INTEGER NOT NULL,
                use_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                requested_by TEXT NOT NULL,
                approved_by TEXT,
                created_at REAL NOT NULL,
                approved_at REAL,
                expires_at REAL NOT NULL,
                revoked_at REAL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(source_operator_agent_id) REFERENCES agents(agent_id)
            );

            CREATE INDEX IF NOT EXISTS idx_operator_delegation_lease_source_status
                ON operator_delegation_leases(
                    source_operator_agent_id, status, expires_at DESC
                );

            CREATE TABLE IF NOT EXISTS operator_action_transactions (
                transaction_id TEXT PRIMARY KEY,
                handoff_id TEXT NOT NULL UNIQUE,
                action_class TEXT NOT NULL,
                target_agent_id TEXT NOT NULL,
                scope_sha256 TEXT NOT NULL,
                state TEXT NOT NULL,
                reason TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                completed_at REAL,
                FOREIGN KEY(handoff_id) REFERENCES operator_handoffs(handoff_id)
                    ON DELETE CASCADE,
                FOREIGN KEY(target_agent_id) REFERENCES agents(agent_id)
            );

            CREATE TABLE IF NOT EXISTS operator_action_receipts (
                receipt_id TEXT PRIMARY KEY,
                transaction_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                step TEXT NOT NULL,
                outcome TEXT NOT NULL,
                actor TEXT NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                UNIQUE(transaction_id, sequence),
                FOREIGN KEY(transaction_id)
                    REFERENCES operator_action_transactions(transaction_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_operator_action_transactions_state
                ON operator_action_transactions(state, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_action_receipts_transaction
                ON operator_action_receipts(transaction_id, sequence ASC);

            CREATE TABLE IF NOT EXISTS operator_broker_requests (
                request_id TEXT PRIMARY KEY,
                root_operator_agent_id TEXT NOT NULL,
                target_fork_agent_id TEXT NOT NULL,
                handoff_id TEXT NOT NULL,
                campaign_id TEXT,
                assignment_id TEXT,
                action_transaction_id TEXT,
                workspace_set_digest TEXT NOT NULL,
                scope_sha256 TEXT NOT NULL,
                expected_git_parent TEXT NOT NULL,
                expected_git_parents_json TEXT NOT NULL DEFAULT '[]',
                expected_git_tree TEXT,
                commit_subject TEXT NOT NULL,
                exact_paths_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'pending',
                executor_id TEXT,
                claim_expires_at REAL,
                result_json TEXT NOT NULL DEFAULT '{}',
                error_code TEXT,
                error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                completed_at REAL,
                canceled_at REAL,
                approval_report_id TEXT,
                validated_by_operator_agent_id TEXT,
                validated_at REAL,
                validation_sha256 TEXT,
                FOREIGN KEY(root_operator_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(target_fork_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(handoff_id) REFERENCES operator_handoffs(handoff_id)
                    ON DELETE CASCADE,
                FOREIGN KEY(action_transaction_id)
                    REFERENCES operator_action_transactions(transaction_id)
                    ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_operator_broker_requests_status
                ON operator_broker_requests(status, expires_at, created_at);
            CREATE INDEX IF NOT EXISTS idx_operator_broker_requests_root
                ON operator_broker_requests(
                    root_operator_agent_id, status, created_at DESC
                );
            CREATE INDEX IF NOT EXISTS idx_operator_broker_requests_target
                ON operator_broker_requests(
                    target_fork_agent_id, status, created_at DESC
                );

            CREATE TABLE IF NOT EXISTS workerbee_controlled_run_leases (
                lease_id TEXT PRIMARY KEY,
                root_operator_agent_id TEXT NOT NULL,
                target_fork_agent_id TEXT NOT NULL,
                campaign_id TEXT NOT NULL,
                workspace_set_digest TEXT NOT NULL,
                backend TEXT NOT NULL,
                backend_instance_id TEXT NOT NULL,
                backend_fingerprint TEXT NOT NULL,
                backend_generation INTEGER NOT NULL,
                workspace_binding_id TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                profile_sha256 TEXT NOT NULL,
                baseline_commit TEXT NOT NULL,
                candidate_commit TEXT NOT NULL,
                recipe_id TEXT NOT NULL,
                tool_manifest_sha256 TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                max_uses INTEGER NOT NULL,
                use_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                requested_by TEXT NOT NULL,
                approved_by TEXT,
                created_at REAL NOT NULL,
                approved_at REAL,
                expires_at REAL NOT NULL,
                revoked_at REAL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(root_operator_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(target_fork_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(campaign_id) REFERENCES operator_campaigns(campaign_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_workerbee_controlled_run_leases_root
                ON workerbee_controlled_run_leases(
                    root_operator_agent_id, status, expires_at
                );

            CREATE TABLE IF NOT EXISTS workerbee_controlled_run_requests (
                request_id TEXT PRIMARY KEY,
                root_operator_agent_id TEXT NOT NULL,
                target_fork_agent_id TEXT NOT NULL,
                campaign_id TEXT NOT NULL,
                workspace_set_digest TEXT NOT NULL,
                backend TEXT NOT NULL,
                backend_instance_id TEXT NOT NULL,
                backend_fingerprint TEXT NOT NULL,
                backend_generation INTEGER NOT NULL,
                workspace_binding_id TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                profile_version INTEGER NOT NULL,
                profile_sha256 TEXT NOT NULL,
                profile_json TEXT NOT NULL,
                baseline_commit TEXT NOT NULL,
                candidate_commit TEXT NOT NULL,
                recipe_id TEXT NOT NULL,
                tool_manifest_sha256 TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                requested_by TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                decided_by TEXT,
                approved_at REAL,
                rejected_at REAL,
                lease_id TEXT,
                executor_id TEXT,
                claim_expires_at REAL,
                workerbee_run_id TEXT,
                workerbee_workspace_id TEXT,
                result_json TEXT NOT NULL DEFAULT '{}',
                error_code TEXT,
                error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                started_at REAL,
                completed_at REAL,
                canceled_at REAL,
                canceled_by TEXT,
                FOREIGN KEY(root_operator_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(target_fork_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(campaign_id) REFERENCES operator_campaigns(campaign_id)
                    ON DELETE CASCADE,
                FOREIGN KEY(lease_id) REFERENCES workerbee_controlled_run_leases(lease_id)
                    ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_workerbee_controlled_runs_status
                ON workerbee_controlled_run_requests(status, expires_at, created_at);
            CREATE INDEX IF NOT EXISTS idx_workerbee_controlled_runs_root
                ON workerbee_controlled_run_requests(
                    root_operator_agent_id, status, created_at DESC
                );
            CREATE INDEX IF NOT EXISTS idx_workerbee_controlled_runs_fork
                ON workerbee_controlled_run_requests(
                    target_fork_agent_id, status, created_at DESC
                );

            CREATE TABLE IF NOT EXISTS workerbee_controlled_run_receipts (
                receipt_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE,
                outcome TEXT NOT NULL,
                backend_run_id TEXT,
                workspace_id TEXT,
                evidence_json TEXT NOT NULL DEFAULT '{}',
                cleanup_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                FOREIGN KEY(request_id)
                    REFERENCES workerbee_controlled_run_requests(request_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_workerbee_controlled_run_receipts_request
                ON workerbee_controlled_run_receipts(request_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS agent_prune_batches (
                batch_id TEXT PRIMARY KEY,
                preset TEXT NOT NULL,
                delete_thread INTEGER NOT NULL DEFAULT 0,
                criteria_json TEXT NOT NULL DEFAULT '{}',
                candidate_count INTEGER NOT NULL DEFAULT 0,
                hidden_count INTEGER NOT NULL DEFAULT 0,
                skipped_count INTEGER NOT NULL DEFAULT 0,
                results_json TEXT NOT NULL DEFAULT '[]',
                undo_results_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                undone_at REAL
            );

            CREATE TABLE IF NOT EXISTS operator_kb_entries (
                kb_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                project TEXT,
                repo_root TEXT,
                git_remote TEXT,
                branch TEXT,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                body TEXT NOT NULL,
                tags_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'proposed',
                redaction_status TEXT NOT NULL DEFAULT 'unreviewed',
                created_by_operator_agent_id TEXT NOT NULL,
                created_by_agent_id TEXT NOT NULL,
                source_knowledge_link_id TEXT,
                source_handoff_id TEXT,
                source_turn_ids_json TEXT NOT NULL DEFAULT '[]',
                stale_after REAL,
                expires_at REAL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                promoted_at REAL,
                retired_at REAL,
                imported_at REAL,
                FOREIGN KEY(created_by_operator_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(created_by_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(source_knowledge_link_id) REFERENCES operator_knowledge_links(link_id)
                    ON DELETE SET NULL,
                FOREIGN KEY(source_handoff_id) REFERENCES operator_handoffs(handoff_id)
                    ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS operator_kb_sources (
                source_row_id TEXT PRIMARY KEY,
                kb_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                FOREIGN KEY(kb_id) REFERENCES operator_kb_entries(kb_id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS operator_kb_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                kb_id TEXT,
                event_type TEXT NOT NULL,
                operator_agent_id TEXT,
                summary TEXT NOT NULL,
                detail_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                FOREIGN KEY(kb_id) REFERENCES operator_kb_entries(kb_id)
                    ON DELETE SET NULL,
                FOREIGN KEY(operator_agent_id) REFERENCES agents(agent_id)
                    ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS operator_kb_seed_runs (
                seed_run_id TEXT PRIMARY KEY,
                logical_operator_agent_id TEXT NOT NULL,
                source_operator_agent_id TEXT NOT NULL,
                seed_type TEXT NOT NULL,
                scope TEXT NOT NULL,
                project TEXT,
                repo_root TEXT,
                git_remote TEXT,
                branch TEXT,
                prompt TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'requested',
                command_id TEXT,
                tmux_pane_id TEXT,
                delivery_status TEXT,
                error TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                completed_at REAL,
                FOREIGN KEY(logical_operator_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(source_operator_agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY(command_id) REFERENCES commands(command_id)
                    ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS operator_kb_index_jobs (
                job_id TEXT PRIMARY KEY,
                kb_id TEXT,
                operation TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                error TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                completed_at REAL,
                FOREIGN KEY(kb_id) REFERENCES operator_kb_entries(kb_id)
                    ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS operator_kb_chunks (
                chunk_id TEXT PRIMARY KEY,
                kb_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                semantic_signature_json TEXT NOT NULL DEFAULT '{}',
                token_count INTEGER NOT NULL DEFAULT 0,
                content_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(kb_id) REFERENCES operator_kb_entries(kb_id)
                    ON DELETE CASCADE,
                UNIQUE(kb_id, chunk_index)
            );

            CREATE TABLE IF NOT EXISTS operator_kb_queries (
                query_id TEXT PRIMARY KEY,
                logical_operator_agent_id TEXT NOT NULL,
                operator_agent_id TEXT NOT NULL,
                target_operator_agent_id TEXT,
                query TEXT NOT NULL,
                scope TEXT,
                project TEXT,
                repo_root TEXT,
                tags_json TEXT NOT NULL DEFAULT '[]',
                include_expired INTEGER NOT NULL DEFAULT 0,
                include_proposed INTEGER NOT NULL DEFAULT 0,
                semantic INTEGER NOT NULL DEFAULT 0,
                retrieval_mode TEXT NOT NULL DEFAULT 'keyword',
                retrieval_provider TEXT NOT NULL DEFAULT 'sqlite',
                retrieval_model TEXT NOT NULL DEFAULT 'keyword-fts-like',
                index_version TEXT NOT NULL DEFAULT 'sqlite-hybrid-v1',
                source TEXT NOT NULL DEFAULT 'search',
                limit_count INTEGER NOT NULL DEFAULT 0,
                match_count INTEGER NOT NULL DEFAULT 0,
                semantic_match_count INTEGER NOT NULL DEFAULT 0,
                satisfied_by_kb INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                FOREIGN KEY(logical_operator_agent_id) REFERENCES agents(agent_id)
                    ON DELETE CASCADE,
                FOREIGN KEY(operator_agent_id) REFERENCES agents(agent_id)
                    ON DELETE CASCADE,
                FOREIGN KEY(target_operator_agent_id) REFERENCES agents(agent_id)
                    ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS operator_kb_query_matches (
                match_id TEXT PRIMARY KEY,
                query_id TEXT NOT NULL,
                kb_id TEXT,
                rank INTEGER NOT NULL,
                retrieval_mode TEXT NOT NULL DEFAULT 'keyword',
                retrieval_sources_json TEXT NOT NULL DEFAULT '[]',
                score REAL,
                keyword_rank INTEGER,
                semantic_score REAL,
                semantic_chunks_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                FOREIGN KEY(query_id) REFERENCES operator_kb_queries(query_id)
                    ON DELETE CASCADE,
                FOREIGN KEY(kb_id) REFERENCES operator_kb_entries(kb_id)
                    ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS operator_kb_feedback (
                feedback_id TEXT PRIMARY KEY,
                query_id TEXT,
                match_id TEXT,
                kb_id TEXT,
                logical_operator_agent_id TEXT NOT NULL,
                operator_agent_id TEXT NOT NULL,
                feedback TEXT NOT NULL,
                summary TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                FOREIGN KEY(query_id) REFERENCES operator_kb_queries(query_id)
                    ON DELETE CASCADE,
                FOREIGN KEY(match_id) REFERENCES operator_kb_query_matches(match_id)
                    ON DELETE SET NULL,
                FOREIGN KEY(kb_id) REFERENCES operator_kb_entries(kb_id)
                    ON DELETE SET NULL,
                FOREIGN KEY(logical_operator_agent_id) REFERENCES agents(agent_id)
                    ON DELETE CASCADE,
                FOREIGN KEY(operator_agent_id) REFERENCES agents(agent_id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS operator_kb_sync_records (
                logical_project_id TEXT NOT NULL,
                stable_id TEXT NOT NULL,
                local_kb_id TEXT,
                revision INTEGER NOT NULL,
                parent_content_hash TEXT,
                content_hash TEXT NOT NULL,
                tombstone INTEGER NOT NULL DEFAULT 0,
                trusted_reviewer INTEGER NOT NULL DEFAULT 0,
                reviewer_evidence_json TEXT NOT NULL DEFAULT '{}',
                source_bundle_sha256 TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(logical_project_id, stable_id),
                FOREIGN KEY(local_kb_id) REFERENCES operator_kb_entries(kb_id)
                    ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS durable_helper_bundles (
                helper_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                object_sha256 TEXT NOT NULL,
                object_bytes INTEGER NOT NULL,
                source_path TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                provenance_json TEXT NOT NULL,
                validation_receipt_json TEXT,
                scopes_json TEXT NOT NULL DEFAULT '[]',
                kb_refs_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL,
                created_by_operator_agent_id TEXT NOT NULL,
                reviewed_by_operator_agent_id TEXT,
                activated_by_operator_agent_id TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(name, object_sha256)
            );

            CREATE TABLE IF NOT EXISTS durable_helper_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                helper_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                operator_agent_id TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                FOREIGN KEY(helper_id) REFERENCES durable_helper_bundles(helper_id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS dynamic_resource_requests (
                request_id TEXT PRIMARY KEY,
                request_sha256 TEXT NOT NULL,
                resource_class TEXT NOT NULL,
                requester_agent_id TEXT NOT NULL,
                operator_agent_id TEXT NOT NULL,
                campaign_id TEXT,
                handoff_id TEXT,
                workspace_set_digest TEXT NOT NULL,
                workerbee_binding_id TEXT,
                workerbee_backend_generation TEXT,
                provider_capability_sha256 TEXT,
                spec_json TEXT NOT NULL,
                provider TEXT NOT NULL,
                ttl_seconds INTEGER NOT NULL,
                quota INTEGER NOT NULL,
                max_uses INTEGER NOT NULL,
                use_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                approval_mode TEXT NOT NULL,
                approval_json TEXT,
                delegation_lease_id TEXT,
                provider_receipt_json TEXT,
                cleanup_receipt_json TEXT,
                restart_required INTEGER NOT NULL DEFAULT 0,
                expires_at REAL NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dynamic_resource_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                operator_agent_id TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                FOREIGN KEY(request_id) REFERENCES dynamic_resource_requests(request_id)
                    ON DELETE CASCADE
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS operator_kb_fts USING fts5(
                kb_id UNINDEXED,
                title,
                summary,
                body,
                tags,
                project,
                repo_root,
                tokenize = 'unicode61'
            );

            CREATE INDEX IF NOT EXISTS idx_operator_campaigns_operator_updated
                ON operator_campaigns(operator_agent_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_campaigns_status_updated
                ON operator_campaigns(status, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_assignments_campaign_state
                ON operator_campaign_assignments(campaign_id, state);
            CREATE INDEX IF NOT EXISTS idx_operator_assignments_target_updated
                ON operator_campaign_assignments(target_agent_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_events_campaign_created
                ON operator_campaign_events(campaign_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_events_assignment_created
                ON operator_campaign_events(assignment_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_forks_logical_updated
                ON operator_forks(logical_operator_agent_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_forks_source_updated
                ON operator_forks(source_caller_agent_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_forks_fork_agent
                ON operator_forks(fork_agent_id);
            CREATE INDEX IF NOT EXISTS idx_operator_project_spawns_logical_status
                ON operator_project_spawn_requests(
                    logical_operator_agent_id,
                    status,
                    created_at ASC
                );
            CREATE INDEX IF NOT EXISTS idx_operator_project_spawns_review_status
                ON operator_project_spawn_requests(review_fork_id, status, created_at ASC);
            CREATE INDEX IF NOT EXISTS idx_operator_project_spawns_launched_agent
                ON operator_project_spawn_requests(launched_agent_id);
            CREATE INDEX IF NOT EXISTS idx_operator_fork_edges_from
                ON operator_fork_edges(from_fork_id);
            CREATE INDEX IF NOT EXISTS idx_operator_fork_edges_to
                ON operator_fork_edges(to_fork_id);
            CREATE INDEX IF NOT EXISTS idx_operator_knowledge_links_logical_status
                ON operator_knowledge_links(
                    logical_operator_agent_id,
                    status,
                    updated_at DESC
                );
            CREATE INDEX IF NOT EXISTS idx_operator_knowledge_links_source
                ON operator_knowledge_links(source_agent_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_knowledge_links_target
                ON operator_knowledge_links(target_agent_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_knowledge_turns_link_created
                ON operator_knowledge_turns(link_id, created_at ASC);
            CREATE INDEX IF NOT EXISTS idx_operator_knowledge_turns_recipient_status
                ON operator_knowledge_turns(
                    recipient_agent_id,
                    delivery_status,
                    created_at DESC
                );
            CREATE INDEX IF NOT EXISTS idx_operator_handoffs_logical_status
                ON operator_handoffs(
                    logical_operator_agent_id,
                    status,
                    updated_at DESC
                );
            CREATE INDEX IF NOT EXISTS idx_operator_handoffs_target_status
                ON operator_handoffs(
                    target_operator_agent_id,
                    status,
                    updated_at DESC
                );
            CREATE INDEX IF NOT EXISTS idx_operator_handoffs_source_updated
                ON operator_handoffs(source_agent_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_handoffs_target_caller
                ON operator_handoffs(target_caller_agent_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_handoffs_expires
                ON operator_handoffs(expires_at, status);
            CREATE INDEX IF NOT EXISTS idx_agent_prune_batches_created
                ON agent_prune_batches(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_kb_scope_status
                ON operator_kb_entries(scope, status, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_kb_project_status
                ON operator_kb_entries(project, status, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_kb_source_link
                ON operator_kb_entries(source_knowledge_link_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_kb_source_handoff
                ON operator_kb_entries(source_handoff_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_kb_sources_kb
                ON operator_kb_sources(kb_id, source_type);
            CREATE INDEX IF NOT EXISTS idx_operator_kb_events_kb_created
                ON operator_kb_events(kb_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_kb_seed_runs_logical_status
                ON operator_kb_seed_runs(
                    logical_operator_agent_id,
                    status,
                    updated_at DESC
                );
            CREATE INDEX IF NOT EXISTS idx_operator_kb_seed_runs_source
                ON operator_kb_seed_runs(source_operator_agent_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_kb_created_status
                ON operator_kb_entries(
                    created_by_operator_agent_id,
                    status,
                    updated_at DESC
                );
            CREATE INDEX IF NOT EXISTS idx_operator_kb_index_jobs_status_created
                ON operator_kb_index_jobs(status, created_at ASC, job_id ASC);
            CREATE INDEX IF NOT EXISTS idx_operator_kb_index_jobs_kb_updated
                ON operator_kb_index_jobs(kb_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_kb_chunks_kb_index
                ON operator_kb_chunks(kb_id, chunk_index);
            CREATE INDEX IF NOT EXISTS idx_operator_kb_chunks_hash
                ON operator_kb_chunks(content_hash);
            CREATE INDEX IF NOT EXISTS idx_operator_kb_queries_operator_created
                ON operator_kb_queries(logical_operator_agent_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_kb_queries_project_created
                ON operator_kb_queries(project, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_kb_query_matches_query_rank
                ON operator_kb_query_matches(query_id, rank ASC);
            CREATE INDEX IF NOT EXISTS idx_operator_kb_query_matches_kb
                ON operator_kb_query_matches(kb_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_kb_feedback_query_created
                ON operator_kb_feedback(query_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_kb_feedback_kb_created
                ON operator_kb_feedback(kb_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operator_kb_sync_local
                ON operator_kb_sync_records(local_kb_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_durable_helpers_status_name
                ON durable_helper_bundles(status, name, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_durable_helper_events_helper
                ON durable_helper_events(helper_id, event_id DESC);
            CREATE INDEX IF NOT EXISTS idx_dynamic_resource_status_expiry
                ON dynamic_resource_requests(status, expires_at, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_dynamic_resource_campaign
                ON dynamic_resource_requests(campaign_id, status, created_at DESC);
            """
        )
        previous_schema_version = store._schema_version(conn)
        # Schema 39 introduced controlled runs in one checkpoint and the
        # runtime fingerprint was added before activation.  Keep this
        # repair unconditional so a database briefly initialized by the
        # earlier checkpoint fails closed with an empty fingerprint rather
        # than crashing on a missing column.
        store._ensure_column(
            conn,
            "workerbee_controlled_run_requests",
            "backend_fingerprint",
            "TEXT NOT NULL DEFAULT ''",
        )
        store._ensure_column(
            conn,
            "workerbee_controlled_run_leases",
            "backend_fingerprint",
            "TEXT NOT NULL DEFAULT ''",
        )
        conn.execute(
            """
            UPDATE joplin_sync_jobs
            SET status = 'queued', started_at = NULL
            WHERE status = 'running'
            """
        )
        store._ensure_column(conn, "agents", "last_poll_at", "REAL")
        store._ensure_column(conn, "tokens", "token_id", "TEXT")
        store._ensure_column(
            conn,
            "tokens",
            "role",
            "TEXT NOT NULL DEFAULT 'controller'",
        )
        store._ensure_column(conn, "tokens", "audience", "TEXT")
        store._ensure_column(conn, "tokens", "client_id", "TEXT")
        store._ensure_column(
            conn,
            "tokens",
            "scopes_json",
            "TEXT NOT NULL DEFAULT '[]'",
        )
        store._ensure_column(
            conn,
            "operator_broker_requests",
            "expected_git_tree",
            "TEXT",
        )
        store._ensure_column(
            conn,
            "tokens",
            "allowed_agent_ids_json",
            "TEXT NOT NULL DEFAULT '[]'",
        )
        store._ensure_column(conn, "tokens", "last_used_at", "REAL")
        conn.execute(
            """
            UPDATE tokens
            SET token_id = 'legacy-' || substr(token_hash, 1, 24)
            WHERE token_id IS NULL OR token_id = ''
            """
        )
        conn.execute(
            """
            UPDATE tokens
            SET role = CASE
                WHEN lower(kind) = 'observer' THEN 'observer'
                ELSE 'controller'
            END
            WHERE role IS NULL OR role = '' OR role = 'controller'
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tokens_token_id ON tokens(token_id)"
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_remote_audit_created
                ON remote_audit_events(created_at DESC)
            """
        )
        store._ensure_column(
            conn,
            "joplin_sync_jobs",
            "profile_key",
            "TEXT NOT NULL DEFAULT 'default'",
        )
        store._ensure_column(
            conn,
            "joplin_sync_jobs",
            "note_ids_json",
            "TEXT NOT NULL DEFAULT '[]'",
        )
        store._ensure_column(
            conn,
            "joplin_sync_jobs",
            "reasons_json",
            "TEXT NOT NULL DEFAULT '[]'",
        )
        store._ensure_column(
            conn,
            "joplin_sync_jobs",
            "generation",
            "INTEGER NOT NULL DEFAULT 1",
        )
        store._ensure_column(
            conn,
            "joplin_sync_jobs",
            "progress_json",
            "TEXT NOT NULL DEFAULT '{}'",
        )
        store._ensure_column(conn, "joplin_sync_jobs", "error_code", "TEXT")
        conn.execute(
            """
            UPDATE joplin_sync_jobs
            SET note_ids_json = CASE
                    WHEN note_id IS NULL OR note_id = '' THEN '[]'
                    ELSE json_array(note_id)
                END,
                reasons_json = json_array(reason)
            WHERE note_ids_json = '[]' AND reasons_json = '[]'
            """
        )
        store._ensure_column(
            conn,
            "agents",
            "agent_type",
            "TEXT NOT NULL DEFAULT 'caller'",
        )
        store._ensure_column(conn, "agents", "latest_report_seen_at", "REAL")
        store._ensure_column(conn, "agents", "starred_at", "REAL")
        store._ensure_column(conn, "agents", "dismissed_at", "REAL")
        store._ensure_column(
            conn,
            "agents",
            "pbx_active",
            "INTEGER NOT NULL DEFAULT 1",
        )
        store._ensure_column(
            conn,
            "reports",
            "metadata_json",
            "TEXT NOT NULL DEFAULT '{}'",
        )
        store._ensure_column(
            conn,
            "operator_campaign_assignments",
            "operator_fork_id",
            "TEXT",
        )
        store._ensure_column(
            conn,
            "operator_campaign_assignments",
            "fork_track_id",
            "TEXT",
        )
        store._ensure_column(
            conn,
            "operator_campaign_assignments",
            "delivery_topology",
            "TEXT NOT NULL DEFAULT 'operator_fork'",
        )
        store._ensure_column(
            conn,
            "operator_forks",
            "fork_track_id",
            "TEXT NOT NULL DEFAULT 'default'",
        )
        store._ensure_column(
            conn,
            "operator_forks",
            "fork_purpose",
            "TEXT NOT NULL DEFAULT 'edit'",
        )
        store._ensure_column(
            conn,
            "operator_forks",
            "access_mode",
            "TEXT NOT NULL DEFAULT 'edit'",
        )
        store._ensure_column(conn, "operator_forks", "source_cwd", "TEXT")
        store._ensure_column(conn, "operator_forks", "work_root", "TEXT")
        store._ensure_column(
            conn, "operator_campaigns", "predecessor_campaign_id", "TEXT"
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_operator_campaign_successor
            ON operator_campaigns(predecessor_campaign_id)
            WHERE predecessor_campaign_id IS NOT NULL
            """
        )
        store._ensure_column(
            conn,
            "operator_handoffs",
            "execution_scope_json",
            "TEXT NOT NULL DEFAULT '{}'",
        )
        store._ensure_column(
            conn,
            "operator_broker_requests",
            "expected_git_parents_json",
            "TEXT NOT NULL DEFAULT '[]'",
        )
        store._ensure_column(conn, "operator_broker_requests", "campaign_id", "TEXT")
        store._ensure_column(conn, "operator_broker_requests", "assignment_id", "TEXT")
        store._ensure_column(
            conn, "operator_broker_requests", "approval_report_id", "TEXT"
        )
        store._ensure_column(
            conn,
            "operator_broker_requests",
            "validated_by_operator_agent_id",
            "TEXT",
        )
        store._ensure_column(conn, "operator_broker_requests", "validated_at", "REAL")
        store._ensure_column(
            conn, "operator_broker_requests", "validation_sha256", "TEXT"
        )
        store._ensure_column(
            conn,
            "dynamic_resource_requests",
            "provider_capability_sha256",
            "TEXT",
        )
        conn.execute(
            """
            UPDATE operator_forks
            SET fork_track_id = COALESCE(NULLIF(fork_track_id, ''), 'default'),
                fork_purpose = COALESCE(NULLIF(fork_purpose, ''), 'edit'),
                access_mode = COALESCE(NULLIF(access_mode, ''), 'edit'),
                source_cwd = COALESCE(NULLIF(source_cwd, ''), cwd),
                work_root = COALESCE(NULLIF(work_root, ''), cwd)
            """
        )
        conn.execute("DROP INDEX IF EXISTS idx_operator_forks_source_session")
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_operator_forks_source_session
                ON operator_forks(
                    logical_operator_agent_id,
                    source_caller_agent_id,
                    source_codex_session_id,
                    fork_track_id
                )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_operator_forks_track_updated
                ON operator_forks(
                    logical_operator_agent_id,
                    fork_track_id,
                    updated_at DESC
                )
            """
        )
        store._backfill_agent_type(conn)
        if previous_schema_version < 7:
            store._backfill_latest_report_seen(conn)
        if previous_schema_version < 20:
            store._rebuild_operator_kb_fts(conn)
        if previous_schema_version < 21:
            store._rebuild_operator_kb_semantic_chunks(conn)
        conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
            (str(schema_version),),
        )


__all__ = ("SchemaStore", "initialize_schema")
