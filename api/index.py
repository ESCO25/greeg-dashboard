from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient, ASCENDING
from bson import ObjectId
from bson.errors import InvalidId
from datetime import datetime, timezone
import os
import hashlib
import secrets
import hmac

app = Flask(__name__, static_folder=None)
CORS(app)

MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://aabohasn97_db_user:Wu0dwnqjDa8V7LG6@greg.lk9lpsg.mongodb.net/greeg?appName=GREG")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "admin123")
GUILD_ID = os.environ.get("GUILD_ID", "1003257192104874004")

_client = None
db = None

def get_db():
    global _client, db
    if _client is None:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        db = _client["greeg"]
    return db

def require_auth(f):
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "Unauthorized"}), 401
        token = auth[7:]
        expected = hashlib.sha256(DASHBOARD_PASSWORD.encode()).hexdigest()
        if not hmac.compare_digest(token, expected):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

@app.route("/")
def index():
    return jsonify({"status": "ok", "name": "Greeg Bot Dashboard API"})

@app.route("/api/auth", methods=["POST"])
def auth():
    data = request.get_json() or {}
    if data.get("password") == DASHBOARD_PASSWORD:
        token = hashlib.sha256(DASHBOARD_PASSWORD.encode()).hexdigest()
        return jsonify({"token": token, "guild_id": GUILD_ID})
    return jsonify({"error": "Invalid password"}), 401

@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})

# ── Discord data synced by bot ──────────────────────────────────

@app.route("/api/roles-list")
@require_auth
def get_roles_list():
    d = get_db()
    roles = list(d["discord_roles"].find({}, {"_id": 0}).sort("position", -1))
    return jsonify({"roles": roles})

@app.route("/api/channels")
@require_auth
def get_channels():
    d = get_db()
    channels = list(d["discord_channels"].find({}, {"_id": 0}))
    return jsonify({"channels": channels})

# ── Stats ───────────────────────────────────────────────────────

@app.route("/api/stats")
@require_auth
def stats():
    d = get_db()
    vs = d["verify_stats"].find_one({"guild_id": GUILD_ID}) or {}
    members_count = d["members"].count_documents({"guild_id": GUILD_ID})
    bans_count = d["bans"].count_documents({"guild_id": GUILD_ID})
    pending_queue = d["verification_queue"].count_documents({})
    tickets = list(d["tickets"].find({}, {"_id": 0}))
    auto_replies = d["auto_replies"].count_documents({"guild_id": GUILD_ID})
    return jsonify({
        "requested": vs.get("requested", 0),
        "approved": vs.get("approved", 0),
        "rejected": vs.get("rejected", 0),
        "verified_members": members_count,
        "bans": bans_count,
        "pending_queue": pending_queue,
        "open_tickets": sum(1 for t in tickets if t.get("status") == "open"),
        "closed_tickets": sum(1 for t in tickets if t.get("status") == "closed"),
        "auto_replies": auto_replies,
    })

# ── Verify Settings ─────────────────────────────────────────────

@app.route("/api/verify-settings", methods=["GET"])
@require_auth
def get_verify_settings():
    d = get_db()
    s = d["verify_settings"].find_one({"guild_id": GUILD_ID}) or {}
    return jsonify({
        "verify_channel": s.get("verify_channel", 1493262714695450814),
        "unverified_role": s.get("unverified_role", 1497372312041951274),
        "verified_role": s.get("verified_role", 1344359636127584289),
        "log_channel": s.get("log_channel", ""),
        "token_expire_min": s.get("token_expire_min", 15),
    })

@app.route("/api/verify-settings", methods=["POST"])
@require_auth
def update_verify_settings():
    d = get_db()
    data = request.get_json() or {}
    update = {}
    for key in ["verify_channel", "unverified_role", "verified_role", "log_channel", "token_expire_min"]:
        if key in data:
            update[key] = data[key]
    if update:
        d["verify_settings"].update_one(
            {"guild_id": GUILD_ID},
            {"$set": update},
            upsert=True
        )
    return jsonify({"success": True, "updated": update})

