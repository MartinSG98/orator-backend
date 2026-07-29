"""Central place to build boto3 clients.

Credentials are never handled by this app. They resolve through the standard
boto3 chain: in a deployed environment that is the IAM role attached to the
compute, locally it is the default profile, or the profile named in
ORATOR_AWS_PROFILE when the machine has more than one account configured.
"""

from typing import Any

import boto3

from app.config import get_settings


def client(service: str) -> Any:
    settings = get_settings()
    session = boto3.session.Session(
        profile_name=settings.aws_profile or None,
        region_name=settings.aws_region,
    )
    return session.client(service)
