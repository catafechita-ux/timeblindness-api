from fastapi import FastAPI, APIRouter, HTTPException, Header, Depends
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import random
import string
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
from datetime import datetime, timedelta


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app without a prefix
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")


# ============================================================
# Legacy status check (kept for template compatibility)
# ============================================================
class StatusCheck(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class StatusCheckCreate(BaseModel):
    client_name: str


@api_router.get("/")
async def root():
    return {"message": "Hello World"}


@api_router.get("/health")
async def health():
    # Cheap liveness check for Render / keep-awake pinger (no DB access).
    return {"ok": True}


@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_dict = input.dict()
    status_obj = StatusCheck(**status_dict)
    _ = await db.status_checks.insert_one(status_obj.dict())
    return status_obj


@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    status_checks = await db.status_checks.find().to_list(1000)
    return [StatusCheck(**status_check) for status_check in status_checks]


# ============================================================
# Family Tasks feature (Groups model)
# ============================================================
# Data model:
#   family_users:  { user_id, token, name, created_at }
#   family_groups: { group_id, name, member_ids: [user_id, ...], created_by, created_at }
#   family_invites:{ code, group_id, created_by, created_at, expires_at, used }
#   family_tasks:  { id, group_id, text, checked, created_by, created_at, updated_at }
# ============================================================

# --------- Models ---------
class RegisterRequest(BaseModel):
    name: str


class RegisterResponse(BaseModel):
    user_id: str
    token: str
    name: str


class UpdateNameRequest(BaseModel):
    name: str


class CreateGroupRequest(BaseModel):
    name: Optional[str] = None


class GroupMember(BaseModel):
    user_id: str
    name: str
    is_me: bool = False


class GroupOut(BaseModel):
    group_id: str
    name: str
    members: List[GroupMember]
    created_at: datetime


class RenameGroupRequest(BaseModel):
    name: str


class InviteResponse(BaseModel):
    code: str
    expires_at: datetime
    group_id: str


class AcceptInviteRequest(BaseModel):
    code: str


class AcceptInviteResponse(BaseModel):
    group_id: str
    group_name: str
    members: List[GroupMember]
    already_member: bool


class TaskOut(BaseModel):
    id: str
    group_id: str
    text: str
    checked: bool
    created_by: str
    created_at: datetime
    updated_at: datetime


class TaskCreate(BaseModel):
    text: str = ""


class TaskUpdate(BaseModel):
    text: Optional[str] = None
    checked: Optional[bool] = None


# --------- Helpers ---------
def _generate_invite_code() -> str:
    """Return a friendly short code like 'AB7-K3X'."""
    alphabet = string.ascii_uppercase + string.digits
    alphabet = "".join(c for c in alphabet if c not in "0O1IL")
    part1 = "".join(random.choices(alphabet, k=3))
    part2 = "".join(random.choices(alphabet, k=3))
    return f"{part1}-{part2}"


async def _get_current_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization[len("Bearer "):].strip()
    user = await db.family_users.find_one({"token": token})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


async def _get_members(group: dict, me_user_id: str) -> List[GroupMember]:
    """Return members list with names, marking `is_me`."""
    member_ids = group.get("member_ids") or []
    if not member_ids:
        # Legacy fallback
        u1 = group.get("user1_id")
        u2 = group.get("user2_id")
        member_ids = [x for x in (u1, u2) if x]

    result: List[GroupMember] = []
    for uid in member_ids:
        u = await db.family_users.find_one({"user_id": uid})
        if not u:
            continue
        result.append(GroupMember(
            user_id=uid,
            name=u.get("name", "?"),
            is_me=(uid == me_user_id),
        ))
    return result


def _default_group_name(members: List[GroupMember]) -> str:
    """Auto-name a group based on member names (excluding me)."""
    others = [m.name for m in members if not m.is_me]
    if not others:
        return "My group"
    if len(others) == 1:
        return others[0]
    if len(others) == 2:
        return f"{others[0]} & {others[1]}"
    return f"{others[0]}, {others[1]} +{len(others) - 2}"


async def _ensure_member(group_id: str, user_id: str) -> dict:
    group = await db.family_groups.find_one({"group_id": group_id})
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    if user_id not in (group.get("member_ids") or []):
        raise HTTPException(status_code=403, detail="Not a member of this group")
    return group


# --------- Startup migration ---------
@app.on_event("startup")
async def _migrate_connections_to_groups():
    """Migrate legacy family_connections docs to family_groups."""
    try:
        cur = db.family_connections.find({})
        async for conn in cur:
            gid = conn.get("connection_id") or conn.get("group_id") or str(uuid.uuid4())
            member_ids = conn.get("member_ids")
            if not member_ids:
                member_ids = []
                if conn.get("user1_id"):
                    member_ids.append(conn["user1_id"])
                if conn.get("user2_id"):
                    member_ids.append(conn["user2_id"])

            existing = await db.family_groups.find_one({"group_id": gid})
            if existing:
                continue
            await db.family_groups.insert_one({
                "group_id": gid,
                "name": conn.get("name") or "",
                "member_ids": member_ids,
                "created_by": conn.get("created_by") or (member_ids[0] if member_ids else None),
                "created_at": conn.get("created_at") or datetime.utcnow(),
            })

        # Migrate task connection_id → group_id if needed
        cur2 = db.family_tasks.find({"connection_id": {"$exists": True}, "group_id": {"$exists": False}})
        async for t in cur2:
            await db.family_tasks.update_one(
                {"_id": t["_id"]},
                {"$set": {"group_id": t["connection_id"]}}
            )
    except Exception as e:
        logging.warning(f"Migration warning: {e}")


# --------- User endpoints ---------

@api_router.post("/family/register", response_model=RegisterResponse)
async def family_register(payload: RegisterRequest):
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    if len(name) > 40:
        name = name[:40]

    user_id = str(uuid.uuid4())
    token = uuid.uuid4().hex + uuid.uuid4().hex
    doc = {
        "user_id": user_id,
        "token": token,
        "name": name,
        "created_at": datetime.utcnow(),
    }
    await db.family_users.insert_one(doc)
    return RegisterResponse(user_id=user_id, token=token, name=name)


@api_router.get("/family/me")
async def family_me(current=Depends(_get_current_user)):
    return {"user_id": current["user_id"], "name": current["name"]}


@api_router.patch("/family/me")
async def family_update_me(
    payload: UpdateNameRequest,
    current=Depends(_get_current_user),
):
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    if len(name) > 40:
        name = name[:40]
    await db.family_users.update_one(
        {"user_id": current["user_id"]},
        {"$set": {"name": name}},
    )
    return {"user_id": current["user_id"], "name": name}


# --------- Group endpoints ---------

@api_router.post("/family/group", response_model=GroupOut)
async def family_create_group(
    payload: CreateGroupRequest,
    current=Depends(_get_current_user),
):
    """Create a new group with just the current user as first member."""
    group_id = str(uuid.uuid4())
    name = (payload.name or "").strip() if payload.name else ""
    if len(name) > 60:
        name = name[:60]
    doc = {
        "group_id": group_id,
        "name": name,
        "member_ids": [current["user_id"]],
        "created_by": current["user_id"],
        "created_at": datetime.utcnow(),
    }
    await db.family_groups.insert_one(doc)
    members = await _get_members(doc, current["user_id"])
    return GroupOut(
        group_id=group_id,
        name=name or _default_group_name(members),
        members=members,
        created_at=doc["created_at"],
    )


@api_router.get("/family/groups", response_model=List[GroupOut])
async def family_list_groups(current=Depends(_get_current_user)):
    cur = db.family_groups.find({"member_ids": current["user_id"]})
    result: List[GroupOut] = []
    async for g in cur:
        members = await _get_members(g, current["user_id"])
        result.append(GroupOut(
            group_id=g["group_id"],
            name=g.get("name") or _default_group_name(members),
            members=members,
            created_at=g.get("created_at", datetime.utcnow()),
        ))
    result.sort(key=lambda x: x.created_at, reverse=True)
    return result


@api_router.patch("/family/group/{group_id}", response_model=GroupOut)
async def family_rename_group(
    group_id: str,
    payload: RenameGroupRequest,
    current=Depends(_get_current_user),
):
    group = await _ensure_member(group_id, current["user_id"])
    name = (payload.name or "").strip()
    if len(name) > 60:
        name = name[:60]
    await db.family_groups.update_one(
        {"group_id": group_id},
        {"$set": {"name": name}},
    )
    group["name"] = name
    members = await _get_members(group, current["user_id"])
    return GroupOut(
        group_id=group_id,
        name=name or _default_group_name(members),
        members=members,
        created_at=group["created_at"],
    )


@api_router.delete("/family/group/{group_id}")
async def family_leave_group(
    group_id: str,
    current=Depends(_get_current_user),
):
    """Leave a group. If last member, delete group + all tasks."""
    group = await _ensure_member(group_id, current["user_id"])
    remaining = [uid for uid in group.get("member_ids", []) if uid != current["user_id"]]
    if not remaining:
        await db.family_groups.delete_one({"group_id": group_id})
        await db.family_tasks.delete_many({"group_id": group_id})
        await db.family_invites.delete_many({"group_id": group_id})
        return {"ok": True, "deleted": True}
    await db.family_groups.update_one(
        {"group_id": group_id},
        {"$set": {"member_ids": remaining}},
    )
    return {"ok": True, "deleted": False, "remaining": len(remaining)}


# --------- Invitation endpoints ---------

@api_router.post("/family/group/{group_id}/invite", response_model=InviteResponse)
async def family_create_invite(
    group_id: str,
    current=Depends(_get_current_user),
):
    """Generate a fresh invitation code for THIS group, valid 30 minutes."""
    await _ensure_member(group_id, current["user_id"])
    for _ in range(5):
        code = _generate_invite_code()
        existing = await db.family_invites.find_one({"code": code, "used": False})
        if not existing:
            break
    expires = datetime.utcnow() + timedelta(minutes=30)
    doc = {
        "code": code,
        "group_id": group_id,
        "created_by": current["user_id"],
        "created_at": datetime.utcnow(),
        "expires_at": expires,
        "used": False,
    }
    await db.family_invites.insert_one(doc)
    return InviteResponse(code=code, expires_at=expires, group_id=group_id)


@api_router.post("/family/accept-invite", response_model=AcceptInviteResponse)
async def family_accept_invite(
    payload: AcceptInviteRequest,
    current=Depends(_get_current_user),
):
    code = (payload.code or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Code is required")

    invite = await db.family_invites.find_one({"code": code})
    if not invite:
        raise HTTPException(status_code=404, detail="Invalid code")
    if invite.get("used"):
        raise HTTPException(status_code=400, detail="Code already used")
    if invite["expires_at"] < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Code expired")

    group_id = invite["group_id"]
    group = await db.family_groups.find_one({"group_id": group_id})
    if not group:
        raise HTTPException(status_code=404, detail="Group no longer exists")

    already = current["user_id"] in group.get("member_ids", [])
    if not already:
        await db.family_groups.update_one(
            {"group_id": group_id},
            {"$addToSet": {"member_ids": current["user_id"]}},
        )
    await db.family_invites.update_one({"code": code}, {"$set": {"used": True}})

    group = await db.family_groups.find_one({"group_id": group_id})
    members = await _get_members(group, current["user_id"])
    return AcceptInviteResponse(
        group_id=group_id,
        group_name=group.get("name") or _default_group_name(members),
        members=members,
        already_member=already,
    )


# --------- Task endpoints ---------

@api_router.get("/family/tasks/{group_id}", response_model=List[TaskOut])
async def family_list_tasks(
    group_id: str,
    current=Depends(_get_current_user),
):
    await _ensure_member(group_id, current["user_id"])
    cur = db.family_tasks.find({"group_id": group_id})
    results: List[TaskOut] = []
    async for t in cur:
        results.append(TaskOut(
            id=t["id"],
            group_id=t.get("group_id", group_id),
            text=t.get("text", ""),
            checked=t.get("checked", False),
            created_by=t.get("created_by", ""),
            created_at=t["created_at"],
            updated_at=t.get("updated_at", t["created_at"]),
        ))
    results.sort(key=lambda t: t.created_at)
    return results


@api_router.post("/family/tasks/{group_id}", response_model=TaskOut)
async def family_create_task(
    group_id: str,
    payload: TaskCreate,
    current=Depends(_get_current_user),
):
    await _ensure_member(group_id, current["user_id"])
    now = datetime.utcnow()
    task = {
        "id": str(uuid.uuid4()),
        "group_id": group_id,
        "text": payload.text or "",
        "checked": False,
        "created_by": current["user_id"],
        "created_at": now,
        "updated_at": now,
    }
    await db.family_tasks.insert_one(task)
    return TaskOut(**task)


@api_router.patch("/family/tasks/{group_id}/{task_id}", response_model=TaskOut)
async def family_update_task(
    group_id: str,
    task_id: str,
    payload: TaskUpdate,
    current=Depends(_get_current_user),
):
    await _ensure_member(group_id, current["user_id"])
    task = await db.family_tasks.find_one({"id": task_id, "group_id": group_id})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    update = {"updated_at": datetime.utcnow()}
    if payload.text is not None:
        update["text"] = payload.text
    if payload.checked is not None:
        update["checked"] = payload.checked
    await db.family_tasks.update_one({"id": task_id}, {"$set": update})
    task.update(update)
    return TaskOut(
        id=task["id"],
        group_id=task["group_id"],
        text=task.get("text", ""),
        checked=task.get("checked", False),
        created_by=task.get("created_by", ""),
        created_at=task["created_at"],
        updated_at=task["updated_at"],
    )


@api_router.delete("/family/tasks/{group_id}/{task_id}")
async def family_delete_task(
    group_id: str,
    task_id: str,
    current=Depends(_get_current_user),
):
    await _ensure_member(group_id, current["user_id"])
    result = await db.family_tasks.delete_one({
        "id": task_id, "group_id": group_id,
    })
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True}


# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