# ── Bans ────────────────────────────────────────────────────────

@app.route("/api/bans")
@require_auth
def get_bans():
    d = get_db()
    limit = min(int(request.args.get("limit", 50)), 200)
    bans = list(d["bans"].find({"guild_id": GUILD_ID}).sort("banned_at", -1).limit(limit))
    result = []
    for b in bans:
        result.append({
            "id": str(b["_id"]),
            "type": b.get("type"),
            "value": b.get("value", "")[:40],
            "discord_id": b.get("discord_id"),
            "username": b.get("username", "Unknown"),
            "banned_at": str(b.get("banned_at", ""))[:19],
            "banned_by": b.get("banned_by", ""),
        })
    return jsonify({"bans": result, "count": d["bans"].count_documents({"guild_id": GUILD_ID})})

@app.route("/api/unban", methods=["POST"])
@require_auth
def unban():
    d = get_db()
    data = request.get_json() or {}
    discord_id = data.get("discord_id")
    if not discord_id:
        return jsonify({"error": "discord_id required"}), 400
    result = d["bans"].delete_many({"discord_id": str(discord_id), "guild_id": GUILD_ID})
    return jsonify({"success": True, "deleted": result.deleted_count})

# ── Queue ───────────────────────────────────────────────────────

@app.route("/api/queue", methods=["GET"])
@require_auth
def get_queue():
    d = get_db()
    items = list(d["verification_queue"].find().sort("timestamp", -1).limit(50))
    result = []
    for item in items:
        result.append({
            "id": str(item["_id"]),
            "discord_id": item.get("discord_id"),
            "action": item.get("action"),
            "timestamp": str(item.get("timestamp", ""))[:19],
        })
    return jsonify({"queue": result, "count": len(result)})

@app.route("/api/queue/clear", methods=["POST"])
@require_auth
def clear_queue():
    d = get_db()
    result = d["verification_queue"].delete_many({})
    return jsonify({"success": True, "deleted": result.deleted_count})

# ── Members ─────────────────────────────────────────────────────

@app.route("/api/members")
@require_auth
def get_members():
    d = get_db()
    limit = min(int(request.args.get("limit", 100)), 500)
    members = list(d["members"].find({"guild_id": GUILD_ID}).sort("verified_at", -1).limit(limit))
    result = []
    for m in members:
        result.append({
            "discord_id": m.get("discord_id"),
            "username": m.get("username", "Unknown"),
            "ip": m.get("ip", "")[:20],
            "device_id": m.get("device_id", "")[:20],
            "verified_at": str(m.get("verified_at", ""))[:19],
        })
    return jsonify({"members": result, "count": d["members"].count_documents({"guild_id": GUILD_ID})})

@app.route("/api/member-info")
@require_auth
def member_info():
    query = request.args.get("query", "").strip()
    if not query:
        return jsonify({"member": None})
    d = get_db()
    member = None
    if query.isdigit():
        member = d["members"].find_one({"discord_id": query, "guild_id": GUILD_ID})
    if not member:
        member = d["members"].find_one({"username": query, "guild_id": GUILD_ID})
    if not member:
        return jsonify({"member": None})
    banned = d["bans"].find_one({"discord_id": member["discord_id"], "guild_id": GUILD_ID}) is not None
    return jsonify({
        "member": {
            "discord_id": member.get("discord_id"),
            "username": member.get("username", query),
            "verified": True,
            "banned": banned,
            "roles": [],
        }
    })

# ── Auto Replies ────────────────────────────────────────────────

@app.route("/api/auto-replies", methods=["GET"])
@require_auth
def get_auto_replies():
    d = get_db()
    replies = list(d["auto_replies"].find({"guild_id": GUILD_ID}).sort("trigger", 1))
    result = []
    for r in replies:
        result.append({
            "id": str(r["_id"]),
            "trigger": r.get("trigger"),
            "response": r.get("response"),
            "channel_id": r.get("channel_id", ""),
            "match_type": r.get("match_type", "exact"),
            "enabled": r.get("enabled", True),
        })
    return jsonify({"auto_replies": result, "count": len(result)})

