/**
 * openjev guard — every bash command (and secret-path file access) is validated
 * before execution by two layers:
 *
 * 1. Deterministic rules (instant, no model): cloud lifecycle is human-only
 *    (terraform plan/apply/destroy, pulumi), Kubernetes mutations (kubectl
 *    write verbs, helm install/upgrade/uninstall, argocd app sync), AWS
 *    mutations (only get/list/describe-style reads pass), reading secrets from
 *    disk (id_rsa, .env, *.pem, ~/.aws/credentials, ~/.kube/config, ...) or
 *    from secret managers (vault, pass, 1Password, sops) or the cloud metadata
 *    endpoint, and extremely destructive filesystem operations (rm -rf on /, ~,
 *    ~/projects, ~/photos, ~/videos, ~/documents, mkfs, diskutil erase, dd to
 *    raw devices, shred, docker volume rm/prune).
 *
 * 2. openjev-tool NLI judgment (level-6 Jev Guard style): commands that pass
 *    the rules but are not trivially safe are scored with
 *    `openjev-tool jev noul` against the safety contract below. The command is
 *    blocked when P(contradiction) >= OPENJEV_GUARD_THRESHOLD.
 *
 * Environment knobs:
 *   OPENJEV_GUARD=off                disable the guard entirely
 *   OPENJEV_GUARD_THRESHOLD=<0..1>   contradiction threshold (default 0.25,
 *                                    calibrated on openjev-qwen3.5-4b: benign dev
 *                                    commands score <= 0.16, risky ones >= 0.27)
 *   OPENJEV_GUARD_SERVER=<url>       openjev server (default http://127.0.0.1:8080,
 *                                    honors OPENJEV_SERVER; preferred path: model
 *                                    stays resident, ~1s per check on MPS)
 *   OPENJEV_GUARD_MODEL=<id>         model pin on the server path
 *                                    (default openjev-qwen3.5-4b —
 *                                    nli-deberta-v3-small scores ~0.98 neutral on
 *                                    the policy premise and cannot gate anything)
 *   OPENJEV_GUARD_BIN=<path>         openjev-tool binary for the CLI fallback
 *   OPENJEV_GUARD_TIMEOUT_MS=<ms>    CLI fallback timeout (default 90000)
 *   OPENJEV_GUARD_FAIL=open|closed   behavior when openjev is unavailable
 *                                    (default open: warn and allow)
 *   OPENJEV_GUARD_LOG=<path|off>     JSONL inference log
 *                                    (default ~/.config/openjev/guard-log.jsonl)
 *
 * Inference log: every judgment appends one JSON line with the command, the
 * decision (block/allow), the deciding layer (rule, benign, nli, cache,
 * fail-open), the rule id/reason, and for NLI verdicts the full label
 * probabilities, the source (server/cli), latency, and the threshold. Inspect
 * it live with:
 *   tail -f ~/.config/openjev/guard-log.jsonl | jq .
 *
 * Fast paths: commands made only of benign read-only segments (ls, cat, git
 * log, kubectl get, aws get/list/describe, ...) skip the NLI call. Verdicts are
 * cached per command string. When no server is reachable the CLI fallback
 * loads the model from the HF cache on /Volumes/T7 (HF_HUB_OFFLINE=1 is set so
 * a missing disk fails fast instead of downloading ~8 GB); start
 * `openjev-tool serve` (or the menubar) to keep guards fast.
 */
import type { Plugin } from "@opencode-ai/plugin"
import { execFile } from "node:child_process"
import { existsSync } from "node:fs"
import { appendFile, mkdir } from "node:fs/promises"
import { homedir } from "node:os"
import { dirname } from "node:path"
import { promisify } from "node:util"

const execFileAsync = promisify(execFile)

const env = process.env as Record<string, string | undefined>
const DISABLED = env.OPENJEV_GUARD === "off"
const THRESHOLD = Number.parseFloat(env.OPENJEV_GUARD_THRESHOLD ?? "0.25") || 0.25
const SERVER =
  env.OPENJEV_GUARD_SERVER ?? env.OPENJEV_SERVER ?? "http://127.0.0.1:8080"
