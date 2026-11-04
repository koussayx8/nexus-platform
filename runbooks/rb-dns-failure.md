---
id: rb-dns-failure
title: DNS resolution failure
version: 1
fault_classes: [dns_failure]
signals: [CoreDNSDown, NXDOMAIN, nslookup]
allowed_actions: [escalate]
forbidden_actions: [delete_deployment, restart_workload]
updated: 2026-11-03
---
## Symptoms
Name resolution fails inside the cluster. Logs show no such host, temporary failure in name resolution, NXDOMAIN, lookup timeout or SERVFAIL. The application cannot reach services by name, pods stay Ready and CPU is normal.

## Diagnosis
Check whether several workloads fail to resolve the same names, which points at CoreDNS or its configuration, or only one name, which points at a wrong service name. Resolution lives outside the workload, so changing the workload does not fix it.

## Decision
A cluster DNS fault cannot be repaired from the workload. Escalate with investigate and name the failing hostnames.

## Action
escalate with recommendation kind investigate. No cluster mutation is requested.

## Escalation
Escalate to the platform owner when more than one namespace is affected.
