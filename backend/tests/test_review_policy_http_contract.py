"""HTTP transport and route policy rejection layers, including bundled atomicity."""
import copy

import pytest

from app.models.user import User
from app.models.workspace import Workspace

pytestmark = pytest.mark.asyncio
DEFAULT = {'ingest': 'needs_review', 'manual': 'reviewed'}
CHANGED = {'ingest': 'reviewed', 'manual': 'reviewed'}


@pytest.mark.parametrize(('extra', 'status'), [
    *[({'expected_review_policy': value}, 422) for value in ('bad', [], 7, True)],
    ({}, 409),
    *[({'expected_review_policy': value}, 409) for value in (
        None, {}, {'ingest': 'needs_review'}, {'ingest': 'invalid', 'manual': 'reviewed'},
        {'ingest': 'needs_review', 'manual': 'reviewed', 'extra': 'reviewed'}, CHANGED,
        {'ingest': ['needs_review'], 'manual': 'reviewed'},
        {'ingest': {'value': 'needs_review'}, 'manual': 'reviewed'},
    )],
    *[({'expected_review_policy': DEFAULT, 'review_policy': value}, 400) for value in (
        {'ingest': 'invalid'}, {'unknown': 'reviewed'},
        {'ingest': ['reviewed']}, {'ingest': {'value': 'reviewed'}},
    )],
    *[({'expected_review_policy': DEFAULT, 'review_policy': value}, 422) for value in ('bad', [], 7, True)],
])
async def test_actual_http_validation_layers_leave_every_bundled_field_unchanged(
    client, auth_headers, session, test_workspace, test_user, extra, status,
):
    ws_id, user_id = test_workspace.id, test_user.id
    def settings(row):
        return {key: copy.deepcopy(getattr(row, key)) for key in (
            'name', 'default_currency', 'locale', 'timezone', 'tax_jurisdiction', 'icon', 'color', 'review_policy')}
    before = settings(test_workspace)
    preferences = copy.deepcopy(test_user.preferences)
    response = await client.patch(f'/api/workspaces/{ws_id}', headers=auth_headers, json={
        'name': 'Must not persist', 'default_currency': 'EUR', 'locale': 'en', 'timezone': 'UTC',
        'tax_jurisdiction': 'BR', 'icon': 'star', 'color': '#000000', 'review_policy': CHANGED, **extra,
    })
    assert response.status_code == status, response.text
    session.expire_all()
    assert settings(await session.get(Workspace, ws_id)) == before
    assert (await session.get(User, user_id)).preferences == preferences


async def test_policy_absent_old_http_client_needs_no_expectation(client, auth_headers, test_workspace):
    response = await client.patch(f'/api/workspaces/{test_workspace.id}', headers=auth_headers,
                                  json={'name': 'Old client still works'})
    assert response.status_code == 200, response.text
    assert response.json()['name'] == 'Old client still works'
    assert response.json()['review_policy'] is None