@app.route("/api/auto-replies", methods=["POST"])
@require_auth
def add_auto_reply():
    d = get_db()
    data = request.get_json() or {}
    trigger = data.get("trigger", "").strip()
    response = data.get("response", "").strip()
    if not trigger or not response:
        return jsonify({"error": "trigger and response required"}), 400
    doc = {
        "guild_id": GUILD_ID,
        "trigger": trigger,
        "response": response,
        "channel_id": data.get("channel_id", ""),
        "match_type": data.get("match_type", "exact"),
        "enabled": data.get("enabled", True),
        "created_at": datetime.now(timezone.utc),
    }
    reply_id = d["auto_replies"].insert_one(doc).inserted_id
    return jsonify({"success": True, "id": str(reply_id)})

@app.route("/api/auto-replies/<reply_id>", methods=["DELETE"])
@require_auth
def delete_auto_reply(reply_id):
    d = get_db()
    result = d["auto_replies"].delete_one({"_id": ObjectId(reply_id), "guild_id": GUILD_ID})
    return jsonify({"success": True, "deleted": result.deleted_count})

@app.route("/api/auto-replies/<reply_id>", methods=["PUT"])
@require_auth
def update_auto_reply(reply_id):
    d = get_db()
    data = request.get_json() or {}
    update = {}
    for key in ["trigger", "response", "channel_id", "match_type", "enabled"]:
        if key in data:
            update[key] = data[key]
    if update:
        d["auto_replies"].update_one(
            {"_id": ObjectId(reply_id), "guild_id": GUILD_ID},
            {"$set": update}
        )
    return jsonify({"success": True, "updated": update})

# ── Config ──────────────────────────────────────────────────────

@app.route("/api/config")
@require_auth
def get_config():
    d = get_db()
    cfg = d["bot_config"].find_one({"guild_id": GUILD_ID}) or {}
    return jsonify({
        "prefix": cfg.get("prefix", "!"),
        "bot_status": cfg.get("bot_status", "اشوفكم بصمة😶‍🌫️"),
        "welcome_enabled": cfg.get("welcome_enabled", True),
        "welcome_channel": cfg.get("welcome_channel", ""),
        "welcome_message": cfg.get("welcome_message", "🎉 مرحباً بك {member} في السيرفر!"),
        "log_channel": cfg.get("log_channel", ""),
    })

@app.route("/api/config", methods=["POST"])
@require_auth
def update_config():
    d = get_db()
    data = request.get_json() or {}
    update = {}
    for key in ["prefix", "bot_status", "welcome_enabled", "welcome_channel", "welcome_message", "log_channel"]:
        if key in data:
            update[key] = data[key]
    if update:
        d["bot_config"].update_one(
            {"guild_id": GUILD_ID},
            {"$set": update},
            upsert=True
        )
    return jsonify({"success": True, "updated": update})

# ── Tickets ─────────────────────────────────────────────────────

@app.route("/api/tickets", methods=["GET"])
@require_auth
def get_tickets():
    d = get_db()
    tickets = list(d["tickets"].find({}, {"_id": 0}).sort("created_at", -1).limit(50))
    return jsonify({
        "open": sum(1 for t in tickets if t.get("status") == "open"),
        "closed": sum(1 for t in tickets if t.get("status") == "closed"),
        "tickets": tickets,
    })

@app.route("/api/tickets/setup", methods=["POST"])
@require_auth
def tickets_setup():
    d = get_db()
    data = request.get_json() or {}
    d["ticket_settings"].update_one(
        {"guild_id": GUILD_ID},
        {"$set": data},
        upsert=True
    )
    return jsonify({"success": True})