const MODEL = env.OPENJEV_GUARD_MODEL ?? "openjev-qwen3.5-4b"
const CLI_TIMEOUT = Number.parseInt(env.OPENJEV_GUARD_TIMEOUT_MS ?? "90000", 10) || 90000
const FAIL_CLOSED = (env.OPENJEV_GUARD_FAIL ?? "open") === "closed"
const LOG_PATH = env.OPENJEV_GUARD_LOG ?? `${homedir()}/.config/openjev/guard-log.jsonl`
const LOG_ENABLED = LOG_PATH !== "off"
const HTTP_TIMEOUT = 8000
const CACHE_MAX = 512

const PREMISE = `Safety contract for shell commands an AI coding agent wants to execute:
1. Allowed: read-only inspection (ls, cat, grep, git log, kubectl get, aws get/list/describe), and local reversible development work (build, test, lint, format, git add/commit/branch, editing files inside the project).
2. Forbidden, cloud lifecycle is human-only: terraform plan/apply/destroy/import, pulumi up/destroy, cloudformation deploy, and any cloud resource provisioning.
3. Forbidden, Kubernetes mutations: kubectl create/apply/delete/scale/patch/rollout/exec/edit/run, helm install/upgrade/uninstall, argocd app sync.
4. Forbidden, AWS mutations: any aws CLI call that creates, modifies or deletes resources (create/delete/put/update/copy or sync to s3).
5. Forbidden, secrets: reading .env, id_rsa, *.pem, *.key, credentials files, ~/.ssh, ~/.aws/credentials, ~/.kube/config, or fetching secrets from vault, pass, 1Password, sops, or the cloud metadata endpoint.
6. Forbidden, destructive filesystem operations: rm -rf on /, ~, $HOME, ~/projects, ~/photos, ~/videos, ~/documents or other home directories, mkfs, diskutil erase, dd to raw devices, shred, deleting docker volumes.
7. Forbidden: anything irreversible, global in effect, or that destroys data the human cares about.
The human performs all cloud and infrastructure lifecycle operations personally.`

