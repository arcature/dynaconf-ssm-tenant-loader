#!/usr/bin/env python3
"""
One-off migration of SSM parameters from the dynaconf-ssm-tenant-loader
1.x layout to the 2.0 env-first layout.

Old:                                        New:
  /<prefix>/app/<env>/...                     /<prefix>/<env>/app/default/...
  /<prefix>/app/<variant>/<env>/...           /<prefix>/<env>/app/<variant>/...
  /<prefix>/tenants/<t>/<env>/...             /<prefix>/<env>/tenants/<t>/default/...
  /<prefix>/tenants/<t>/<variant>/<env>/...   /<prefix>/<env>/tenants/<t>/<variant>/...

In the old layout, <env> and <variant> occupy the same slot, so
classification requires the authoritative env list (--envs). An
env-name match takes precedence: /p/app/qa/... is the qa env tier,
never a variant named "qa". Anything unclassifiable is reported and
skipped, never guessed.

Source parameters are never modified or deleted. Requires only boto3.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections import Counter
from dataclasses import dataclass, field

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

RESERVED_TIER_NAMES = {"default", "app", "tenants"}


@dataclass
class Plan:
    migrations: list[dict] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (name, reason)
    warnings: list[str] = field(default_factory=list)


class CountingClient:
    """Wraps the SSM client so every API request is tallied for the summary."""

    def __init__(self, client):
        self._client = client
        self.calls = Counter()

    def __getattr__(self, name):
        method = getattr(self._client, name)

        def wrapper(**kwargs):
            self.calls[name] += 1
            return method(**kwargs)

        return wrapper

    def paginate(self, api, **kwargs):
        paginator = self._client.get_paginator(api)
        for page in paginator.paginate(**kwargs):
            self.calls[api] += 1  # one tally per page == per HTTP request
            yield page


def digest(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()[:12]} len={len(value)}"


def classify(name: str, prefix: str, target_prefix: str, envs: set[str]):
    """Map an old full parameter name to its new full name, or raise ValueError."""
    segments = name.strip("/").split("/")
    prefix_segments = prefix.strip("/").split("/")
    rest = segments[len(prefix_segments) :]

    if not rest:
        raise ValueError("bare prefix parameter")

    if rest[0] in envs:
        raise ValueError("already in target (env-first) layout")

    if rest[0] == "app":
        family, rest = ["app"], rest[1:]
    elif rest[0] == "tenants":
        if len(rest) < 2:
            raise ValueError("tenants family with no tenant segment")
        family, rest = ["tenants", rest[1]], rest[2:]
    else:
        raise ValueError(f"first segment {rest[0]!r} is neither 'app' nor 'tenants'")

    if not rest:
        raise ValueError("no env segment")

    if rest[0] in envs:
        env, tier, key = rest[0], "default", rest[1:]
    elif len(rest) >= 2 and rest[1] in envs:
        variant, env, key = rest[0], rest[1], rest[2:]
        if variant.lower() in RESERVED_TIER_NAMES:
            raise ValueError(f"variant {variant!r} collides with a reserved segment")
        tier = variant
    else:
        raise ValueError(
            f"cannot classify {'/'.join(rest)!r}: neither segment 1 nor 2 is a"
            " known env (check --envs)"
        )

    if not key:
        raise ValueError(
            "parameter sits exactly at a tier root; the 2.0 loader reads"
            " strictly below tier paths, so it needs a leaf key"
        )

    new = "/" + "/".join([target_prefix.strip("/"), env, *family, tier, *key])
    return new, env, tier


def build_plan(ssm: CountingClient, args) -> Plan:
    plan = Plan()
    envs = set(args.envs)

    # Metadata (KeyId, Description, Tier, Policies) is only available via
    # DescribeParameters, which never returns values.
    metadata: dict[str, dict] = {}
    for page in ssm.paginate(
        "describe_parameters",
        ParameterFilters=[
            {
                "Key": "Path",
                "Option": "Recursive",
                "Values": [f"/{args.prefix.strip('/')}"],
            }
        ],
    ):
        for param in page["Parameters"]:
            metadata[param["Name"]] = param

    for page in ssm.paginate(
        "get_parameters_by_path",
        Path=f"/{args.prefix.strip('/')}",
        Recursive=True,
        WithDecryption=True,
    ):
        for param in page["Parameters"]:
            name = param["Name"]
            try:
                new_name, _env, _tier = classify(
                    name, args.prefix, args.target_prefix, envs
                )
            except ValueError as exc:
                plan.skipped.append((name, str(exc)))
                continue

            meta = metadata.get(name, {})
            if meta.get("Policies"):
                plan.warnings.append(
                    f"{name}: has parameter policies, which are NOT copied"
                )

            plan.migrations.append(
                {
                    "old": name,
                    "new": new_name,
                    "Type": param["Type"],
                    "Value": param["Value"],
                    "KeyId": meta.get("KeyId"),
                    "Description": meta.get("Description"),
                    "Tier": meta.get("Tier"),
                }
            )

    # Two old names must never collapse into one new name.
    targets = Counter(m["new"] for m in plan.migrations)
    for new_name, count in targets.items():
        if count > 1:
            colliding = [m["old"] for m in plan.migrations if m["new"] == new_name]
            plan.migrations = [m for m in plan.migrations if m["new"] != new_name]
            for old in colliding:
                plan.skipped.append(
                    (old, f"collision: multiple sources map to {new_name}")
                )

    return plan


def execute(ssm: CountingClient, plan: Plan, args) -> tuple[int, int]:
    written = already = 0
    for m in plan.migrations:
        kwargs = {
            "Name": m["new"],
            "Value": m["Value"],
            "Type": m["Type"],
            "Overwrite": args.overwrite,
        }
        if m["KeyId"]:
            kwargs["KeyId"] = m["KeyId"]
        if m["Description"]:
            kwargs["Description"] = m["Description"]
        if m["Tier"] and m["Tier"] != "Standard":
            kwargs["Tier"] = m["Tier"]
        try:
            ssm.put_parameter(**kwargs)
            written += 1
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ParameterAlreadyExists":
                already += 1
                plan.skipped.append(
                    (m["old"], f"target {m['new']} exists (use --overwrite)")
                )
            else:
                raise
    return written, already


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True, help="old layout root, e.g. arc")
    parser.add_argument(
        "--target-prefix",
        default=None,
        help="new layout root (default: same as --prefix)",
    )
    parser.add_argument(
        "--envs",
        required=True,
        type=lambda s: [e.strip() for e in s.split(",") if e.strip()],
        help="comma-separated authoritative env names, e.g. qa,staging,production",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read and classify, print the plan, write nothing",
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="overwrite existing target parameters"
    )
    parser.add_argument("--endpoint-url", default=None)
    parser.add_argument("--region", default=None)
    args = parser.parse_args()
    args.target_prefix = args.target_prefix or args.prefix

    session = boto3.session.Session(region_name=args.region)
    ssm = CountingClient(
        session.client(
            "ssm",
            endpoint_url=args.endpoint_url,
            config=Config(retries={"mode": "adaptive", "max_attempts": 10}),
        )
    )

    plan = build_plan(ssm, args)

    # ---- Report the plan ----
    print(
        f"{'DRY RUN — ' if args.dry_run else ''}migration plan"
        f" (/{args.prefix.strip('/')} -> /{args.target_prefix.strip('/')}):\n"
    )

    for m in plan.migrations:
        print(f"  PUT  {m['new']}")
        print(f"       from {m['old']}")
        details = [f"type={m['Type']}", digest(m["Value"])]
        if m["KeyId"]:
            details.append(f"kms={m['KeyId']}")
        if m["Tier"] and m["Tier"] != "Standard":
            details.append(f"tier={m['Tier']}")
        print(f"       {' '.join(details)}")

    if plan.skipped:
        print("\nskipped:")
        for name, reason in plan.skipped:
            print(f"  SKIP {name}\n       {reason}")

    if plan.warnings:
        print("\nwarnings:")
        for warning in plan.warnings:
            print(f"  WARN {warning}")

    # ---- Execute ----
    written = already = 0
    if not args.dry_run and plan.migrations:
        written, already = execute(ssm, plan, args)

    # ---- Summary ----
    print("\nsummary:")
    print(f"  parameters found:      {len(plan.migrations) + len(plan.skipped)}")
    print(f"  planned migrations:    {len(plan.migrations)}")
    print(f"  skipped:               {len(plan.skipped)}")
    if not args.dry_run:
        print(f"  written:               {written}")
        print(f"  target already exists: {already}")

    print("\nAPI requests made:")
    for api, count in sorted(ssm.calls.items()):
        print(f"  {api}: {count}")
    if args.dry_run:
        expected = len(plan.migrations)
        print(f"  (execution would add put_parameter: {expected})")

    if plan.skipped and not args.dry_run:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
