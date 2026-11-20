import os
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

client = AsyncIOMotorClient(os.getenv("MONGO_URI"))
db = client["videovault_db"]

users_col = db["users"]
videos_col = db["videos"]
unlocks_col = db["unlocks"]
trending_col = db["trending_logs"]
bot_msgs_col = db["bot_messages"]
settings_col = db["settings"]

# New Lightweight Collections
analytics_col = db["ad_analytics"]
requests_col = db["video_requests"]
support_col = db["support_tickets"]

DEFAULT_SETTINGS = {
    "_id": "global",
    "app_name": "VideoVault",
    "welcome_notice": "✨ প্রিমিয়াম ভিডিও লাইব্রেরিতে আপনাকে স্বাগতম!",
    "monetag_zone_id": "",
    "direct_links": [],
    "ad_timer": 15,
    "unlock_expiry_hours": 24,
    "vip_coin_price": 50,
    "unlock_all_coin_price": 30,
    "daily_coin_reward": 10,
    "referral_coin_reward": 20,
    "auto_delete_hours": 6,
    "force_channels": [],
    "notification_channel_id": "",
    "watch_btn_text": "🟢 Watch Now",
    "tutorial_btn_text": "🟡 কিভাবে ভিডিও দেখবেন?",
    "tutorial_btn_url": "",
    "custom_btn_text": "🔵 আমাদের চ্যানেলে জয়েন করুন",
    "custom_btn_url": "",
    "main_channel_name": "🔵 মেইন চ্যানেল",
    "main_channel_url": "",
    "request_channel_name": "🟡 রিকোয়েস্ট চ্যানেল",
    "request_channel_url": "",
    # New Feature Settings
    "maintenance_mode": False,
    "maintenance_msg": "🔧 সার্ভার ও নতুন ফিচার আপডেট চলছে! অনুগ্রহ করে কিছুক্ষণ পর আবার চেষ্টা করুন।",
    "share_unlock_enabled": True,
    "backup_bot_token": "",
    "wakeup_days": 3,
    "contest_prize": "👑 সেরা ৩ জন পাবে ১ মাসের ফ্রি VIP!",
    "contest_end_date": "",
    "tasks": []  # [{"id": "t1", "title": "...", "url": "...", "reward": 15}]
}

async def init_db():
    # Existing & New Indexes for Ultra-Fast Queries
    await users_col.create_index("user_id", unique=True)
    await users_col.create_index([("weekly_refs", -1)])
    await users_col.create_index("last_active")
    await users_col.create_index("is_blocked")

    await videos_col.create_index([("created_at", -1)])
    await videos_col.create_index([("title", "text")])

    await unlocks_col.create_index([("user_id", 1), ("video_id", 1), ("part_num", 1)])
    await unlocks_col.create_index("expires_at", expireAfterSeconds=0)

    await trending_col.create_index("created_at", expireAfterSeconds=86400)
    await bot_msgs_col.create_index("delete_at")

    await analytics_col.create_index("date", unique=True)
    await requests_col.create_index([("created_at", -1)])
    await support_col.create_index("admin_msg_id")
    # Auto-clean support tickets after 7 days (604800 seconds)
    await support_col.create_index("created_at", expireAfterSeconds=604800)

    if not await settings_col.find_one({"_id": "global"}):
        await settings_col.insert_one(DEFAULT_SETTINGS.copy())

async def get_settings():
    doc = await settings_col.find_one({"_id": "global"})
    merged = DEFAULT_SETTINGS.copy()
    if doc:
        merged.update(doc)
    return merged
