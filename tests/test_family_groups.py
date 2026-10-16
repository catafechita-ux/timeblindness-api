"""End-to-end backend tests for Family Tasks Group-based API."""
import pytest
import requests
from conftest import BASE_URL, _auth, _register


# ---------- Legacy / health ----------
class TestLegacy:
    def test_root(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/")
        assert r.status_code == 200
        assert r.json().get("message") == "Hello World"

    def test_status_endpoints_removed(self, api_client):
        # Legacy open /api/status endpoints were removed (no unauthenticated DB writes/reads).
        assert api_client.post(f"{BASE_URL}/api/status", json={"client_name": "x"}).status_code in (404, 405)
        assert api_client.get(f"{BASE_URL}/api/status").status_code in (404, 405)


# ---------- Auth ----------
class TestAuth:
    def test_register(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/family/register", json={"name": "TEST_AuthUser"})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["name"] == "TEST_AuthUser"
        assert j["user_id"] and j["token"]

    def test_register_empty_name(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/family/register", json={"name": "   "})
        assert r.status_code == 400

    def test_me_no_token(self, api_client):
        r = requests.get(f"{BASE_URL}/api/family/me")
        assert r.status_code == 401

    def test_me_invalid_token(self, api_client):
        r = requests.get(f"{BASE_URL}/api/family/me", headers=_auth("invalid_token_xyz"))
        assert r.status_code == 401

    def test_me_and_update(self, api_client):
        u = _register(api_client, "TEST_Rename")
        r = requests.get(f"{BASE_URL}/api/family/me", headers=_auth(u["token"]))
        assert r.status_code == 200
        assert r.json()["name"] == "TEST_Rename"

        r2 = requests.patch(
            f"{BASE_URL}/api/family/me",
            json={"name": "TEST_Renamed"},
            headers=_auth(u["token"]),
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["name"] == "TEST_Renamed"

        # Verify persisted
        r3 = requests.get(f"{BASE_URL}/api/family/me", headers=_auth(u["token"]))
        assert r3.json()["name"] == "TEST_Renamed"


# ---------- Group CRUD ----------
class TestGroupCRUD:
    def test_create_group_solo(self, api_client, three_users):
        parent = three_users["parent"]
        r = requests.post(
            f"{BASE_URL}/api/family/group",
            json={"name": "TEST_Solo"},
            headers=_auth(parent["token"]),
        )
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["name"] == "TEST_Solo"
        assert len(j["members"]) == 1
        assert j["members"][0]["is_me"] is True
        assert j["members"][0]["user_id"] == parent["user_id"]

    def test_list_groups(self, api_client, three_users):
        parent = three_users["parent"]
        r = requests.get(f"{BASE_URL}/api/family/groups", headers=_auth(parent["token"]))
        assert r.status_code == 200
        groups = r.json()
        assert isinstance(groups, list)
        assert any(g["name"] == "TEST_Solo" for g in groups)

    def test_rename_group(self, api_client, three_users):
        parent = three_users["parent"]
        r = requests.post(
            f"{BASE_URL}/api/family/group",
            json={"name": "TEST_Rename_G"},
            headers=_auth(parent["token"]),
        )
        gid = r.json()["group_id"]
        r2 = requests.patch(
            f"{BASE_URL}/api/family/group/{gid}",
            json={"name": "TEST_Renamed_G"},
            headers=_auth(parent["token"]),
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["name"] == "TEST_Renamed_G"

    def test_rename_group_non_member_403(self, api_client, three_users):
        parent = three_users["parent"]
        kid2 = three_users["kid2"]
        r = requests.post(
            f"{BASE_URL}/api/family/group",
            json={"name": "TEST_Private"},
            headers=_auth(parent["token"]),
        )
        gid = r.json()["group_id"]
        r2 = requests.patch(
            f"{BASE_URL}/api/family/group/{gid}",
            json={"name": "haha"},
            headers=_auth(kid2["token"]),
        )
        assert r2.status_code == 403

    def test_leave_group_last_member_deletes(self, api_client):
        u = _register(api_client, "TEST_Solo_Leaver")
        r = requests.post(
            f"{BASE_URL}/api/family/group",
            json={"name": "TEST_Doomed"},
            headers=_auth(u["token"]),
        )
        gid = r.json()["group_id"]
        # Add a task
        rt = requests.post(
            f"{BASE_URL}/api/family/tasks/{gid}",
            json={"text": "TEST_doomed_task"},
            headers=_auth(u["token"]),
        )
        assert rt.status_code == 200
        # Create an invite
        ri = requests.post(
            f"{BASE_URL}/api/family/group/{gid}/invite",
            headers=_auth(u["token"]),
        )
        assert ri.status_code == 200
        # Leave -> cascade delete
        rl = requests.delete(
            f"{BASE_URL}/api/family/group/{gid}",
            headers=_auth(u["token"]),
        )
        assert rl.status_code == 200, rl.text
        assert rl.json().get("deleted") is True
        # Group listing should not include it
        rlst = requests.get(f"{BASE_URL}/api/family/groups", headers=_auth(u["token"]))
        assert not any(g["group_id"] == gid for g in rlst.json())


# ---------- Invites & 3-member flow ----------
class TestInvitesAndMembers:
    def test_invite_non_member_403(self, api_client, three_users):
        parent = three_users["parent"]
        kid1 = three_users["kid1"]
        r = requests.post(
            f"{BASE_URL}/api/family/group",
            json={"name": "TEST_InviteScope"},
            headers=_auth(parent["token"]),
        )
        gid = r.json()["group_id"]
        r2 = requests.post(
            f"{BASE_URL}/api/family/group/{gid}/invite",
            headers=_auth(kid1["token"]),
        )
        assert r2.status_code == 403

    def test_accept_invite_bad_code(self, api_client, three_users):
        parent = three_users["parent"]
        r = requests.post(
            f"{BASE_URL}/api/family/accept-invite",
            json={"code": "ZZZ-ZZZ"},
            headers=_auth(parent["token"]),
        )
        assert r.status_code == 404

    def test_three_member_group_flow(self, api_client, three_users):
        """Parent creates group, invites Kid1, then Kid2 with fresh code."""
        parent = three_users["parent"]
        kid1 = three_users["kid1"]
        kid2 = three_users["kid2"]

        # 1. Parent creates a group
        r = requests.post(
            f"{BASE_URL}/api/family/group",
            json={"name": "TEST_Family"},
            headers=_auth(parent["token"]),
        )
        assert r.status_code == 200, r.text
        gid = r.json()["group_id"]

        # 2. Parent invites Kid1
        ri1 = requests.post(
            f"{BASE_URL}/api/family/group/{gid}/invite",
            headers=_auth(parent["token"]),
        )
        assert ri1.status_code == 200
        code1 = ri1.json()["code"]

        # 3. Kid1 accepts
        ra1 = requests.post(
            f"{BASE_URL}/api/family/accept-invite",
            json={"code": code1},
            headers=_auth(kid1["token"]),
        )
        assert ra1.status_code == 200, ra1.text
        j1 = ra1.json()
        assert j1["already_member"] is False
        assert len(j1["members"]) == 2

        # 4. Used code cannot be reused
        ra1_again = requests.post(
            f"{BASE_URL}/api/family/accept-invite",
            json={"code": code1},
            headers=_auth(kid2["token"]),
        )
        assert ra1_again.status_code == 400

        # 5. Parent generates NEW code, Kid2 accepts -> 3 members
        ri2 = requests.post(
            f"{BASE_URL}/api/family/group/{gid}/invite",
            headers=_auth(parent["token"]),
        )
        assert ri2.status_code == 200
        code2 = ri2.json()["code"]
        assert code2 != code1

        ra2 = requests.post(
            f"{BASE_URL}/api/family/accept-invite",
            json={"code": code2},
            headers=_auth(kid2["token"]),
        )
        assert ra2.status_code == 200, ra2.text
        j2 = ra2.json()
        assert j2["already_member"] is False
        assert len(j2["members"]) == 3

        # 6. All 3 users see the group in /groups
        for user in (parent, kid1, kid2):
            rg = requests.get(f"{BASE_URL}/api/family/groups", headers=_auth(user["token"]))
            assert rg.status_code == 200
            found = [g for g in rg.json() if g["group_id"] == gid]
            assert found, f"User {user['name']} does not see group"
            assert len(found[0]["members"]) == 3
            # is_me flag correct
            me = [m for m in found[0]["members"] if m["is_me"]]
            assert len(me) == 1
            assert me[0]["user_id"] == user["user_id"]

        # 7. Self-accept an unused new code returns already_member=true
        ri3 = requests.post(
            f"{BASE_URL}/api/family/group/{gid}/invite",
            headers=_auth(parent["token"]),
        )
        code3 = ri3.json()["code"]
        ra3 = requests.post(
            f"{BASE_URL}/api/family/accept-invite",
            json={"code": code3},
            headers=_auth(parent["token"]),
        )
        assert ra3.status_code == 200
        assert ra3.json()["already_member"] is True
        # Members still 3
        assert len(ra3.json()["members"]) == 3

        # store for downstream tests
        pytest.family_gid = gid


# ---------- Tasks: cross-user sync ----------
class TestTasksCrossUserSync:
    def _gid(self):
        gid = getattr(pytest, "family_gid", None)
        if not gid:
            pytest.skip("group not created in prior test")
        return gid

    def test_create_task_visible_to_all(self, three_users):
        gid = self._gid()
        parent = three_users["parent"]
        kid1 = three_users["kid1"]
        kid2 = three_users["kid2"]

        rc = requests.post(
            f"{BASE_URL}/api/family/tasks/{gid}",
            json={"text": "TEST_task_by_parent"},
            headers=_auth(parent["token"]),
        )
        assert rc.status_code == 200, rc.text
        task = rc.json()
        pytest.family_task_id = task["id"]

        # Both kids should see it
        for user in (kid1, kid2):
            rl = requests.get(
                f"{BASE_URL}/api/family/tasks/{gid}",
                headers=_auth(user["token"]),
            )
            assert rl.status_code == 200
            ids = [t["id"] for t in rl.json()]
            assert task["id"] in ids, f"{user['name']} does not see task"

    def test_update_task_by_kid1_visible_to_all(self, three_users):
        gid = self._gid()
        parent = three_users["parent"]
        kid1 = three_users["kid1"]
        kid2 = three_users["kid2"]
        tid = pytest.family_task_id

        ru = requests.patch(
            f"{BASE_URL}/api/family/tasks/{gid}/{tid}",
            json={"checked": True, "text": "TEST_task_updated"},
            headers=_auth(kid1["token"]),
        )
        assert ru.status_code == 200, ru.text
        assert ru.json()["checked"] is True
        assert ru.json()["text"] == "TEST_task_updated"

        for user in (parent, kid2):
            rl = requests.get(
                f"{BASE_URL}/api/family/tasks/{gid}",
                headers=_auth(user["token"]),
            )
            t = next((x for x in rl.json() if x["id"] == tid), None)
            assert t is not None
            assert t["checked"] is True
            assert t["text"] == "TEST_task_updated"

    def test_delete_task_by_kid2_removes_for_all(self, three_users):
        gid = self._gid()
        parent = three_users["parent"]
        kid1 = three_users["kid1"]
        kid2 = three_users["kid2"]
        tid = pytest.family_task_id

        rd = requests.delete(
            f"{BASE_URL}/api/family/tasks/{gid}/{tid}",
            headers=_auth(kid2["token"]),
        )
        assert rd.status_code == 200

        for user in (parent, kid1, kid2):
            rl = requests.get(
                f"{BASE_URL}/api/family/tasks/{gid}",
                headers=_auth(user["token"]),
            )
            ids = [t["id"] for t in rl.json()]
            assert tid not in ids

    def test_tasks_non_member_403(self, api_client, three_users):
        gid = self._gid()
        stranger = _register(api_client, "TEST_Stranger")
        r = requests.get(
            f"{BASE_URL}/api/family/tasks/{gid}",
            headers=_auth(stranger["token"]),
        )
        assert r.status_code == 403
        r2 = requests.post(
            f"{BASE_URL}/api/family/tasks/{gid}",
            json={"text": "TEST_hack"},
            headers=_auth(stranger["token"]),
        )
        assert r2.status_code == 403

    def test_tasks_no_token_401(self):
        gid = self._gid()
        r = requests.get(f"{BASE_URL}/api/family/tasks/{gid}")
        assert r.status_code == 401


# ---------- Multiple groups per user ----------
class TestMultipleGroups:
    def test_user_can_have_multiple_independent_groups(self, api_client, three_users):
        parent = three_users["parent"]
        kid1 = three_users["kid1"]

        # Parent creates a second group with just kid1
        r1 = requests.post(
            f"{BASE_URL}/api/family/group",
            json={"name": "TEST_GroupA"},
            headers=_auth(parent["token"]),
        )
        gA = r1.json()["group_id"]
        r2 = requests.post(
            f"{BASE_URL}/api/family/group",
            json={"name": "TEST_GroupB"},
            headers=_auth(parent["token"]),
        )
        gB = r2.json()["group_id"]

        # Invite kid1 to GroupA only
        ri = requests.post(
            f"{BASE_URL}/api/family/group/{gA}/invite",
            headers=_auth(parent["token"]),
        )
        code = ri.json()["code"]
        requests.post(
            f"{BASE_URL}/api/family/accept-invite",
            json={"code": code},
            headers=_auth(kid1["token"]),
        )

        # Kid1 sees GroupA but not GroupB
        rl = requests.get(f"{BASE_URL}/api/family/groups", headers=_auth(kid1["token"]))
        ids = [g["group_id"] for g in rl.json()]
        assert gA in ids
        assert gB not in ids

        # Kid1 cannot read tasks for gB
        rt = requests.get(
            f"{BASE_URL}/api/family/tasks/{gB}",
            headers=_auth(kid1["token"]),
        )
        assert rt.status_code == 403

        # Tasks in gA are independent of gB
        requests.post(
            f"{BASE_URL}/api/family/tasks/{gA}",
            json={"text": "TEST_taskA"},
            headers=_auth(parent["token"]),
        )
        requests.post(
            f"{BASE_URL}/api/family/tasks/{gB}",
            json={"text": "TEST_taskB"},
            headers=_auth(parent["token"]),
        )
        rgA = requests.get(f"{BASE_URL}/api/family/tasks/{gA}", headers=_auth(parent["token"]))
        rgB = requests.get(f"{BASE_URL}/api/family/tasks/{gB}", headers=_auth(parent["token"]))
        textsA = [t["text"] for t in rgA.json()]
        textsB = [t["text"] for t in rgB.json()]
        assert "TEST_taskA" in textsA and "TEST_taskB" not in textsA
        assert "TEST_taskB" in textsB and "TEST_taskA" not in textsB
