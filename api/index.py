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

@app.route("/api/stats")
@require_auth
def stats():
    d = get_db()
    vs = d["verify_stats"].find_one({"guild_id": GUILD_ID}) or {}
    members_count = d["members"].count_documents({"guild_id": GUILD_ID})
    bans_count = d["bans"].count_documents({"guild_id": GUILD_ID})
    pending_queue = d["verification_queue"].count_documents({})
    return jsonify({
        "requested": vs.get("requested", 0),
        "approved": vs.get("approved", 0),
        "rejected": vs.get("rejected", 0),
        "verified_members": members_count,
        "bans": bans_count,
        "pending_queue": pending_queue,
    })

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

@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Server error"}), 500