@app.route("/api/tickets/close", methods=["POST"])
@require_auth
def close_ticket():
    d = get_db()
    data = request.get_json() or {}
    ticket_id = data.get("ticket_id", "")
    if not ticket_id:
        return jsonify({"error": "ticket_id required"}), 400
    try:
        result = d["tickets"].update_one(
            {"id": ticket_id},
            {"$set": {"status": "closed", "closed_at": datetime.now(timezone.utc).isoformat()}}
        )
        if result.matched_count:
            return jsonify({"success": True})
        return jsonify({"error": "Ticket not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Mod Log ─────────────────────────────────────────────────────

@app.route("/api/mod/log")
@require_auth
def get_mod_log():
    d = get_db()
    limit = min(int(request.args.get("limit", 20)), 100)
    log = list(d["mod_log"].find({}, {"_id": 0}).sort("timestamp", -1).limit(limit))
    return jsonify({"log": log})

@app.route("/api/mod/<action>", methods=["POST"])
@require_auth
def mod_action(action):
    if action not in ("warn", "mute", "kick", "ban", "timeout"):
        return jsonify({"error": "Invalid action"}), 400
    d = get_db()
    data = request.get_json() or {}
    entry = {
        "user_id": data.get("user_id", ""),
        "username": data.get("username", "Unknown"),
        "action": action,
        "moderator": "Dashboard",
        "reason": data.get("reason", "No reason"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    d["mod_log"].insert_one(entry)
    # cap at 200
    total = d["mod_log"].count_documents({})
    if total > 200:
        oldest = d["mod_log"].find().sort("timestamp", 1).limit(total - 200)
        ids = [o["_id"] for o in oldest]
        d["mod_log"].delete_many({"_id": {"$in": ids}})
    if action == "ban":
        d["bans"].insert_one({
            "discord_id": data.get("user_id", ""),
            "username": data.get("username", "Unknown"),
            "type": "discord",
            "guild_id": GUILD_ID,
            "banned_at": datetime.now(timezone.utc).isoformat(),
        })
    return jsonify({"success": True})

# ── Discord-bot-required endpoints (placeholder) ────────────────

@app.route("/api/send-message", methods=["POST"])
@require_auth
def send_message():
    return jsonify({"error": "Discord bot not connected to this API server"}), 503

@app.route("/api/send-embed", methods=["POST"])
@require_auth
def send_embed():
    return jsonify({"error": "Discord bot not connected to this API server"}), 503

@app.route("/api/delete-messages", methods=["POST"])
@require_auth
def delete_messages():
    return jsonify({"error": "Discord bot not connected to this API server"}), 503

@app.route("/api/roles/add", methods=["POST"])
@require_auth
def roles_add():
    return jsonify({"error": "Discord bot not connected to this API server"}), 503

@app.route("/api/roles/remove", methods=["POST"])
@require_auth
def roles_remove():
    return jsonify({"error": "Discord bot not connected to this API server"}), 503

# ── Bot roles (stored in MongoDB) ───────────────────────────────

@app.route("/api/roles")
@require_auth
def get_roles():
    d = get_db()
    roles = list(d["bot_roles"].find({"guild_id": GUILD_ID}).sort("name", 1))
    result = []
    for r in roles:
        result.append({
            "id": str(r["_id"]),
            "role_id": r.get("role_id"),
            "name": r.get("name"),
            "description": r.get("description", ""),
            "category": r.get("category", "general"),
        })
    return jsonify({"roles": result, "count": len(result)})

@app.route("/api/roles", methods=["POST"])
@require_auth
def add_role():
    d = get_db()
    data = request.get_json() or {}
    role_id = data.get("role_id")
    name = data.get("name", "").strip()
    if not role_id or not name:
        return jsonify({"error": "role_id and name required"}), 400
    doc = {
        "guild_id": GUILD_ID,
        "role_id": int(role_id),
        "name": name,
        "description": data.get("description", ""),
        "category": data.get("category", "general"),
    }
    d["bot_roles"].update_one(
        {"guild_id": GUILD_ID, "role_id": int(role_id)},
        {"$set": doc},
        upsert=True
    )
    return jsonify({"success": True})

@app.route("/api/roles/<role_id>", methods=["DELETE"])
@require_auth
def delete_role(role_id):
    d = get_db()
    d["bot_roles"].delete_one({"_id": ObjectId(role_id), "guild_id": GUILD_ID})
    return jsonify({"success": True})

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Server error"}), 500