const SECRET_PATH_RE =
  /(^|[~/\s"'])(\.env[^/]*|id_(rsa|dsa|ed25519|ecdsa)[^/]*|[^/]*\.pem|[^/]*\.key|[^/]*\.p12|[^/]*\.pfx|\.?credentials[^/]*|\.netrc|\.npmrc|\.git-credentials|secrets?\.[^/]*|[^/]*secrets?\.(json|ya?ml|txt|ini)|service[-_]?account[^/]*\.json|\.ssh\/[^/]*|\.aws\/credentials|\.kube\/config|\.docker\/config\.json|\.git-hub-token[^/]*|[^/]*\.keystore)$/i

const READERS = new Set([
  "cat", "head", "tail", "less", "more", "strings", "xxd", "od", "base64",
  "bat", "openssl", "gpg", "shred", "cp", "mv", "dd", "install", "tee",
])

const TF_LIFECYCLE = new Set([
  "apply", "destroy", "plan", "import", "taint", "untaint", "state", "workspace",
])
const PULUMI_LIFECYCLE = new Set([
  "up", "destroy", "refresh", "import", "state", "config",
])
const K8S_MUTATIONS = new Set([
  "create", "apply", "delete", "edit", "patch", "replace", "scale", "rollout",
  "autoscale", "run", "expose", "exec", "cp", "attach", "drain", "cordon",
  "uncordon", "taint", "label", "annotate", "set", "approve", "certificates",
])
const K8S_READONLY = new Set([
  "get", "describe", "logs", "top", "explain", "diff", "wait", "version",
  "api-resources", "api-versions", "auth", "cluster-info", "config", "options",
])
const HELM_MUTATIONS = new Set([
  "install", "upgrade", "uninstall", "delete", "rollback", "push",
])
const ARGOCD_MUTATIONS = new Set(["sync", "create", "delete", "patch", "rollback"])
const AWS_READONLY_PREFIX = /^(get|list|describe|batch[-_]get|search)/i
const AWS_BENIGN_OPS = new Set([
  "login", "logout", "tail", "update-kubeconfig", "get-caller-identity",
])
const PRECIOUS_HOME = [
  "projects", "photos", "videos", "documents", "pictures", "music", "movies",
  "desktop", "downloads", "library", "work", "dev", "src", "code", "repos",
]
const BENIGN_RM_RE =
  /^(?:-[a-zA-Z]+|node_modules|\.?venv|dist|build|target|__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache|\.coverage|[^/]*\.egg-info|coverage(?:\/.*)?)$/

const BENIGN_FIRST = new Set([
  "ls", "pwd", "cat", "head", "tail", "wc", "file", "stat", "du", "df", "tree",
  "echo", "printf", "which", "type", "command", "date", "uname", "whoami",
  "id", "hostname", "env", "printenv", "grep", "egrep", "fgrep", "rg", "jq",
  "yq", "column", "sort", "uniq", "cut", "tr", "diff", "cmp", "basename",
  "dirname", "realpath", "readlink", "seq", "true", "false", "test", "[",
  "md5", "shasum", "sha256sum", "mise", "bun", "node", "deno",
])
const BENIGN_GIT = new Set([
  "status", "log", "diff", "show", "blame", "rev-parse", "describe",
  "ls-files", "shortlog", "reflog", "remote", "branch", "tag", "stash",
  "add", "commit", "switch", "checkout", "restore", "merge", "rebase",
  "worktree", "config",
])
const BENIGN_DOCKER = new Set([
  "ps", "images", "logs", "inspect", "version", "stats", "top", "system",
])

type Probs = { contradiction: number; entailment?: number; neutral?: number }
type Decision = {
  block: boolean
  rule: string
  reason: string
  layer: "rule" | "benign" | "nli" | "cache" | "fail-open"
  probabilities?: Probs
  source?: "server" | "cli"
  latencyMs?: number
}

const BLOCK = (rule: string, reason: string): Decision => ({
  block: true, rule, reason, layer: "rule",
})
const ALLOW: Decision = { block: false, rule: "", reason: "", layer: "benign" }

function splitSegments(cmd: string, depth = 0): string[] {
  if (depth > 3) return [cmd]
  const parts = cmd.split(/\|\||&&|;|\||\n|\r/).map((s) => s.trim()).filter(Boolean)
  const out: string[] = []
  for (const part of parts) {
    out.push(part)
    const inner = [
      ...part.matchAll(/\$\(([^()]*(?:\([^()]*\)[^()]*)*)\)/g),
      ...part.matchAll(/`([^`]*)`/g),
    ]
    for (const m of inner) out.push(...splitSegments(m[1], depth + 1))
  }
  return out
}

function firstTokens(segment: string): string[] {
  const tokens = segment.trim().split(/\s+/)
  const out: string[] = []
  for (const token of tokens) {
    if (out.length === 0) {
      if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(token)) continue
      if (["sudo", "nohup", "env", "command", "exec", "time", "nice"].includes(token)) continue
    }
    out.push(token.replace(/^.*\//, ""))
    if (out.length >= 3) break
  }
  return out
}

function argsOf(segment: string): string[] {
  return segment.trim().split(/\s+/).slice(1)
}

function isSecretPathRef(token: string): boolean {
  const expanded = token.replace(/^~/, homedir())
  return SECRET_PATH_RE.test(token) || SECRET_PATH_RE.test(expanded)
}

function awsVerdict(segment: string): Decision | null {
  const tokens = segment.trim().split(/\s+/)
  if (tokens[0]?.replace(/^.*\//, "") !== "aws") return null
  const rest = tokens.slice(1).filter((t) => !t.startsWith("-"))
  const service = rest[0] ?? ""
  const op = rest[1] ?? ""
  if (!service || service === "help" || op === "help") return ALLOW
  if (service === "s3") {
    if (["ls", "la", "website"].includes(op) === false && ["cp", "mv", "sync"].includes(op)) {
      const args = rest.slice(2)
      const dst = args[args.length - 1] ?? ""
      const src = args[0] ?? ""
      const isDownload = src.startsWith("s3://") && !dst.startsWith("s3://")
      if (!isDownload) return BLOCK("aws-mutation", `aws s3 ${op} writes to S3`)
      return ALLOW
    }
    if (["rm", "mb", "rb", "del", "sync"].includes(op) && op !== "sync") {
      return BLOCK("aws-mutation", `aws s3 ${op} mutates S3`)
    }
    if (op === "sync") {
      const args = rest.slice(2)
      const dst = args[args.length - 1] ?? ""
      const src = args[0] ?? ""
      const isDownload = src.startsWith("s3://") && !dst.startsWith("s3://")
      if (!isDownload) return BLOCK("aws-mutation", "aws s3 sync uploads to S3")
      return ALLOW
    }
    return ALLOW
  }
  if (AWS_READONLY_PREFIX.test(op) || AWS_BENIGN_OPS.has(op)) return ALLOW
  if (op) return BLOCK("aws-mutation", `aws ${service} ${op} is not a read-only operation`)
  return null
}

const RULES: Array<(segment: string, raw: string) => Decision | null> = [
  (seg) => {
    const [cmd, verb] = firstTokens(seg)
    if (["terraform", "tf", "tofu"].includes(cmd) && TF_LIFECYCLE.has(verb ?? "")) {
      return BLOCK("cloud-lifecycle", `terraform ${verb} — cloud lifecycle is human-only`)
    }
    return null
  },
  (seg) => {
    const [cmd, verb] = firstTokens(seg)
    if (cmd === "pulumi" && PULUMI_LIFECYCLE.has(verb ?? "")) {
      return BLOCK("cloud-lifecycle", `pulumi ${verb} — cloud lifecycle is human-only`)
    }
    return null
  },
  (seg) => {
    const [cmd, verb] = firstTokens(seg)
    if (["kubectl", "oc", "k"].includes(cmd)) {
      if (K8S_MUTATIONS.has(verb ?? "")) {
        return BLOCK("k8s-mutation", `kubectl ${verb} mutates the cluster — human-only`)
      }
    }
    return null
  },
  (seg) => {
    const [cmd, verb] = firstTokens(seg)
    if (cmd === "helm" && HELM_MUTATIONS.has(verb ?? "")) {
      return BLOCK("k8s-mutation", `helm ${verb} mutates the cluster — human-only`)
    }
    return null
  },
  (seg) => {
    const [cmd, verb, obj] = firstTokens(seg)
    if (cmd === "argocd" && verb === "app" && ARGOCD_MUTATIONS.has(obj ?? "")) {
      return BLOCK("k8s-mutation", `argocd app ${obj} mutates the cluster — human-only`)
    }
    return null
  },
  (seg) => awsVerdict(seg),
  (seg) => {
    const [cmd, verb, obj] = firstTokens(seg)
    if (cmd === "vault" && (verb === "read" || (verb === "kv" && obj === "get"))) {
      return BLOCK("secrets", `vault ${verb} ${obj ?? ""} reads secrets from a secret manager`)
    }
    if ((cmd === "pass" || cmd === "gopass") && verb === "show") {
      return BLOCK("secrets", `${cmd} show reads secrets from a secret manager`)
    }
    if (cmd === "op" && ["read", "item"].includes(verb ?? "")) {
      return BLOCK("secrets", `op ${verb} reads secrets from 1Password`)
    }
    if (cmd === "sops" && (verb === "-d" || verb === "--decrypt")) {
      return BLOCK("secrets", "sops --decrypt reads encrypted secrets")
    }
    return null
  },
  (seg) => {
    const [cmd] = firstTokens(seg)
    if (!READERS.has(cmd ?? "")) return null
    if (argsOf(seg).some((a) => isSecretPathRef(a))) {
      return BLOCK("secrets", `reading a secrets file via ${cmd}`)
    }
    if (/<\s*([^\s]+)/.test(seg)) {
      const m = seg.match(/<\s*([^\s;|&]+)/)
      if (m && isSecretPathRef(m[1])) return BLOCK("secrets", `redirecting a secrets file into ${cmd}`)
    }
    return null
  },
  (_seg, raw) => {
    if (/169\.254\.169\.254/.test(raw) && /\b(curl|wget|http|nc|nmap)\b/.test(raw)) {
      return BLOCK("secrets", "fetching the cloud metadata endpoint reads credentials")
    }
    return null
  },
  (seg) => {
    const [cmd, verb, obj] = firstTokens(seg)
    if (cmd === "rm") {
      const args = argsOf(seg)
      const targets = args.filter((a) => !a.startsWith("-"))
      const precious = targets.find((t) => {
        const clean = t.replace(/['"]/g, "")
        if (clean === "/" || clean === "/*" || clean === "~" || clean === "~/*") return true
        if (/^\$HOME(\/|\*|$)/.test(clean) || /^\/(Users|home)\/[^/]+(\/|\*|$)/.test(clean)) return true
        const homePrefix = clean.replace(/^~/, "")
        return PRECIOUS_HOME.some((d) => homePrefix === `/${d}` || homePrefix.startsWith(`/${d}/`))
      })
      if (precious) return BLOCK("fs-nuke", `rm targets ${precious} — irreversible data loss`)
    }
    if (cmd === "mkfs" || cmd?.startsWith("mkfs.")) return BLOCK("fs-nuke", "mkfs formats a filesystem")
    if (cmd === "diskutil" && (verb ?? "").match(/erase|partition|split|secureErase|apfs/)) {
      return BLOCK("fs-nuke", `diskutil ${verb} destroys disk contents`)
    }
    if (cmd === "dd" && /\bof=\/dev\/(disk|rdisk|sd|nvme)/.test(seg)) {
      return BLOCK("fs-nuke", "dd writes to a raw device")
    }
    if (cmd === "shred" || cmd === "wipefs" || cmd === "srm") {
      return BLOCK("fs-nuke", `${cmd} irreversibly destroys data`)
    }
    if ((cmd === "chmod" || cmd === "chown") && (verb === "-R" || obj === "-R")) {
      const args = argsOf(seg).join(" ")
      if (/(^|\s)(\/|~|\$HOME)(\s|$|\*)/.test(args)) {
        return BLOCK("fs-nuke", `${cmd} -R on a system/home root`)
      }
    }
    if (cmd === "docker" && verb === "volume" && ["rm", "prune"].includes(obj ?? "")) {
      return BLOCK("fs-nuke", "docker volume rm/prune deletes volumes")
    }
    if (cmd === "docker" && verb === "system" && obj === "prune") {
      return BLOCK("fs-nuke", "docker system prune deletes containers/images/networks")
    }
    if (/:\(\)\s*\{.*\};\s*:/.test(seg)) return BLOCK("fs-nuke", "fork bomb")
    if (/>\s*\/dev\/(disk|rdisk|sd|nvme)/.test(seg)) return BLOCK("fs-nuke", "redirecting to a raw device")
    return null
  },
]

function isBenignSegment(segment: string): boolean {
  if (/\$\(|`/.test(segment) || /\bsudo\b/.test(segment)) return false
  const [cmd, verb, obj] = firstTokens(segment)
  if (!cmd) return false
  if (cmd === "rm") return argsOf(segment).every((a) => BENIGN_RM_RE.test(a))
  if (BENIGN_FIRST.has(cmd)) {
    if (cmd === "cat" || cmd === "head" || cmd === "tail") {
      return !argsOf(segment).some((a) => isSecretPathRef(a))
    }
    return true
  }
  if (cmd === "git") return BENIGN_GIT.has(verb ?? "")
  if (cmd === "docker") {
    if (BENIGN_DOCKER.has(verb ?? "")) return verb !== "system" || obj === "info" || obj === "df"
    return false
  }
  if (["kubectl", "oc", "k"].includes(cmd)) return K8S_READONLY.has(verb ?? "")
  if (cmd === "aws") return awsVerdict(segment)?.block !== true
  if (["terraform", "tf", "tofu"].includes(cmd)) {
    return !TF_LIFECYCLE.has(verb ?? "")
  }
  if (cmd === "helm") return !HELM_MUTATIONS.has(verb ?? "")
  return false
}

const FORCE_NLI_RE = new RegExp(
  [
    "\\b(terraform|tofu|tf|pulumi|helm|argocd|vault|sops|mkfs(\\.\\w+)?|wipefs|shred)\\b",
    "\\b(kubectl|oc|k)\\s+(create|apply|delete|edit|patch|replace|scale|rollout|autoscale|run|expose|exec|cp|attach|drain|cordon|uncordon|taint|label|annotate|set|approve|bind|evict)\\b",
    "\\baws\\s+s3\\s+(cp|mv|rm|sync|mb|rb|del)\\b",
    "\\baws\\s+[a-z][a-z0-9-]*\\s+(put|create|delete|update(?!-kubeconfig)|add|remove|attach|detach|tag|untag|terminate|start|stop|reboot|modify|register|deregister|enable|disable|cancel|associate|disassociate|upload|import|reset|restore|set|run|deploy|publish|invoke|execute|rotate|sign|move|copy|write|push|batch)[a-z0-9-]*\\b",
    "\\b(pass|gopass)\\s+show\\b|\\bop\\s+(read|item)\\b",
    "\\bdiskutil\\s+(erase|partition|split|secureErase|apfs)\\b",
    "\\bdd\\b[^;|&]*\\bof=/dev/|>\\s*/dev/(disk|rdisk|sd|nvme)",
    "\\brm\\b(?![^;|&]*(node_modules|\\.?venv|__pycache__|\\.pytest_cache|\\.mypy_cache|\\.ruff_cache|\\.coverage|egg-info|/dist|/build|/target)[^;|&]*)[^;|&]*-[a-zA-Z]*[rf]",
    "\\bid_rsa\\b|\\.pem\\b|\\b\\.env\\b|credentials|\\.ssh\\b|\\.aws/|\\.kube/",
    "169\\.254\\.169\\.254",
  ].join("|"),
)

export function classify(raw: string): Decision & { needsNli: boolean } {
  const segments = splitSegments(raw)
  for (const rule of RULES) {
    for (const seg of segments) {
      const verdict = rule(seg, raw)
      if (verdict?.block) return { ...verdict, needsNli: false }
    }
  }
  if (segments.every(isBenignSegment) && !FORCE_NLI_RE.test(raw)) {
    return { ...ALLOW, needsNli: false }
  }
  return { ...ALLOW, layer: "nli", needsNli: true }
}

const cache = new Map<string, Decision>()

function cachePut(key: string, value: Decision) {
  cache.set(key, value)
  if (cache.size > CACHE_MAX) cache.delete(cache.keys().next().value as string)
}

function openjevBin(): string {
  const candidates = [
    env.OPENJEV_GUARD_BIN,
    `${homedir()}/.local/bin/openjev-tool`,
  ].filter(Boolean) as string[]
  for (const candidate of candidates) if (existsSync(candidate)) return candidate
  return "openjev-tool"
}

function childEnv(): Record<string, string> {
  const e: Record<string, string> = {}
  for (const [k, v] of Object.entries(process.env)) if (v !== undefined) e[k] = v
  e.HF_HUB_OFFLINE = "1"
  if (!e.HF_HOME) {
    const t7 = "/Volumes/T7/ai-models/huggingface"
    if (existsSync(t7)) {
      e.HF_HOME = t7
      e.HF_HUB_CACHE = `${t7}/hub`
    }
  }
  return e
}

async function askServer(claim: string): Promise<{ probabilities: Probs; latencyMs: number } | null> {
  const started = Date.now()
  try {
    const health = await fetch(`${SERVER}/health`, { signal: AbortSignal.timeout(500) })
    if (!health.ok) return null
    const res = await fetch(`${SERVER}/v1/noul`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ premise: PREMISE, claim, model: MODEL }),
      signal: AbortSignal.timeout(HTTP_TIMEOUT),
    })
    if (!res.ok) return null
    const data = (await res.json()) as { probabilities?: { contradiction?: number } }
    const contradiction = data.probabilities?.contradiction
    if (contradiction === undefined) return null
    return { probabilities: data.probabilities as Probs, latencyMs: Date.now() - started }
  } catch {
    return null
  }
}

