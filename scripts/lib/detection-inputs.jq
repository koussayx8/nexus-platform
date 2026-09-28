# detection-inputs.jq — the M1b-6c checks (plan M1b-6 6c; spec §3, §27 "kube-state-metrics series
# and Alertmanager v2 API"): the series the M1b-7 rules and the §3 kube-state-metrics alerts read,
# and the Alertmanager v2 API the Alert Poller reads. Change 21: an empty or NaN result is a FAIL.
#
# A pure jq filter, no shell wrapper; it never contacts anything. Two modes, --arg mode:
#   list   (run with -n) prints [{id, api, path}]: what to fetch. `api` is prometheus or
#          alertmanager; `path` is the API path, query URL-encoded, relative to the service root.
#   check  (the default) reads one object {<id>: <saved response body>} on stdin and prints
#          {ok, checks: [{id, ok, why, seen}], unexpected}. ok only if every check passes; a
#          missing response fails its check. Build the object from a directory of <id>.json files:
#            jq -n 'reduce inputs as $d ({}; . + {(input_filename | split("/") | last
#              | rtrimstr(".json")): $d})' <dir>/*.json | jq --arg mode check -f detection-inputs.jq
# Live use (after the M1 exit, approved separately): fetch each path through the API server's
# service proxy (kubectl get --raw), no port-forward, and save each body as <id>.json. Tests:
# scripts/tests/detection-inputs.sh (fixtures only).
#
# What each check requires:
#   Prometheus vector: status success, a non-empty vector, series for both nexus-dev and
#     nexus-prod, every value finite (not NaN or ±Inf), and the labels the rules select or group
#     on. http_requests_total's status must be grouped ("2xx"): the M1b-7 error ratio matches
#     status="5xx" literally.
#   Prometheus metadata: the metric is known with the expected type. kube-state-metrics emits the
#     CrashLoopBackOff series only while a container waits, so at idle only the name can be
#     checked; the reason value needs a real CrashLoopBackOff.
#   Alertmanager: the v2 status fields; alerts carry what the Alert Poller reads (fingerprint,
#     labels.alertname, status.state, startsAt) and include an active Watchdog; groups carry
#     labels, a receiver and alerts, and one of them holds the Watchdog.

def namespaces: ["nexus-dev", "nexus-prod"];

def checks: [
  {id: "ksm-spec-replicas", api: "prometheus", kind: "vector",
   query: "kube_deployment_spec_replicas{namespace=~\"nexus-dev|nexus-prod\",deployment=\"sample-api\"}",
   labels: ["namespace", "deployment"]},
  {id: "ksm-replicas-unavailable", api: "prometheus", kind: "vector",
   query: "kube_deployment_status_replicas_unavailable{namespace=~\"nexus-dev|nexus-prod\",deployment=\"sample-api\"}",
   labels: ["namespace", "deployment"]},
  {id: "ksm-waiting-reason-metadata", api: "prometheus", kind: "metadata",
   metric: "kube_pod_container_status_waiting_reason", type: "gauge"},
  {id: "cadvisor-cpu", api: "prometheus", kind: "vector",
   query: "container_cpu_usage_seconds_total{job=\"kubelet\",namespace=~\"nexus-dev|nexus-prod\",container=\"sample-api\"}",
   labels: ["namespace", "pod", "container"]},
  {id: "http-requests-labels", api: "prometheus", kind: "vector",
   query: "http_requests_total{job=\"sample-api\",namespace=~\"nexus-dev|nexus-prod\"}",
   labels: ["job", "namespace", "handler", "status"], grouped_status: true},
  {id: "http-duration-bucket-labels", api: "prometheus", kind: "vector",
   query: "http_request_duration_seconds_bucket{job=\"sample-api\",namespace=~\"nexus-dev|nexus-prod\"}",
   labels: ["job", "namespace", "handler", "le"]},
  {id: "am-status", api: "alertmanager", kind: "am-status", path: "/api/v2/status"},
  {id: "am-alerts", api: "alertmanager", kind: "am-alerts", path: "/api/v2/alerts"},
  {id: "am-alert-groups", api: "alertmanager", kind: "am-groups", path: "/api/v2/alerts/groups"}
];

def path_of:
  if .kind == "vector" then "/api/v1/query?query=" + (.query | @uri)
  elif .kind == "metadata" then "/api/v1/metadata?metric=" + (.metric | @uri)
  else .path end;

# why: the non-empty reasons, joined.
def why($reasons): $reasons | map(select(. != null)) | join("; ");

def finite: . != "NaN" and . != "+Inf" and . != "-Inf";

def prom_error: "Prometheus status \(.status // "?"): \(.error // "no error text")";

