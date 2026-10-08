export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface LoginChallenge {
  two_factor_required: true;
  challenge_token: string;
  expires_in: number;
}

export type LoginResponse = TokenPair | LoginChallenge;

export type UserRole = "owner" | "admin" | "maintainer" | "viewer";

export interface User {
  id: string;
  username: string;
  display_name: string | null;
  role: UserRole;
  is_active: boolean;
  disabled_at: string | null;
  last_login_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectMembership {
  id: string;
  project_id: string;
  user_id: string;
  role: "maintainer" | "viewer";
  created_at: string;
  updated_at: string;
  user: User;
}

export interface Project {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  project_type: string;
  runtime_type: string;
  status: "created" | "deploying" | "running" | "stopped" | "failed";
  repository_url: string | null;
  branch: string;
  install_command: string | null;
  build_command: string | null;
  start_command: string | null;
  output_directory: string | null;
  healthcheck_url: string | null;
  notes: string | null;
  runtime_config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ProjectGitCredential {
  configured: boolean;
  credential_type: "token" | "deploy_key" | string | null;
  username: string | null;
}

export interface ProjectTemplate {
  id: string;
  name: string;
  description: string;
  project_type: string;
  runtime_type: string;
  install_command: string | null;
  build_command: string | null;
  start_command: string | null;
  output_directory: string | null;
  healthcheck_url: string | null;
  runtime_config: Record<string, unknown>;
  suggested_env: string[];
}

export interface HostMetrics {
  cpu_percent: number;
  memory_percent: number;
  memory_used: number;
  memory_total: number;
  disk_percent: number;
  disk_used: number;
  disk_total: number;
  network_bytes_sent: number;
  network_bytes_received: number;
  uptime_seconds: number;
}

export interface MinecraftStatus {
  running?: boolean;
  available: boolean;
  online_players: number | null;
  max_players: number | null;
  motd?: unknown;
  version?: string | null;
}

export interface ProjectMetricValues {
  minecraft?: MinecraftStatus;
  runtime_type: string;
  status: string;
  disk_bytes: number;
  health_status: string;
  health_status_code: number | null;
  resource_status?: string;
  cpu_percent?: number;
  memory_percent?: number;
  memory_used?: number;
  memory_total?: number;
  network_bytes_sent?: number;
  network_bytes_received?: number;
  process_count?: number;
  resource_policy?: ProjectResourcePolicyMetric;
  resource_violations?: ProjectResourceViolation[];
}

export interface ProjectResourcePolicy {
  id: string | null;
  project_id: string;
  enabled: boolean;
  cpu_cores: number | null;
  memory_mb: number | null;
  disk_mb: number | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ProjectResourcePolicyMetric {
  enabled: boolean;
  cpu_cores: number | null;
  memory_mb: number | null;
  disk_mb: number | null;
  enforcement: "disabled" | "enforced" | "monitor_only" | string;
}

export interface ProjectResourceViolation {
  resource: "cpu" | "memory" | "disk" | string;
  current: number;
  limit: number;
  unit: "percent" | "bytes" | string;
}

export interface Job {
  id: string;
  kind: string;
  status: string;
  progress: number;
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface MinecraftVersionOption {
  minecraft_version: string;
  forge_versions: string[];
  java_version: number;
}

export interface MinecraftVersionCatalog {
  versions: MinecraftVersionOption[];
}

export interface Deployment {
  id: string;
  project_id: string;
  status: string;
  source_revision: string | null;
  release_path: string | null;
  started_at: string | null;
  finished_at: string | null;
  log: string;
  created_at: string;
}

export interface EnvironmentVariable {
  id: string;
  key: string;
  value: string;
  is_secret: boolean;
  updated_at: string;
}

export interface EnvironmentVariableVersion {
  id: string;
  key: string;
  is_secret: boolean;
  value_sha256: string | null;
  action: string;
  actor_id: string | null;
  reason: string | null;
  created_at: string;
}

export interface EnvironmentReveal {
  key: string;
  value: string;
  is_secret: boolean;
}

export interface EnvironmentImportResult {
  created: number;
  updated: number;
  deleted: number;
  keys: string[];
}

export interface Domain {
  id: string;
  project_id: string;
  hostname: string;
  upstream_port: number;
  is_primary: boolean;
  ssl_status: string;
  certificate_expires_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Backup {
  id: string;
  project_id: string | null;
  database_id: string | null;
  backup_type: string;
  status: string;
  size_bytes: number;
  checksum: string | null;
  manifest: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface BackupPolicy {
  id: string;
  project_id: string;
  enabled: boolean;
  schedule: string;
  retention_count: number;
  include_items: string[];
  include_databases: boolean;
  encryption_enabled: boolean;
  last_run_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface DatabaseServer {
  id: string;
  name: string;
  engine: string;
  host: string;
  port: number;
  admin_username: string;
  tls_enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface DatabaseInstance {
  id: string;
  server_id: string;
  project_id: string | null;
  name: string;
  username: string;
  engine: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface DatabaseTable {
  schema: string | null;
  name: string;
  table_type: string;
}

export interface MetricSample {
  id: string;
  project_id: string | null;
  captured_at: string;
  values: HostMetrics | ProjectMetricValues;
}

export type ProjectLatestMetrics = Record<string, MetricSample | null>;

export interface AuditLog {
  id: string;
  actor_id: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  ip_address: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface Notification {
  id: string;
  notification_type: string;
  severity: string;
  title: string;
  message: string;
  resource_type: string | null;
  resource_id: string | null;
  read_at: string | null;
  created_at: string;
}

export interface MinecraftBuildOption {
  build_id: string;
  channel: "stable" | "release" | "unknown";
  java_version: number;
}

export interface MinecraftPluginEntry {
  name: string;
  path: string;
  size_bytes: number;
  enabled: boolean;
}