async function askCli(claim: string): Promise<{ probabilities: Probs; latencyMs: number } | null> {
  const started = Date.now()
  try {
    const { stdout } = await execFileAsync(
      openjevBin(),
      ["jev", "noul", PREMISE, claim],
      { timeout: CLI_TIMEOUT, env: childEnv(), maxBuffer: 16 * 1024 * 1024 },
    )
    const line = stdout.trim().split("\n").reverse().find((l) => l.startsWith("{"))
    if (!line) return null
    const data = JSON.parse(line) as { probabilities?: { contradiction?: number } }
    const contradiction = data.probabilities?.contradiction
    if (contradiction === undefined) return null
    return { probabilities: data.probabilities as Probs, latencyMs: Date.now() - started }
  } catch {
    return null
  }
}

export async function judge(raw: string): Promise<Decision> {
  const verdict = classify(raw)
  if (verdict.block) {
    await appendLog ("bash", raw, verdict)
    return verdict
  }
  if (!verdict.needsNli) return verdict
  const cached = cache.get(raw)
  if (cached) return { ...cached, layer: "cache" }
  const claim = `The shell command "${raw.slice(0, 2000)}" is safe for the AI agent to execute under this safety contract.`
  let result = await askServer(claim)
  let source: "server" | "cli" = "server"
  if (!result) {
    result = await askCli(claim)
    source = "cli"
  }
  if (!result) {
    if (FAIL_CLOSED) {
      const blocked = BLOCK("nli-unavailable", `openjev unavailable (${SERVER}) and OPENJEV_GUARD_FAIL=closed`)
      await appendLog("bash", raw, blocked)
      return blocked
    }
    const failOpen: Decision = { ...ALLOW, layer: "fail-open", reason: `openjev unavailable (${SERVER})` }
    await appendLog("bash", raw, failOpen)
    return failOpen
  }
  const { contradiction } = result.probabilities
  const decision: Decision =
    contradiction >= THRESHOLD
      ? {
          ...BLOCK(
            "nli-contradiction",
            `openjev judged the command unsafe: P(contradiction)=${contradiction.toFixed(3)} >= threshold ${THRESHOLD}`,
          ),
          layer: "nli",
          probabilities: result.probabilities,
          source,
          latencyMs: result.latencyMs,
        }
      : {
          ...ALLOW,
          layer: "nli",
          probabilities: result.probabilities,
          source,
          latencyMs: result.latencyMs,
        }
  cachePut(raw, decision)
  await appendLog("bash", raw, decision)
  return decision
}

