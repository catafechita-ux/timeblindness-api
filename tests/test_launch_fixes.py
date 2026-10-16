import os, requests
B = os.environ["EXPO_BACKEND_URL"].rstrip("/")
H = lambda t: {"Authorization": f"Bearer {t}"}

def reg(name):
    r = requests.post(f"{B}/api/family/register", json={"name": name}); assert r.status_code == 200, r.text
    return r.json()

def test_status_removed():
    assert requests.get(f"{B}/api/status").status_code in (404, 405)
    assert requests.post(f"{B}/api/status", json={"client_name": "x"}).status_code in (404, 405)

def test_health():
    assert requests.get(f"{B}/api/health").json() == {"ok": True}

def test_task_text_limit():
    a = reg("LenA"); g = requests.post(f"{B}/api/family/group", json={}, headers=H(a["token"])).json()
    ok = requests.post(f"{B}/api/family/tasks/{g['group_id']}", json={"text": "x"*500}, headers=H(a["token"]))
    assert ok.status_code == 200
    bad = requests.post(f"{B}/api/family/tasks/{g['group_id']}", json={"text": "x"*501}, headers=H(a["token"]))
    assert bad.status_code == 422
    bad2 = requests.patch(f"{B}/api/family/tasks/{g['group_id']}/{ok.json()['id']}", json={"text": "y"*501}, headers=H(a["token"]))
    assert bad2.status_code == 422

def test_invite_code_format():
    a = reg("CodeA"); g = requests.post(f"{B}/api/family/group", json={}, headers=H(a["token"])).json()
    c = requests.post(f"{B}/api/family/group/{g['group_id']}/invite", headers=H(a["token"])).json()["code"]
    assert len(c) == 7 and c[3] == "-" and not set(c) & set("0O1IL")

def test_delete_account():
    a, b = reg("DelA"), reg("DelB")
    solo = requests.post(f"{B}/api/family/group", json={"name": "solo"}, headers=H(a["token"])).json()
    shared = requests.post(f"{B}/api/family/group", json={"name": "shared"}, headers=H(b["token"])).json()
    code = requests.post(f"{B}/api/family/group/{shared['group_id']}/invite", headers=H(b["token"])).json()["code"]
    assert requests.post(f"{B}/api/family/accept-invite", json={"code": code}, headers=H(a["token"])).status_code == 200
    requests.post(f"{B}/api/family/tasks/{solo['group_id']}", json={"text": "a solo"}, headers=H(a["token"]))
    requests.post(f"{B}/api/family/tasks/{shared['group_id']}", json={"text": "a shared"}, headers=H(a["token"]))
    requests.post(f"{B}/api/family/tasks/{shared['group_id']}", json={"text": "b shared"}, headers=H(b["token"]))
    requests.post(f"{B}/api/family/group/{shared['group_id']}/invite", headers=H(a["token"]))
    r = requests.delete(f"{B}/api/family/me", headers=H(a["token"]))
    assert r.status_code == 200, r.text
    j = r.json(); assert j["groups_deleted"] == 1 and j["groups_left"] == 1 and j["tasks_deleted"] >= 1
    assert requests.get(f"{B}/api/family/me", headers=H(a["token"])).status_code == 401
    gs = requests.get(f"{B}/api/family/groups", headers=H(b["token"])).json()
    sg = [g for g in gs if g["group_id"] == shared["group_id"]][0]
    assert [m["name"] for m in sg["members"]] == ["DelB"]
    ts = requests.get(f"{B}/api/family/tasks/{shared['group_id']}", headers=H(b["token"])).json()
    assert [t["text"] for t in ts] == ["b shared"]
    # solo group gone: b is not a member, so 404 means deleted
    assert requests.get(f"{B}/api/family/tasks/{solo['group_id']}", headers=H(b["token"])).status_code == 404

def test_join_rate_limit():
    a = reg("RateA")
    codes = [requests.post(f"{B}/api/family/accept-invite", json={"code": "ZZZ-ZZZ"}, headers=H(a["token"])).status_code for _ in range(12)]
    assert codes[:10] == [404]*10 and codes[-1] == 429
