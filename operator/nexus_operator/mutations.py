"""The operator's two mutating requests, Scale and Eviction (spec §9, §14).

They go through the recorder package's AuditedClient, which returns the API server's Audit-Id and
never raises on a 4xx: a denial by RBAC, Kyverno or the PDB is an outcome to record. Nothing else
in the operator sends a mutating request. dry_run sends the same request with dryRun=All (Safety
Gate stage 7 and the compensation preview).
"""

from .recorder import ApiResult, AuditedClient


class Mutations:
    def __init__(self, client: AuditedClient) -> None:
        self._client = client

    async def patch_scale(
        self, namespace: str, name: str, replicas: int, dry_run: bool = False
    ) -> ApiResult:
        """Merge-patch the Scale subresource of a Deployment."""
        return await self._client.request(
            "PATCH",
            f"/apis/apps/v1/namespaces/{namespace}/deployments/{name}/scale",
            {"spec": {"replicas": replicas}},
            dry_run=dry_run,
            content_type="application/merge-patch+json",
        )

    async def evict(
        self, namespace: str, pod: str, uid: str, dry_run: bool = False
    ) -> ApiResult:
        """Create an Eviction for one pod, preconditioned on its UID."""
        body = {
            "apiVersion": "policy/v1",
            "kind": "Eviction",
            "metadata": {"name": pod, "namespace": namespace},
            "deleteOptions": {"preconditions": {"uid": uid}},
        }
        return await self._client.request(
            "POST",
            f"/api/v1/namespaces/{namespace}/pods/{pod}/eviction",
            body,
            dry_run=dry_run,
        )

    async def close(self) -> None:
        await self._client.close()