let logDirReady = false

async function appendLog(tool: string, subject: string, decision: Decision): Promise<void> {
  if (!LOG_ENABLED) return
  try {
    if (!logDirReady) {
      await mkdir(dirname(LOG_PATH), { recursive: true })
      logDirReady = true
    }
    const p = decision.probabilities
    const record = {
      ts: new Date().toISOString(),
      tool,
      subject: subject.slice(0, 4000),
      decision: decision.block ? "block" : "allow",
      layer: decision.layer,
      rule: decision.rule || undefined,
      reason: decision.reason || undefined,
      contradiction: p ? Number(p.contradiction.toFixed(4)) : undefined,
      entailment: p?.entailment !== undefined ? Number(p.entailment.toFixed(4)) : undefined,
      neutral: p?.neutral !== undefined ? Number(p.neutral.toFixed(4)) : undefined,
      threshold: p !== undefined ? THRESHOLD : undefined,
      source: decision.source,
      latency_ms: decision.latencyMs,
    }
    await appendFile(LOG_PATH, `${JSON.stringify(record)}\n`)
  } catch {
    /* logging must never break the guard */
  }
}

function guardMessage(decision: Decision, subject: string): string {
  return [
    `openjev-guard blocked [${decision.rule}]: ${decision.reason}`,
    `Subject: ${subject}`,
    "Cloud/infra lifecycle, cluster mutations and secret access are human-only. Ask Dennis to run it personally.",
  ].join("\n")
}

export const ExecCliCheck: Plugin = async () => {
  if (DISABLED) return {}
  return {
    "tool.execute.before": async (input: any, output: any) => {
      if (input?.tool === "bash") {
        const command: unknown = output?.args?.command
        if (typeof command === "string" && command.trim()) {
          const decision = await judge(command)
          if (decision.block) throw new Error(guardMessage(decision, command))
        }
      }
      if (["read", "edit", "write"].includes(input?.tool ?? "")) {
        const filePath: unknown = output?.args?.filePath
        if (typeof filePath === "string" && isSecretPathRef(filePath)) {
          const decision = BLOCK("secrets", `${input.tool} on a secrets file`)
          await appendLog(input.tool, filePath, decision)
          throw new Error(guardMessage(decision, filePath))
        }
      }
    },
  }
}

export default ExecCliCheck
