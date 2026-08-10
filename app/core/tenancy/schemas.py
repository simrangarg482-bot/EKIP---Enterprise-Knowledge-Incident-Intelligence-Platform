"""Pydantic contracts for core/tenancy.

Owned by: core/tenancy. Local to this submodule (PROJECT_STRUCTURE.md: types
specific to one submodule live in its own schemas.py; only genuinely shared
types live in shared/schemas/). Read models map from the ORM rows defined in
database/models/tenancy_models.py via `from_attributes=True`, so a service can
build one directly from a row (`Organization.model_validate(row)`) without a
manual field-by-field copy -- same pattern as core/audit/schemas.py and
core/users/schemas.py.

Scope (PROJECT_PLAN.md section 9.2): organizations, projects, SSO
configuration, and connector configuration. Deliberately excluded, even
though their ORM models live in the same tenancy_models.py file:
  - `ExternalIdentityMapping` -- resolving an IdP subject claim to a user is
    core/auth's federation-lookup concern (PROJECT_PLAN.md section 3.3), not
    tenancy's.
  - `ProjectMembership` -- project-scoped role assignment is core/users's
    RBAC concern (PROJECT_PLAN.md section 3.6), consistent with the boundary
    already drawn when Identity/core.users were made organization-scoped
    (ENGINEERING_DECISIONS.md #004).

Secrets discipline (PROJECT_PLAN.md section 12.5): `client_secret_ref` and
`ConnectorConfig.credential_ref` below are references/identifiers into an
encrypted secret store, never a usable raw credential -- core/tenancy stores
and returns only the reference, consistent with its "must never do the
actual OAuth handshake" boundary (PROJECT_PLAN.md section 9.2). It is
therefore safe for these read models to include them. The two deliberate
exceptions are `ConnectorConfigCreate.credential_ref` and
`SSOConfigurationCreate.client_secret_ref` (below) -- a caller registering a
new connector or configuring SSO still submits the *plaintext*
credential/secret once, at setup time; `core.tenancy.service.
register_connector`/`configure_sso` envelope-encrypt it (`app.shared.
security`) before it is ever persisted, so by the time it comes back out
through `ConnectorConfig.credential_ref`/`SSOConfiguration.client_secret_ref`
it is the encrypted envelope, not what was submitted.

`AccessRuleCreate`/`InvitationCreate`/`ProvisioningDecision` back the SSO
provisioning-policy design (ENGINEERING_DECISIONS.md's provisioning-policy
entry): "who may join this organization" is core/tenancy's responsibility,
distinct from core/auth (verifies authentication only) and core/users
(creates users / manages roles once provisioning has already been decided).
`grants_role` on the two Create schemas is a role *name* (e.g. `"engineer"`),
not a raw `grants_role_id` -- resolving a name to an id is the service
layer's job (via core/users's role lookup), so callers of this module's
public interface never need to already know a role's UUID.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# --- Local vocabularies ----------------------------------------------------
# Kept local rather than promoted to shared/schemas/common.py for now -- only
# core/tenancy reads/writes these today. Promote them if/when another module
# (e.g. ingestion/, reading ConnectorStatus) needs the same vocabulary.

OrganizationStatus = Literal["onboarding", "active", "suspended"]
SSOProvider = Literal["entra_id", "okta", "auth0", "google_workspace"]
SSOProtocol = Literal["oidc", "saml"]
ConnectorSource = Literal[
    "slack",
    "teams",
    "github",
    "azure_devops",
    "jira",
    "confluence",
    "sharepoint",
    "runbooks",
    "monitoring",
]
"""`"monitoring"` (added alongside `agents.investigation.live.
MonitoringLiveSource`'s registration into `_LIVE_SOURCES`) has no ingestion
connector or `_CONNECTOR_REGISTRY` entry (`app.ingestion.service`) -- it is
reachable only as a live-evidence source for the Investigation Agent, not as
a document-ingestion connector. A `connector_configs` row with
`source="monitoring"` is therefore only ever consulted by
`agents.investigation.evidence`, never by `app.ingestion.workers`; registering
one will not enqueue ingestion jobs the way the other seven sources would.
"""
ConnectorStatus = Literal["connecting", "active", "error", "disconnected"]
AccessRuleType = Literal["domain", "group"]
InvitationStatus = Literal["pending", "accepted", "expired", "revoked"]


# --- Organizations -----------------------------------------------------------


class OrganizationCreate(BaseModel):
    """Request body for `create_organization`.

    `slug` is constrained to URL-safe lowercase segments since it's used
    directly in the per-organization login URL (PROJECT_PLAN.md section 3.3:
    `https://app.ekip.io/o/{org-slug}/login`) -- validating its shape here,
    once, means every caller (onboarding UI, admin API) gets the same rule
    rather than re-implementing it.
    """

    name: str
    slug: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", min_length=1, max_length=63)


class Organization(BaseModel):
    """A company that has purchased EKIP, as returned by the read surface."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    name: str
    slug: str
    status: OrganizationStatus
    created_at: datetime
    updated_at: datetime


# --- Projects ----------------------------------------------------------------


class ProjectCreate(BaseModel):
    """Request body for `create_project`.

    `is_default` marks the auto-created "General" project every organization
    gets at onboarding time (PROJECT_PLAN.md section 3.2) -- callers creating
    additional, explicitly-scoped projects should leave this `False`.
    """

    name: str
    is_default: bool = False


class Project(BaseModel):
    """A scoping unit within an organization (e.g. "Payments team")."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    is_default: bool
    created_at: datetime
    updated_at: datetime


# --- SSO configuration ---------------------------------------------------------


class SSOConfigurationCreate(BaseModel):
    """Request body for configuring (or replacing) an organization's SSO.

    All four supported providers speak OIDC (PROJECT_PLAN.md section 3.3), so
    `protocol` defaults to `"oidc"` and one shape covers all of them; `saml`
    is accepted here as a forward-compatible option for a future SAML-only
    IdP, not because any current provider needs it. `client_secret_ref` is
    the *plaintext* OIDC client secret as issued by the IdP -- naming it
    `..._ref` matches `ConnectorConfigCreate.credential_ref`'s naming (both
    are call-time-plaintext, at-rest-encrypted), not a claim that the caller
    must already hold a reference into the encrypted secret store.
    `core.tenancy.service.configure_sso` is what turns this into a real
    encrypted-at-rest reference (`app.shared.security.encrypt_secret`)
    before persisting it -- the same encrypt-at-write responsibility
    `register_connector` already has for `ConnectorConfigCreate.
    credential_ref`.
    """

    provider: SSOProvider
    protocol: SSOProtocol = "oidc"
    issuer_url: str
    client_id: str
    client_secret_ref: str


class SSOConfiguration(BaseModel):
    """An organization's SSO federation settings, as returned by the read
    surface. `core/auth` reads this to determine which IdP to redirect an
    employee to (PROJECT_PLAN.md section 3.3, step 2).
    """

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    provider: SSOProvider
    protocol: SSOProtocol
    issuer_url: str
    client_id: str
    client_secret_ref: str
    created_at: datetime
    updated_at: datetime


# --- Connector configuration ----------------------------------------------------


class ConnectorConfigCreate(BaseModel):
    """Request body for `register_connector`.

    `project_id` is optional: a connector can be organization-wide (e.g. one
    GitHub org covering every team) or scoped to a single project
    (PROJECT_PLAN.md section 3.2). `config` holds source-specific settings
    (which Slack workspace, which repos) whose shape genuinely differs per
    source -- left as a free-form mapping for the same EAV-style reason
    `document_metadata` is, per DATABASE_DESIGN.md.
    """

    source: ConnectorSource
    credential_ref: str
    project_id: uuid.UUID | None = None
    config: dict = Field(default_factory=dict)


class ConnectorConfig(BaseModel):
    """One (organization, external tool) connection, as returned by the read
    surface -- backs `list_connectors` (PROJECT_PLAN.md section 9.2).
    """

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    project_id: uuid.UUID | None
    source: ConnectorSource
    credential_ref: str
    config: dict
    status: ConnectorStatus
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime


# --- Organization access rules (domain / group auto-join) --------------------


class AccessRuleCreate(BaseModel):
    """Request body for `create_access_rule`.

    `value` is interpreted according to `rule_type`: an email domain (e.g.
    `"nevikenz.com"`, no leading `@`) for `"domain"`, or an IdP group name/id
    (e.g. `"engineering"`) for `"group"`. `grants_role` is the platform role
    *name* a matching login should receive -- resolved to a `grants_role_id`
    by the service layer, not carried as a raw id here.
    """

    rule_type: AccessRuleType
    value: str
    grants_role: str
    is_active: bool = True


class AccessRule(BaseModel):
    """A configured domain/group auto-join rule, as returned by the read
    surface. See `database/models/tenancy_models.py::OrganizationAccessRule`
    for why there is no separate `"email"` rule type -- that need is served
    by `Invitation` instead.
    """

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    rule_type: AccessRuleType
    value: str
    grants_role_id: uuid.UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime


# --- Invitations ---------------------------------------------------------------


class InvitationCreate(BaseModel):
    """Request body for `create_invitation`.

    `expires_at` is optional: omitting it lets the service apply a sensible
    platform default (rather than forcing every caller to compute one), while
    still allowing an admin UI to offer a custom expiry if needed.
    """

    email: str
    grants_role: str
    expires_at: datetime | None = None


class Invitation(BaseModel):
    """A pending, accepted, expired, or revoked invitation, as returned by
    the read surface.
    """

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    email: str
    status: InvitationStatus
    grants_role_id: uuid.UUID
    invited_by: uuid.UUID
    expires_at: datetime
    accepted_at: datetime | None
    created_at: datetime
    updated_at: datetime


# --- Provisioning decision -----------------------------------------------------


class ProvisioningDecision(BaseModel):
    """The result of evaluating whether a verified SSO login may provision a
    user in an organization -- the return type of `evaluate_provisioning`.

    Not an ORM-backed read model (there is no `provisioning_decisions` table;
    this is a computed value), so no `from_attributes`/`frozen` config is
    needed beyond the default. `reason` is a stable, machine-readable code
    (`"invitation_match"` / `"domain_match"` / `"group_match"` /
    `"no_matching_policy"`) for logging/observability -- not meant to be
    shown to the end user verbatim.
    """

    allowed: bool
    grants_role_id: uuid.UUID | None = None
    matched_invitation_id: uuid.UUID | None = None
    reason: str