def vector_check($c):
  if .status != "success" then {ok: false, why: prom_error}
  elif .data.resultType != "vector" then {ok: false, why: "resultType \(.data.resultType)"}
  else .data.result as $r
    | ([$r[].metric.namespace] | unique) as $ns
    | [namespaces[] | select(. as $n | $ns | index($n) | not)] as $missing_ns
    | [$r[] | select(.value[1] | finite | not)] as $not_finite
    | ([$r[] | .metric as $m | $c.labels[] | select($m[.] == null)] | unique) as $missing_labels
    | (if $c.grouped_status then [$r[].metric.status // empty | select(test("^[1-5]xx$") | not)] | unique
       else [] end) as $ungrouped
    | {ok: (($r | length) > 0 and ($missing_ns | length) == 0 and ($not_finite | length) == 0
            and ($missing_labels | length) == 0 and ($ungrouped | length) == 0),
       why: why([
         (if ($r | length) == 0 then "empty result" else null end),
         (if ($missing_ns | length) > 0 then "no series for \($missing_ns | join(","))" else null end),
         (if ($not_finite | length) > 0 then "\($not_finite | length) value(s) not finite" else null end),
         (if ($missing_labels | length) > 0 then "label(s) missing: \($missing_labels | join(","))" else null end),
         (if ($ungrouped | length) > 0 then "status not grouped: \($ungrouped | join(","))" else null end)]),
       seen: ({series: ($r | length), namespaces: $ns, labels: ([$r[].metric | keys[]] | unique)}
              + (if $c.grouped_status then {status: ([$r[].metric.status] | unique)} else {} end)
              + (if ($c.labels | index("le")) then {le: ([$r[].metric.le] | unique)} else {} end))}
  end;

def metadata_check($c):
  if .status != "success" then {ok: false, why: prom_error}
  else (.data[$c.metric] // []) as $m
    | ([$m[].type] | unique) as $types
    | {ok: (($m | length) > 0 and $types == [$c.type]),
       why: why([
         (if ($m | length) == 0 then "\($c.metric) is unknown to Prometheus" else null end),
         (if ($m | length) > 0 and $types != [$c.type] then "type \($types | join(",")), want \($c.type)" else null end)]),
       seen: {types: $types}}
  end;

def am_status_check:
  if type != "object" then {ok: false, why: "not an Alertmanager v2 status object"}
  else
    [(if (.versionInfo.version // "") == "" then "versionInfo.version" else null end),
     (if (.cluster.status | type) != "string" then "cluster.status" else null end),
     (if (.config.original | type) != "string" then "config.original" else null end),
     (if (.uptime | type) != "string" then "uptime" else null end)] as $missing
    | {ok: (($missing | map(select(. != null)) | length) == 0),
       why: (why($missing) | if . == "" then "" else "missing: " + . end),
       seen: {version: .versionInfo.version, cluster: .cluster.status}}
  end;

# The fields the Alert Poller reads from each alert.
def poller_fields_ok:
  (.fingerprint | type) == "string" and (.fingerprint | length) > 0
  and (.labels | type) == "object" and ((.labels.alertname // "") | length) > 0
  and ((.status.state // "") | IN("active", "suppressed", "unprocessed"))
  and (.startsAt | type) == "string";

def am_alerts_check:
  if type != "array" then {ok: false, why: "not an Alertmanager v2 alert list"}
  else
    [.[] | select(poller_fields_ok | not)] as $bad
    | [.[] | select(.labels.alertname == "Watchdog" and .status.state == "active")] as $watchdog
    | {ok: (length > 0 and ($bad | length) == 0 and ($watchdog | length) > 0),
       why: why([
         (if length == 0 then "no alerts" else null end),
         (if ($watchdog | length) == 0 then "no active Watchdog" else null end),
         (if ($bad | length) > 0 then "\($bad | length) alert(s) lack fingerprint, labels.alertname, status.state or startsAt" else null end)]),
       seen: {alerts: length, alertnames: ([.[].labels.alertname] | unique), states: ([.[].status.state] | unique)}}
  end;

def am_groups_check:
  if type != "array" then {ok: false, why: "not an Alertmanager v2 alert-group list"}
  else
    [.[] | select(((.labels | type) == "object" and (.receiver.name | type) == "string"
                   and (.alerts | type) == "array") | not)] as $bad
    | [.[].alerts[]? | select(.labels.alertname == "Watchdog")] as $watchdog
    | {ok: (length > 0 and ($bad | length) == 0 and ($watchdog | length) > 0),
       why: why([
         (if length == 0 then "no groups" else null end),
         (if ($watchdog | length) == 0 then "no group holds the Watchdog" else null end),
         (if ($bad | length) > 0 then "\($bad | length) group(s) lack labels, receiver.name or alerts" else null end)]),
       seen: {groups: length, receivers: ([.[].receiver.name] | unique)}}
  end;

if ($ARGS.named.mode // "check") == "list" then
  [checks[] | {id, api, path: path_of}]
else
  . as $got
  | [checks[] as $c
     | {id: $c.id}
       + (if ($got | has($c.id) | not) then {ok: false, why: "no response \($c.id).json"}
          else $got[$c.id]
            | if $c.kind == "vector" then vector_check($c)
              elif $c.kind == "metadata" then metadata_check($c)
              elif $c.kind == "am-status" then am_status_check
              elif $c.kind == "am-alerts" then am_alerts_check
              else am_groups_check end
          end)] as $results
  | {ok: ($results | all(.ok)), checks: $results,
     unexpected: ([$got | keys[]] - [checks[].id])}
end
