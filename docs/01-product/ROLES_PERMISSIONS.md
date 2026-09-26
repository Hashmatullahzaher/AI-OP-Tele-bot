# Roles and authorization model (draft)

| Role | Default access | Restrictions |
|---|---|---|
| Platform operator | Manage software versions, deployment health and tenant provisioning metadata. | No implicit access to customer content or credentials. Support access requires separately audited time-limited grant. |
| Tenant administrator | Approve connectors, source allowlists, user/role assignments and retention policies for own tenant. | Cannot grant powers exceeding source-system ACLs. |
| Authorized employee | Use permitted read capabilities and only authorized resource scopes; download reports only for their own authorized queries. | No access to other employees' privileged documents solely because the bot can read them. |
| Source-system/Drive admin | Configure source app grants and limits in the source system. | Source ACLs are applied in addition to OS AI policy. |
| Approver/auditor | Review approval requests and immutable audit metadata within explicitly assigned scope. | Cannot approve own high-risk actions unless source policy explicitly permits. |

Enforcement occurs server-side at ingress and again immediately before each connector call and artifact delivery. Telegram user ID alone is not authentication; pair with a verified enterprise identity and revoke promptly. Group chats and forwarding of sensitive files are disabled by default in pilot. Revalidate rights when underlying Drive permissions or source account grants change.
