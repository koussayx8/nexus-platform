---
id: rb-cert-expiry
title: Certificate expiry
version: 1
fault_classes: [cert_expiry]
signals: [CertificateExpiring, x509, tls]
allowed_actions: [escalate]
forbidden_actions: [delete_deployment, restart_workload]
updated: 2026-11-03
---
## Symptoms
A TLS certificate expired or is about to. Clients and the service log x509 certificate has expired or is not yet valid, tls handshake failure, bad certificate. Connections drop at the handshake, pods stay Ready, and CPU and memory are normal.

## Diagnosis
Check the certificate notAfter date in the error text and the issuer. Renewal belongs to the certificate issuer and to its secret, neither of which the operator may touch. A restart reloads the same expired certificate.

## Decision
A certificate cannot be renewed by the operator. Escalate with investigate and name the certificate and its expiry.

## Action
escalate with recommendation kind investigate. No cluster mutation is requested.

## Escalation
Escalate to the platform owner as urgent when the certificate has already expired.
