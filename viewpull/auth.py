"""AWS authentication via Okta SSO (okta-aws-cli).

Ported from ``process-config-schema-export/auth.py`` (ASP-1537) — the canonical auth model for
AWS tools in this belt (see root CLAUDE.md). The tool implements **no** Okta flow itself: it
builds a boto3 Session from a named profile (``--profile`` / ``AWS_PROFILE``) and verifies it
with an ``sts get-caller-identity`` preflight. On missing/expired credentials it either:

* raises :class:`AuthError` with guidance (default), or
* (with ``--okta-login``) shells out to ``okta-aws-cli`` to refresh ``~/.aws/credentials``
  and retries once.

The recommended setup is a ``~/.aws/config`` profile whose ``credential_process`` runs
``okta-aws-cli`` — then boto3 refreshes transparently and ``--okta-login`` is never needed
(see ``.env.example``).
"""
from __future__ import annotations

import os
import shlex
import subprocess

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError


class AuthError(RuntimeError):
    pass


# UoN's AWS region (documented in .env.example and both belt tools). Used as the final fallback so
# a run without AWS_REGION / a profile region doesn't crash with botocore's NoRegionError; override
# with --region or AWS_REGION/AWS_DEFAULT_REGION.
DEFAULT_REGION = "ap-southeast-2"


def resolve_region(region: str | None = None) -> str:
    """The effective region: explicit arg → AWS_REGION → AWS_DEFAULT_REGION → UoN default."""
    return (region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
            or DEFAULT_REGION)


# STS error codes that indicate missing/expired/invalid credentials.
_CRED_ERROR_CODES = {
    "ExpiredToken", "ExpiredTokenException", "InvalidClientTokenId",
    "UnrecognizedClientException", "RequestExpired", "AccessDenied",
}
# botocore credential-resolution exception class names (SSO/process providers).
_CRED_ERROR_TYPES = {
    "SSOTokenLoadError", "UnauthorizedSSOTokenError",
    "TokenRetrievalError", "CredentialRetrievalError",
}


def make_session(profile: str | None = None, region: str | None = None) -> boto3.Session:
    profile = profile or os.environ.get("AWS_PROFILE") or None
    return boto3.Session(profile_name=profile, region_name=resolve_region(region))


def okta_login_command(profile: str | None) -> list[str]:
    """Build the ``okta-aws-cli`` argv for ``--okta-login``.

    Honours ``OKTA_LOGIN_COMMAND`` verbatim if set (paste your exact working command);
    otherwise builds a default from the ``OKTA_*`` env vars, writing to the named profile.
    """
    override = os.environ.get("OKTA_LOGIN_COMMAND")
    if override:
        return shlex.split(override)
    cmd = [
        "okta-aws-cli", "web",
        "--format", "aws-credentials",
        "--write-aws-credentials",
        "--org-domain", os.environ.get("OKTA_ORG_DOMAIN", ""),
        "--oidc-client-id", os.environ.get("OKTA_OIDC_CLIENT_ID", ""),
        "--aws-acct-fed-app-id", os.environ.get("OKTA_AWS_ACCT_FED_APP_ID", ""),
    ]
    if profile:
        cmd += ["--profile", profile]
    return cmd


def run_okta_login(profile: str | None, *, runner=subprocess.run) -> None:
    has_override = bool(os.environ.get("OKTA_LOGIN_COMMAND"))
    has_parts = bool(
        os.environ.get("OKTA_OIDC_CLIENT_ID") and os.environ.get("OKTA_AWS_ACCT_FED_APP_ID")
    )
    if not has_override and not has_parts:
        raise AuthError(
            "cannot run okta-aws-cli: set OKTA_OIDC_CLIENT_ID + OKTA_AWS_ACCT_FED_APP_ID "
            "(and OKTA_ORG_DOMAIN) in .env, or set OKTA_LOGIN_COMMAND to your full command."
        )
    cmd = okta_login_command(profile)
    print(f"refreshing AWS credentials via: {' '.join(cmd)}")
    try:
        result = runner(cmd)
    except FileNotFoundError as exc:
        raise AuthError(
            "okta-aws-cli not found on PATH. Install it: https://github.com/okta/okta-aws-cli"
        ) from exc
    if getattr(result, "returncode", 0) != 0:
        raise AuthError(f"okta-aws-cli exited with status {result.returncode}")


def _is_credential_error(exc: Exception) -> bool:
    if isinstance(exc, NoCredentialsError):
        return True
    if isinstance(exc, ClientError):
        return exc.response.get("Error", {}).get("Code", "") in _CRED_ERROR_CODES
    return type(exc).__name__ in _CRED_ERROR_TYPES


def _expired_guidance(profile: str | None) -> str:
    p = profile or "<profile>"
    return (
        f"AWS credentials for profile '{p}' are missing or expired.\n"
        f"Refresh with okta-aws-cli, e.g.:\n"
        f"  okta-aws-cli web --write-aws-credentials --profile {p} \\\n"
        f"    --org-domain $OKTA_ORG_DOMAIN --oidc-client-id $OKTA_OIDC_CLIENT_ID \\\n"
        f"    --aws-acct-fed-app-id $OKTA_AWS_ACCT_FED_APP_ID\n"
        f"…or re-run with --okta-login to do it automatically, or configure a "
        f"credential_process profile (see .env.example) so boto3 refreshes transparently."
    )


def try_silent_session(
    *, profile: str | None = None, region: str | None = None
) -> boto3.Session | None:
    """A session only if existing credentials already pass the sts preflight — never interactive
    (no okta-aws-cli shell-out, no surprise browser popup) and never raises for credential or
    connectivity problems: returns None so the caller can warn and leave the gap (`auto` mode)."""
    try:
        return resolve_session(profile=profile, region=region, okta_login=False)
    except (AuthError, BotoCoreError, ClientError):
        return None


def resolve_session(
    *,
    profile: str | None = None,
    region: str | None = None,
    okta_login: bool = False,
    runner=subprocess.run,
) -> boto3.Session:
    """Return a credential-verified boto3 Session.

    Preflights with ``sts get-caller-identity``. On a credential error: if ``okta_login`` is
    set, refreshes via ``okta-aws-cli`` and retries once; otherwise raises :class:`AuthError`.
    """
    profile = profile or os.environ.get("AWS_PROFILE") or None
    region = resolve_region(region)

    session = make_session(profile, region)
    try:
        session.client("sts").get_caller_identity()
        return session
    except (BotoCoreError, ClientError) as exc:
        if not _is_credential_error(exc):
            raise
        if not okta_login:
            raise AuthError(_expired_guidance(profile)) from exc

    run_okta_login(profile, runner=runner)
    session = make_session(profile, region)
    try:
        session.client("sts").get_caller_identity()
    except (BotoCoreError, ClientError) as exc:
        raise AuthError("AWS credentials still invalid after okta-aws-cli refresh.") from exc
    return session
